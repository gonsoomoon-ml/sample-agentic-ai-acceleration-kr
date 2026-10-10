#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 22-setup-notification-ses-irsa.sh — notification-worker용 SES IRSA 역할 생성/갱신
#
# WHAT:  AWS IAM 역할(trust policy + ses:SendEmail/RawEmail)을 만들고,
#        values-eks-fargate-<env>.yaml 의
#        notificationWorker.serviceAccount.annotations 에
#        eks.amazonaws.com/role-arn 을 영구화한다.
#        선택적으로 notificationWorker.env.NOTIFICATION_LOCALE 도 설정한다.
# WHY:   SES 사용 시 Pod 권한 최소화. 수동 IAM 콘솔 작업 대체.
#        values 읽기는 chart 렌더(helm template) 결과 — 부모 values 상속분
#        포함. 쓰기는 해당 줄만 바꾸는 스코프 편집 — 배포 EC2 원본의 서식이
#        지워지지 않는다(15·17·19·21 과 같은 방식).
#        배포 자체는 install-eks.sh 에 위임한다(bare helm upgrade 는
#        terraform-output --set 주입을 잃어 placeholder 로 역행한다).
# HOW:   dry-run 기본, --apply 로 IAM/values 변경.
#        SES 리전: 권한 policy 의 Resource ARN 과 파드의 AWS_SES_REGION 은
#        같은 SES identity 리전을 가리켜야 AccessDenied 가 나지 않는다.
#        identity ARN 의 리전을 values 의 email.ses.region 에도 함께 적어
#        (비워 두면 chart 가 aws.region 으로 채운다) 두 값이 어긋나지 않게 한다.
#
# USAGE: cd docs/us-llm-gateway/update-scripts
#        bash 22-setup-notification-ses-irsa.sh
#        bash 22-setup-notification-ses-irsa.sh --apply [--locale ko|en]
#
# NEXT:  bash 21-set-notification-provider.sh ses --apply
#        → install-eks.sh <env> → status.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

APPLY=0
LOCALE_ARG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)   APPLY=1; shift ;;
        --locale)  LOCALE_ARG="${2:?--locale requires a value}"; shift 2 ;;
        -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

require_env
command -v helm >/dev/null 2>&1 || die "helm not found. Run this on the deployer EC2."
command -v python3 >/dev/null 2>&1 || die "python3 not found — values 편집기와 렌더 검증에 필요하다"
python3 -c 'import yaml' 2>/dev/null || die "python3-yaml(PyYAML) not found — values 편집기와 렌더 검증에 필요하다"

# ── Discovery ────────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-llm-gateway-${DEPLOY_ENV}}"
OIDC_ISSUER=$(aws eks describe-cluster --name "$CLUSTER_NAME" \
    --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null) \
    || die "Cannot describe EKS cluster $CLUSTER_NAME. Check AWS_REGION/CLUSTER_NAME."
if [ -z "$OIDC_ISSUER" ] || [ "$OIDC_ISSUER" = "None" ]; then
    die "Cluster $CLUSTER_NAME has no OIDC issuer."
fi

# 파티션 — caller identity ARN 의 두 번째 필드(aws / aws-us-gov / aws-cn).
# arn:aws: 로 하드코딩하면 GovCloud 에서 IAM·SES ARN 이 전부 틀어진다.
AWS_PARTITION=$(printf '%s' "$(aws sts get-caller-identity --query Arn --output text 2>/dev/null || echo arn:aws:sts)" | cut -s -d: -f2)
AWS_PARTITION=${AWS_PARTITION:-aws}
OIDC_PROVIDER_ARN="arn:${AWS_PARTITION}:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_ISSUER#https://}"
ROLE_NAME="llm-gateway-${DEPLOY_ENV}-notification-worker-ses"
ROLE_ARN="arn:${AWS_PARTITION}:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"

