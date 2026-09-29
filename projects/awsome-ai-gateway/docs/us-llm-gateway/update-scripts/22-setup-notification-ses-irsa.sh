#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 22-setup-notification-ses-irsa.sh — notification-worker용 SES IRSA 역할 생성/갱신
#
# WHAT:  AWS IAM 역할(trust policy + ses:SendEmail/RawEmail)을 만들고,
#        values-eks-fargate-<env>.yaml 의
#        notificationWorker.serviceAccount.annotations 에
#        eks.amazonaws.com/role-arn 을 yq 로 영구화한다.
#        선택적으로 notificationWorker.env.NOTIFICATION_LOCALE 도 설정한다.
# WHY:   SES 사용 시 Pod 권한 최소화. 수동 IAM 콘솔 작업 대체.
#        values 는 구조화 도구(yq)로만 읽고 쓴다 — grep/sed/regex 금지.
#        배포 자체는 install-eks.sh 에 위임한다(bare helm upgrade 는
#        terraform-output --set 주입을 잃어 placeholder 로 역행한다).
# HOW:   dry-run 기본, --apply 로 IAM/values 변경.
#
# USAGE: cd docs/us-llm-gateway/update-scripts
#        bash 22-setup-notification-ses-irsa.sh
#        bash 22-setup-notification-ses-irsa.sh --apply [--locale ko|en]
#
# NEXT:  bash 21-set-notification-provider.sh ses --apply
#        → install-eks.sh <env> → status.sh
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

APPLY=0
LOCALE_ARG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)   APPLY=1; shift ;;
        --locale)  LOCALE_ARG="$2"; shift 2 ;;
        -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

require_env
command -v yq >/dev/null 2>&1 || die "yq not found — install: sudo wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 && sudo chmod +x /usr/local/bin/yq"

# ── Discovery ────────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-llm-gateway-${DEPLOY_ENV}}"
OIDC_ISSUER=$(aws eks describe-cluster --name "$CLUSTER_NAME" \
    --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null) \
    || die "Cannot describe EKS cluster $CLUSTER_NAME. Check AWS_REGION/CLUSTER_NAME."
[ -n "$OIDC_ISSUER" ] && [ "$OIDC_ISSUER" != "None" ] || die "Cluster $CLUSTER_NAME has no OIDC issuer."

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

# ── Default notification locale ──────────────────────────────────────────────
# values 에 이미 설정된 locale 이 있으면 그걸 기본값으로 존중한다 (yq 구조화 읽기).
EXISTING_LOCALE=$(yq -r '.notificationWorker.env.NOTIFICATION_LOCALE // empty' "$VALUES" 2>/dev/null)
DEFAULT_LOCALE="${LOCALE_ARG:-${EXISTING_LOCALE:-ko}}"
if [ "$APPLY" -ne 1 ] && [ -z "$LOCALE_ARG" ]; then
    read -rp "Default notification locale (ko/en) [$DEFAULT_LOCALE]: " input
    NOTIFICATION_LOCALE="${input:-$DEFAULT_LOCALE}"
else
    NOTIFICATION_LOCALE="$DEFAULT_LOCALE"
fi
if [[ "$NOTIFICATION_LOCALE" != "ko" && "$NOTIFICATION_LOCALE" != "en" ]]; then
    warn "Unknown locale '$NOTIFICATION_LOCALE'; defaulting to ko"
    NOTIFICATION_LOCALE="ko"
fi
ok "Notification locale  : $NOTIFICATION_LOCALE"

# ── IAM trust policy ─────────────────────────────────────────────────────────
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

# ── SES permission policy ────────────────────────────────────────────────────
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

# ── Dry-run: show what would be created ──────────────────────────────────────
if [ "$APPLY" -ne 1 ]; then
    hdr "DRY-RUN: the following would be created"
    note "IAM trust policy:"
    echo "$TRUST_POLICY" | sed 's/^/    /'
    note "IAM permission policy:"
    echo "$PERMISSION_POLICY" | sed 's/^/    /'
    note "Values file would get (yq):"
    cat <<EOF
    notificationWorker.serviceAccount.annotations["eks.amazonaws.com/role-arn"] = "$ROLE_ARN"
    notificationWorker.env.NOTIFICATION_LOCALE = "$NOTIFICATION_LOCALE"
EOF
    note "Run with --apply to create the role and update values."
    exit 0
fi

# ── Create / update IAM role ─────────────────────────────────────────────────
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

# ── Update values-eks-fargate-<env>.yaml (yq — 구조화 편집) ──────────────────
bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-22-ses-irsa.bak"
cp "$VALUES" "$bak" || die "backup failed"
note "Values backup: $bak"

ROLE="$ROLE_ARN" yq -i \
    '.notificationWorker.serviceAccount.annotations["eks.amazonaws.com/role-arn"] = strenv(ROLE)' \
    "$VALUES"
LOC="$NOTIFICATION_LOCALE" yq -i \
    '.notificationWorker.env.NOTIFICATION_LOCALE = strenv(LOC)' \
    "$VALUES"

ok "Updated $VALUES"
yq '.notificationWorker.serviceAccount.annotations, .notificationWorker.env.NOTIFICATION_LOCALE' "$VALUES"

cat <<EOF

다음으로 배포에 반영:
    ./deployment/scripts/install-eks.sh $DEPLOY_ENV

그 다음 provider 전환:
    bash 21-set-notification-provider.sh ses --apply --region $AWS_REGION

확인:
    kubectl -n $K8S_NAMESPACE get sa $SA_NAME -o yaml
    kubectl -n $K8S_NAMESPACE exec deploy/${HELM_RELEASE}-notification-worker -- env | grep AWS_
EOF
