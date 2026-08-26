#!/usr/bin/env bash
# 08-setup-notification-ses-irsa.sh — notification-worker용 SES IRSA 역할 생성/갱신
#
# WHAT:  AWS IAM 역할(trust policy + ses:SendEmail/RawEmail)을 만들고,
#        values-eks-fargate-<env>.yaml 의 notificationWorker.serviceAccount.annotations
#        에 eks.amazonaws.com/role-arn 을 영구화한다.
# WHY:   SES 사용 시 Pod 권한 최소화. 수동 IAM 콘솔 작업 대체.
# HOW:   dry-run 기본, --apply 로 IAM/values 변경.
#
# USAGE: cd docs/us-llm-gateway/update-scripts
#        bash 08-setup-notification-ses-irsa.sh
#        bash 08-setup-notification-ses-irsa.sh --apply

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

APPLY=0
if [ "${1:-}" = "--apply" ]; then
    APPLY=1
fi

require_env

# ── Discovery ─────────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-llm-gateway-${DEPLOY_ENV}}"
OIDC_ISSUER=$(aws eks describe-cluster --name "$CLUSTER_NAME" \
    --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null) \
    || die "Cannot describe EKS cluster $CLUSTER_NAME. Check AWS_REGION/CLUSTER_NAME."
[ -n "$OIDC_ISSUER" ] && [ "$OIDC_ISSUER" != "None" ] || die "Cluster $CLUSTER_NAME has no OIDC issuer."

OIDC_PROVIDER_ID="${OIDC_ISSUER##*/id/}"
OIDC_PROVIDER_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_ISSUER#https://}"
ROLE_NAME="llm-gateway-${DEPLOY_ENV}-notification-worker-ses"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"
SA_NAME="notification-worker"

# SES identity resource. Default wildcard; narrow later via config.env if desired.
: "${SES_IDENTITY_RESOURCE:=arn:aws:ses:${AWS_REGION}:${AWS_ACCOUNT_ID}:identity/*}"

REPO_ROOT=$(cd "$LIB_DIR/../../.." && pwd)
VALUES="$REPO_ROOT/deployment/charts/llm-gateway/values-eks-fargate-${DEPLOY_ENV}.yaml"
[ -f "$VALUES" ] || die "Values file not found: $VALUES"

hdr "SES IRSA for notification-worker"
ok "Cluster OIDC provider: $OIDC_PROVIDER_ARN"
ok "Service account      : $K8S_NAMESPACE/$SA_NAME"
ok "IAM role             : $ROLE_ARN"
ok "SES identity resource: $SES_IDENTITY_RESOURCE"
ok "Values file          : $VALUES"

# ── Default notification locale ───────────────────────────────────────────────
# values 파일에 이미 설정된 locale 이 있으면 그걸 기본값으로 존중한다.
EXISTING_LOCALE=$(grep -E '^[[:space:]]*NOTIFICATION_LOCALE:' "$VALUES" 2>/dev/null | sed -E 's/.*: *"?([^"]*)"?/\1/' | head -1)
DEFAULT_LOCALE="${EXISTING_LOCALE:-ko}"
read -rp "Default notification locale (ko/en) [$DEFAULT_LOCALE]: " input
NOTIFICATION_LOCALE="${input:-$DEFAULT_LOCALE}"
if [[ "$NOTIFICATION_LOCALE" != "ko" && "$NOTIFICATION_LOCALE" != "en" ]]; then
    warn "Unknown locale '$NOTIFICATION_LOCALE'; defaulting to ko"
    NOTIFICATION_LOCALE="ko"
fi
ok "Notification locale  : $NOTIFICATION_LOCALE"

# ── IAM trust policy ──────────────────────────────────────────────────────────
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "$OIDC_PROVIDER_ARN"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_ISSUER#https://}:sub": "system:serviceaccount:$K8S_NAMESPACE:$SA_NAME",
          "${OIDC_ISSUER#https://}:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
EOF
)

# ── SES permission policy ─────────────────────────────────────────────────────
PERMISSION_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail",
        "ses:SendRawEmail"
      ],
      "Resource": "$SES_IDENTITY_RESOURCE"
    }
  ]
}
EOF
)