REPO_ROOT=$(cd "$LIB_DIR/../../.." && pwd)
CHART="$REPO_ROOT/deployment/charts/llm-gateway"
VALUES="${HELM_VALUES_FILE:-$CHART/values-eks-fargate-${DEPLOY_ENV}.yaml}"
[ -f "$VALUES" ] || die "Values file not found: $VALUES"
VALUES=$(readlink -f "$VALUES")

# ── chart 렌더에서 SA 이름·worker env 읽기 (부모 values 상속분 포함) ──────────
# env values 파일만 읽으면 차트 기본 values.yaml 에 있는 ses.region·
# serviceAccount.name 을 놓친다 — 실제 배포값은 helm 렌더 결과에서 가져온다.
render_chart() {
    local out
    out=$(helm template "$HELM_RELEASE" "$CHART" -f "$1" 2>&1) \
        || { printf 'helm template failed for %s:\n%s\n' "$1" "$(tail -5 <<<"$out")" >&2; return 1; }
    printf '%s' "$out"
}

worker_env() {
    local rendered
    rendered=$(render_chart "$1") || return 1
    python3 -c '
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-notification-worker"):
        # containers[0] 고정 인덱스는 사이드카가 붙는 순간 조용히 다른 컨테이너를
        # 읽는다 — 이름으로 고른다(status.sh 의 kubectl jsonpath 와 같은 규칙).
        for c in d["spec"]["template"]["spec"]["containers"]:
            if c.get("name") == "notification-worker":
                for e in c.get("env") or []:
                    print(e["name"] + "\t" + str(e.get("value", "<secretRef>")))
        break
' <<<"$rendered"
}

rendered_sa_name() {
    # Deployment 의 serviceAccountName — SA 생성 여부(serviceAccount.create)와
    # 무관하게 파드가 실제 쓰는 이름이고, name override·include 로직도 반영된다.
    local rendered
    rendered=$(render_chart "$1") || return 1
    python3 -c '
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-notification-worker"):
        print(d["spec"]["template"]["spec"].get("serviceAccountName") or "")
        break
' <<<"$rendered"
}

sa_annotations() {
    # $2 = 기대하는 SA 이름 — 커스텀 serviceAccount.name 에서 endswith 매칭은
    # 다른 SA(예: xxx-notification-worker)를 잡을 수 있어 정확히 비교한다.
    local rendered
    rendered=$(render_chart "$1") || return 1
    python3 -c '
import sys, yaml
want = sys.argv[1]
for d in yaml.safe_load_all(sys.stdin):
    if d and d.get("kind") == "ServiceAccount" and d["metadata"]["name"] == want:
        for k, v in (d["metadata"].get("annotations") or {}).items():
            print(k + "\t" + str(v))
        break
' "$2" <<<"$rendered"
}

RENDERED_ENV=$(worker_env "$VALUES") \
    || die "could not render notification-worker env — see the message above"
SA_NAME=$(rendered_sa_name "$VALUES") \
    || die "could not render notification-worker serviceAccountName"