# ── Dry-run: show what would be created ───────────────────────────────────────
if [ "$APPLY" -ne 1 ]; then
    hdr "DRY-RUN: the following would be created"
    note "IAM trust policy:"
    echo "$TRUST_POLICY" | sed 's/^/    /'
    note "IAM permission policy:"
    echo "$PERMISSION_POLICY" | sed 's/^/    /'
    note "Values file would get:"
    cat <<EOF
  notificationWorker:
    serviceAccount:
      annotations:
        eks.amazonaws.com/role-arn: "$ROLE_ARN"
    env:
      NOTIFICATION_LOCALE: "$NOTIFICATION_LOCALE"
EOF
    note "Run with --apply to create the role and update values."
    exit 0
fi

# ── Create / update IAM role ──────────────────────────────────────────────────
ROLE_EXISTS=0
aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1 && ROLE_EXISTS=1 || true

if [ "$ROLE_EXISTS" -eq 1 ]; then
    warn "Role $ROLE_NAME already exists. Updating trust policy and inline policy."
    aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST_POLICY"
else
    note "Creating IAM role $ROLE_NAME ..."
    aws iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --description "IRSA for llm-gateway notification-worker to send SES emails"
fi

# Put inline policy (idempotent)
aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name "SESSendEmail" \
    --policy-document "$PERMISSION_POLICY"

ok "IAM role $ROLE_NAME ready."

# ── Update values-eks-fargate-<env>.yaml ──────────────────────────────────────
BACKUP="$VALUES.bak.$(date +%s)"
cp "$VALUES" "$BACKUP"
note "Values backup: $BACKUP"

python3 - "$VALUES" "$ROLE_ARN" <<'PY'
import re, sys

v, role_arn = sys.argv[1:3]
with open(v, encoding="utf-8") as f:
    text = f.read()

# notificationWorker: 아래 serviceAccount: annotations: ... 블록을 대체
# 기존이 {} 이든 비어있든, 채워져 있든 통째로 덮어쓴다(단일 어노테이션만 관리).
pattern = r'^(notificationWorker:\n(?:^  .*?\n)*?)(^  serviceAccount:\n)(    annotations:\s*[^\n]*\n)'
replacement = (
    r'\1\2    annotations:\n'
    r'      eks.amazonaws.com/role-arn: "' + role_arn + r'"\n'
)
new_text, n = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)

if n == 0:
    # serviceAccount 블록이 없으면 notificationWorker: 블록 끝에 추가
    m = re.search(r'^(notificationWorker:\n(?:^  .*\n)*)', text, flags=re.MULTILINE | re.DOTALL)
    if m is None:
        raise SystemExit("notificationWorker block not found in values file")
    block = m.group(1)
    new_block = block + '  serviceAccount:\n' + f'    annotations:\n      eks.amazonaws.com/role-arn: "{role_arn}"\n'
    new_text = text.replace(block, new_block, 1)

with open(v, "w", encoding="utf-8") as f:
    f.write(new_text)
PY

python3 - "$VALUES" "$NOTIFICATION_LOCALE" <<'PY'
import re, sys

v, locale = sys.argv[1:3]
with open(v, encoding="utf-8") as f:
    text = f.read()

pattern = r'^(notificationWorker:\n(?:^  .*\n)*)'

def repl(m):
    block = m.group(1)
    env_m = re.search(r'^(  env:\n(?:    .+\n)*)', block, flags=re.MULTILINE | re.DOTALL)
    if env_m:
        env_block = env_m.group(1)
        key_re = re.compile(r'^    NOTIFICATION_LOCALE:.*$', flags=re.MULTILINE)
        if key_re.search(env_block):
            new_env = key_re.sub(f'    NOTIFICATION_LOCALE: "{locale}"', env_block)
        else:
            new_env = env_block.rstrip('\n') + f'\n    NOTIFICATION_LOCALE: "{locale}"\n'
        return block[:env_m.start()] + new_env + block[env_m.end():]
    else:
        return block.rstrip('\n') + f'\n  env:\n    NOTIFICATION_LOCALE: "{locale}"\n'

new_text = re.sub(pattern, repl, text, count=1, flags=re.MULTILINE | re.DOTALL)

with open(v, "w", encoding="utf-8") as f:
    f.write(new_text)
PY

ok "Updated $VALUES"

cat <<EOF

다음으로 배포에 반영:
    ./deployment/scripts/install-eks.sh $DEPLOY_ENV

확인:
    kubectl -n $K8S_NAMESPACE get sa $SA_NAME -o yaml
    kubectl -n $K8S_NAMESPACE exec deploy/${HELM_RELEASE}-notification-worker -- env | grep AWS_
EOF