if [ -z "$SA_NAME" ]; then
    # notificationWorker.enabled=false → Deployment 미렌더. values 병합으로
    # 계산한다 — serviceAccount.name 이 기본 values 에 있어서 사실상 여기까진
    # 오지 않지만, 렌더 없이도 죽지 않게 둔다.
    SA_NAME=$(python3 -c '
import sys, yaml
env  = yaml.safe_load(open(sys.argv[1])) or {}
base = yaml.safe_load(open(sys.argv[2])) or {}
def get(d, *p):
    for k in p:
        d = (d or {}).get(k) or {}
    return d or ""
print(get(env,"notificationWorker","serviceAccount","name")
      or get(base,"notificationWorker","serviceAccount","name")
      or "notification-worker")
' "$VALUES" "$CHART/values.yaml")
fi
[ -n "$SA_NAME" ] || die "could not determine serviceAccountName"

# ── SES 리전 해석 ────────────────────────────────────────────────────────────
# identity ARN 리전 = 권한 정책 Resource 리전 = 파드의 AWS_SES_REGION 셋이 같아야
# 발송이 통과한다. values 의 email.ses.region(21 이 쓰는 값)을 읽되, env values 와
# 차트 기본 values.yaml 을 helm 과 같은 방향(env 우선)으로 병합해 읽는다 — env
# 파일만 보면 기본 values 의 값을 놓치고, 렌더의 AWS_SES_REGION 은 provider=="ses"
# 일 때만 나오는데 이 스크립트는 provider 전환(21)보다 먼저 돌아야 하므로 렌더에
# 의지할 수 없다. ⚠️ values 의 aws.region 은 폴백으로 쓰지 않는다 — install-eks 는
# --set aws.region=$AWS_REGION 으로 덮어쓰므로 파일 속 값은 커밋된 기본값
# (ap-northeast-2)일 수 있고 배포 리전과 다르다. 비어 있으면
# SES_IDENTITY_RESOURCE(config.env)의 ARN 리전, 그것도 없으면 배포 리전
# (AWS_REGION)을 쓴다.
VALUES_SES_REGION=$(python3 -c '
import sys, yaml
env  = yaml.safe_load(open(sys.argv[1])) or {}
base = yaml.safe_load(open(sys.argv[2])) or {}
def get(d, *path):
    for k in path:
        d = (d or {}).get(k) or {}
    return d or ""
print(get(env, "notificationWorker", "email", "ses", "region")
      or get(base, "notificationWorker", "email", "ses", "region"))
' "$VALUES" "$CHART/values.yaml")
if [ -n "$VALUES_SES_REGION" ]; then
    SES_IDENTITY_REGION="$VALUES_SES_REGION"
elif [ -n "${SES_IDENTITY_RESOURCE:-}" ]; then
    SES_IDENTITY_REGION=$(python3 -c 'import re,sys
m = re.match(r"arn:[a-z0-9*-]+:ses:([a-z0-9-]+):", sys.argv[1])
print(m.group(1) if m else "")' "$SES_IDENTITY_RESOURCE")
    [ -n "$SES_IDENTITY_REGION" ] \
        || die "SES_IDENTITY_RESOURCE '$SES_IDENTITY_RESOURCE' has no region in the ARN"
else
    SES_IDENTITY_REGION="$AWS_REGION"
fi
if [ -n "${SES_IDENTITY_RESOURCE:-}" ]; then
    ARN_REGION=$(python3 -c 'import re,sys
m = re.match(r"arn:[a-z0-9*-]+:ses:([a-z0-9-]+):", sys.argv[1])
print(m.group(1) if m else "")' "$SES_IDENTITY_RESOURCE")
    [ "$ARN_REGION" = "$SES_IDENTITY_REGION" ] \
        || die "region mismatch: values의 SES 리전(email.ses.region)은 '$SES_IDENTITY_REGION'인데 SES_IDENTITY_RESOURCE 는 '$ARN_REGION'.
     Fix one — values (21-set-notification-provider.sh --region) or config.env's SES_IDENTITY_RESOURCE."
else
    SES_IDENTITY_RESOURCE="arn:${AWS_PARTITION}:ses:${SES_IDENTITY_REGION}:${AWS_ACCOUNT_ID}:identity/*"
fi

hdr "SES IRSA for notification-worker"
ok "Cluster OIDC provider: $OIDC_PROVIDER_ARN"
ok "Service account      : $K8S_NAMESPACE/$SA_NAME"
ok "IAM role             : $ROLE_ARN"
ok "SES identity resource: $SES_IDENTITY_RESOURCE (region $SES_IDENTITY_REGION)"
ok "Values file          : $VALUES"

# ── Default notification locale ──────────────────────────────────────────────
# 렌더에 NOTIFICATION_LOCALE 이 보이면 그걸 존중한다. 렌더 자체가 실패하면
# 멈춘다 — 읽기 실패를 키 없음으로 착각해 기본값으로 덮어쓰는 사고를 막는다.
EXISTING_LOCALE=$(awk -F'\t' '$1=="NOTIFICATION_LOCALE"{print $2}' <<<"$RENDERED_ENV" || true)
DEFAULT_LOCALE="${LOCALE_ARG:-${EXISTING_LOCALE:-ko}}"
# 대화형이면 묻는다 — dry-run 에만 묻고 --apply 에서 무시하면 답한 값이 어디에도
# 쓰이지 않는 함정이 된다. 비대화형(CI·파이프)은 --locale 또는 렌더값/기본값.
if [ -z "$LOCALE_ARG" ] && [ -t 0 ]; then
    read -rp "Default notification locale (ko/en) [$DEFAULT_LOCALE]: " input || input=""
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

# ── SES permission policy — Resource 리전 = identity 리전 (파드 region 과 동일해야) ──
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

# ── Scoped values edit (line-based — 서식·주석 보존, 21 과 같은 편집기) ───────
edit_values() {
  python3 - "$@" <<'PY'
import json, re, sys
src, dst, *ops_args = sys.argv[1:]
L = open(src, encoding="utf-8").read().split("\n")

def ind(l): return len(l) - len(l.lstrip(" "))
def code(l): return l.strip() != "" and not l.lstrip().startswith("#")

def split_comment(s):
    # 인라인 주석 위치 — 따옴표 안의 '#' 는 주석이 아니다 (예: key: "a # b").
    # YAML 주석은 공백 뒤 '#' 라서 앞 문자가 공백/탭일 때만 인정한다.
    # 따옴표 스칼라는 값의 첫 비공백 문자가 따옴표일 때만 성립 — 평문 중간의
    # 아포스트로피(Gateway's)는 리터럴이라 "따옴표가 열렸다"로 오인하면 안 된다.
    q = None
    seen = False  # 값 부분에서 비공백 문자를 봤는가
    i = 0
    while i < len(s):
        c = s[i]
        if q:
            if q == '"' and c == "\\":
                i += 2  # \" 는 닫는 따옴표가 아니다(단, '' 안의 \ 는 리터럴)
                continue
            if c == q:
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2  # '' 는 단일 따옴표 안의 이스케이프
                    continue
                q = None
        elif c in " \t":
            pass
        elif not seen and c in "\"'":
            q = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i], s[i:]
        if c not in " \t":
            seen = True
        i += 1
    return s, ""

def child_range(lo, parent_ind):
    # (child_indent, insert_pos, end) — insert_pos 는 마지막 자식 코드 라인
    # 직후라, 다음 섹션을 앞두고 쌓인 주석·빈 줄 사이로 새 키가 새지 않는다.
    child = None; last = lo; i = lo
    while i < len(L):
        if code(L[i]):
            d = ind(L[i])
            if d <= parent_ind:
                break
            if child is None:
                child = d
            last = i + 1
        i += 1
    return child, last, i

def find_leaf(lo, hi, ind_lvl, key):
    hits = [i for i in range(lo, hi)
            if code(L[i]) and ind(L[i]) == ind_lvl and re.match(rf"^\s*{re.escape(key)}\s*:", L[i])]
    if len(hits) > 1:
        sys.exit(f"values: '{key}' appears {len(hits)} times in the same scope — fix the file first")
    return hits[0] if hits else None

def ensure_parent(path, create):
    lo, parent_ind = 0, -1
    for depth, seg in enumerate(path[:-1]):
        ci, ins, hi = child_range(lo, parent_ind)
        i = find_leaf(lo, hi, ci if ci is not None else parent_ind + 2, seg)
        if i is None:
            if not create:
                sys.exit(f"values: '{seg}' not found ({'.'.join(path[:depth + 1])}) — is this a {path[0]} values file?")
            new_ind = ci if ci is not None else parent_ind + 2
            L[ins:ins] = [" " * new_ind + f"{seg}:"]
            parent_ind, lo = new_ind, ins + 1
            continue
        m = re.match(rf"^(\s*){re.escape(seg)}\s*:\s*(.*)$", L[i])
        rest = split_comment(m.group(2) or "")[0].strip()
        if rest not in ("{}", ""):
            sys.exit(f"values: '{seg}' is a scalar ({rest!r}) — cannot nest under it")
        if rest == "{}":
            L[i] = f"{m.group(1)}{seg}:"
        parent_ind, lo = ind(L[i]), i + 1
    ci, ins, hi = child_range(lo, parent_ind)
    return lo, ins, hi, ci if ci is not None else parent_ind + 2

def render_value(typ, raw):
    if typ == "str":  return json.dumps(raw)
    if typ == "int":
        try: return str(int(raw))
        except ValueError: sys.exit(f"edit_values: int expected, got '{raw}'")
    if typ == "bool":
        if raw in ("true", "false"): return raw
        sys.exit(f"edit_values: bool expected, got '{raw}'")
    if typ == "raw":  return raw
    sys.exit(f"edit_values: unknown type '{typ}'")

def leaf_is_multiline(i):
    # 다음 코드 라인이 이 키보다 깊게 들어가 있으면 블록 스칼라(|, >)나
    # 다중행 값 — 첫 줄만 고치면 나머지가 고아가 되므로 수동 편집을 요구한다.
    j = i + 1
    while j < len(L) and not code(L[j]):
        j += 1
    return j < len(L) and ind(L[j]) > ind(L[i])

for arg in ops_args:
    parts = arg.split("\x1f")
    op = parts[0]
    if op == "del":
        path = parts[1:]
        lo, ins, hi, ci = ensure_parent(path, create=False)
        i = find_leaf(lo, hi, ci, path[-1])
        if i is not None:
            if leaf_is_multiline(i):
                sys.exit(f"values: '{path[-1]}' is a multi-line value — edit {path[0]} by hand")
            del L[i]
        continue
    if op == "set":
        path, typ, val = parts[1:-2], parts[-2], parts[-1]
        if len(path) < 1:
            sys.exit(f"edit_values: malformed op '{arg}'")
        lo, ins, hi, ci = ensure_parent(path, create=True)
        i = find_leaf(lo, hi, ci, path[-1])
        if i is not None and leaf_is_multiline(i):
            sys.exit(f"values: '{path[-1]}' is a multi-line value — edit {path[0]} by hand")
        new = " " * ci + f"{path[-1]}: {render_value(typ, val)}"
        if i is not None:
            # split_comment 는 값 부분만 본다 — 키 문자 자체가 'seen' 을 세우면
            # 따옴표 값을 인식 못해 quoted '#' 를 주석으로 오인한다.
            colon = L[i].find(":")
            body, comment = split_comment(L[i][colon + 1:] if colon >= 0 else L[i])
            gap = body[len(body.rstrip()):]
            L[i] = new + (gap + comment if comment else "")
        else:
            L[ins:ins] = [new]
        continue
    sys.exit(f"edit_values: unknown op '{op}'")

open(dst, "w", encoding="utf-8").write("\n".join(L))
PY
}

US=$'\x1f'
OPS=(
  "set${US}notificationWorker${US}serviceAccount${US}annotations${US}eks.amazonaws.com/role-arn${US}str${US}$ROLE_ARN"
  "set${US}notificationWorker${US}env${US}NOTIFICATION_LOCALE${US}str${US}$NOTIFICATION_LOCALE"
  "set${US}notificationWorker${US}email${US}ses${US}region${US}str${US}$SES_IDENTITY_REGION"
)

# ── Dry-run: show what would be created ──────────────────────────────────────
if [ "$APPLY" -ne 1 ]; then
    hdr "DRY-RUN: the following would be created"
    note "IAM trust policy:"
    echo "$TRUST_POLICY" | sed 's/^/    /'
    note "IAM permission policy:"
    echo "$PERMISSION_POLICY" | sed 's/^/    /'
    tmp=$(mktemp)
    edit_values "$VALUES" "$tmp" "${OPS[@]}" \
        || { rm -f "$tmp"; die "values edit failed — $VALUES untouched"; }
    note "Values file diff:"
    diff -u "$VALUES" "$tmp" | sed -n '3,$p' | sed 's/^/  /' || true
    # dry-run 도 helm 렌더로 검증한다 — 편집 결과가 차트를 깨뜨리는 건
    # apply 를 기다릴 이유가 없다.
    render_chart "$tmp" >/dev/null \
        || { rm -f "$tmp"; die "edited values fail helm render — $VALUES untouched"; }
    rm -f "$tmp"
    note "Run with --apply to create the role and update values."
    exit 0
fi

# ── values 편집 + helm 렌더 검증 — IAM 을 만지기 **전에** 한다 ────────────────
# 렌더 실패가 IAM 변경 뒤에 터지면 role 만 덩그러니 생긴 반쪽 적용이 된다.
tmp=$(mktemp)
edit_values "$VALUES" "$tmp" "${OPS[@]}" \
    || { rm -f "$tmp"; die "values edit failed — $VALUES untouched"; }
if cmp -s "$VALUES" "$tmp"; then
    VALUES_CHANGED=0
    ok "values already carries these — nothing to write"
else
    VALUES_CHANGED=1
    SA_OUT=$(sa_annotations "$tmp" "$SA_NAME") \
        || { rm -f "$tmp"; die "edited values fail helm render — $VALUES untouched"; }
    if ! grep -qF "eks.amazonaws.com/role-arn"$'\t'"$ROLE_ARN" <<<"$SA_OUT"; then
        rm -f "$tmp"
        die "edited values do not render eks.amazonaws.com/role-arn=$ROLE_ARN — $VALUES untouched"
    fi
    ENV2=$(worker_env "$tmp") || { rm -f "$tmp"; die "edited values fail helm render — $VALUES untouched"; }
    if ! grep -qF "NOTIFICATION_LOCALE"$'\t'"$NOTIFICATION_LOCALE" <<<"$ENV2"; then
        rm -f "$tmp"
        die "edited values do not render NOTIFICATION_LOCALE=$NOTIFICATION_LOCALE — $VALUES untouched"
    fi

    hdr "Planned change — $(basename "$VALUES") (helm renders it)"
    diff -u "$VALUES" "$tmp" | sed -n '3,$p' | sed 's/^/  /' || true
fi

# 검증이 모두 끝난 뒤 첫 변경 — 여기서 한 번만 확인한다.
confirm "Creating/updating IAM role $ROLE_NAME + writing $VALUES (backup kept in $SNAP_DIR)."

# ── Create / update IAM role ─────────────────────────────────────────────────
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    warn "Role $ROLE_NAME already exists. Updating trust policy and inline policy."
    aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST_POLICY" \
        || { rm -f "$tmp"; die "update-assume-role-policy failed"; }
else
    note "Creating IAM role $ROLE_NAME ..."
    aws iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --description "IRSA for llm-gateway notification-worker to send SES emails" \
        || { rm -f "$tmp"; die "create-role failed"; }
fi

# Put inline policy (idempotent)
aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name "SESSendEmail" \
    --policy-document "$PERMISSION_POLICY" \
    || { rm -f "$tmp"; die "put-role-policy failed"; }

ok "IAM role $ROLE_NAME ready."

# ── Write values (검증은 위에서 끝남) ─────────────────────────────────────────
if [ "$VALUES_CHANGED" = 1 ]; then
    bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-22-ses-irsa.bak"
    cp "$VALUES" "$bak" || { rm -f "$tmp"; die "backup failed"; }
    cp "$tmp" "$VALUES" || { rm -f "$tmp"; die "could not write $VALUES (backup: $bak)"; }
    rm -f "$tmp"
    ok "Updated $VALUES"
    note "backup: $bak"
else
    rm -f "$tmp"
fi

cat <<EOF

다음으로 배포에 반영:
    ./deployment/scripts/install-eks.sh $DEPLOY_ENV

그 다음 provider 전환:
    bash 21-set-notification-provider.sh ses --apply

확인:
    kubectl -n $K8S_NAMESPACE get sa $SA_NAME -o yaml
    kubectl -n $K8S_NAMESPACE exec deploy/${HELM_RELEASE}-notification-worker -- env | grep AWS_
EOF
