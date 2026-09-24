#!/usr/bin/env bash
# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

# ==============================================================================
# enable-body-logging.sh — 요청/응답 본문 로깅 sink 활성화 (terraform + helm env)
# ------------------------------------------------------------------------------
# 본문 로깅은 잠금이 두 겹이다 — 둘 다 열려야 수집이 시작된다:
#   1) 인프라: terraform module "body_logging" (S3 버킷 + Firehose + IAM) 과
#      gatewayProxy.env 의 FIREHOSE_STREAM_NAME / BODY_LOG_S3_BUCKET.
#      이 스크립트가 여는 쪽이다.
#   2) 런타임: 관리자 /monitoring 의 body logging 토글(public.system_settings).
#      스크립트가 끝나도 수집은 꺼져 있다 — 켜는 것은 운영자의 별도 결정이다.
#
# ⚠️ 수집되면 요청 JSON 전문과 스트리밍 SSE 텍스트 전문이 **마스킹 없이** S3 에
#    durable 저장된다. 사용자 프롬프트가 그대로 나가므로 활성화 전 개인정보/
#    보안 검토가 필요하다. S3 객체는 lifecycle(기본 90일)로 만료된다.
#
# 사용법:
#   ./enable-body-logging.sh <env>             # 상태 + plan. 아무것도 안 바꿈
#   ./enable-body-logging.sh <env> --apply     # tfvars 반영 → apply → helm env 주입 → rollout
#   ./enable-body-logging.sh <env> --verify    # 읽기 전용. 버킷/스트림/env 확인
#   ./enable-body-logging.sh <env> --disable   # env 비우기 + helm upgrade (수집 중지.
#                                              # terraform 리소스는 유지 — 버킷 삭제는 수동)
#     env: dev | prod
#   옵션: --values <path>   helm values 파일 (기본: values-eks-fargate-<env>.local.yaml
#                           있으면 그것, 없으면 values-eks-fargate-<env>.yaml)
#
# 전제:
#   - terraform/environments/llm-gateway-<env> 가 init 되어 있고 state 접근 가능
#   - aws / kubectl / terraform / python3 / helm 설치, install-eks.sh 를 돌리는 그 호스트
# ==============================================================================

# -e 는 쓰지 않는다. grep/aws 의 "못 찾음"(exit 1)을 정상 분기로 다룬다.
set -uo pipefail

# 도움말은 인자 검사보다 앞이다.
case "${1:-}" in -h|--help) sed -n '5,34p' "$0"; exit 0 ;; esac

# ---- 리전 확정 (install-eks.sh 와 동일 규약) ----
: "${AWS_REGION:=${AWS_DEFAULT_REGION:-}}"
if [ -z "$AWS_REGION" ]; then
    echo "ERROR: region is not set. export AWS_DEFAULT_REGION=<region> and retry." >&2
    exit 1
fi
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

# ---- 인자 ----
ENV="${1:-}"
if [ -z "$ENV" ] || { [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; }; then
    echo "Usage: $0 <dev|prod> [--apply|--verify|--disable] [--values <path>]"
    exit 1
fi
shift

MODE="plan"
VALUES_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)    MODE="apply";    shift ;;
        --verify)   MODE="verify";   shift ;;
        --disable)  MODE="disable";  shift ;;
        --status)   MODE="plan";     shift ;;
        --values)   VALUES_FILE="${2:-}"; shift 2 ;;
        -h|--help)  sed -n '5,34p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ---- 경로 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TF_DIR="$ROOT/deployment/terraform/environments/llm-gateway-$ENV"
TFVARS="$TF_DIR/terraform.tfvars"
CHART_DIR="$ROOT/deployment/charts/llm-gateway"
RELEASE="llm-gateway"
NS="llm-gateway"

if [ -z "$VALUES_FILE" ]; then
    if [ -f "$CHART_DIR/values-eks-fargate-$ENV.local.yaml" ]; then
        VALUES_FILE="$CHART_DIR/values-eks-fargate-$ENV.local.yaml"
    else
        VALUES_FILE="$CHART_DIR/values-eks-fargate-$ENV.yaml"
    fi
fi

[ -d "$TF_DIR" ] || { echo "ERROR: terraform env dir not found: $TF_DIR" >&2; exit 1; }
[ -f "$TFVARS" ] || { echo "ERROR: terraform.tfvars not found: $TFVARS" >&2; exit 1; }
[ -f "$VALUES_FILE" ] || { echo "ERROR: values file not found: $VALUES_FILE" >&2; exit 1; }

info() { echo "== $*"; }
warn() { echo "⚠️  $*" >&2; }

# tfvars 에 enable_body_logging = true 가 있는가 (주석·공백 무시)
tfvars_enabled() {
    grep -Eq '^[[:space:]]*enable_body_logging[[:space:]]*=[[:space:]]*true' "$TFVARS"
}

tfvars_declared() {
    grep -Eq '^[[:space:]]*enable_body_logging[[:space:]]*=' "$TFVARS"
}

terraform_outputs() {
    (cd "$TF_DIR" && terraform output -raw body_log_firehose_stream 2>/dev/null)
    (cd "$TF_DIR" && terraform output -raw body_log_bucket 2>/dev/null)
}

pod_env() {
    kubectl get deploy "$RELEASE-gateway-proxy" -n "$NS" \
        -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"="}{.value}{"\n"}{end}' \
        2>/dev/null | grep -E '^(FIREHOSE_STREAM_NAME|BODY_LOG_S3_BUCKET)=' || true
}

# gatewayProxy.env 에 KEY: VALUE 를 삽입/치환 (line 기반, PyYAML 불요)
set_env_kv() {
    python3 - "$VALUES_FILE" "$1" "$2" <<'PY'
import re, sys
path, key, val = sys.argv[1:4]
lines = open(path).read().splitlines()

# gatewayProxy: 최상위 키 범위
gp = next(i for i, l in enumerate(lines) if re.match(r'^gatewayProxy:\s*$', l))
end = len(lines)
for i in range(gp + 1, len(lines)):
    if re.match(r'^[a-zA-Z]', lines[i]):
        end = i
        break

# 그 안의 "  env:" 줄
env = next((i for i in range(gp + 1, end) if re.match(r'^\s{2}env:\s*$', lines[i])), None)
if env is None:
    print(f"ERROR: gatewayProxy.env block not found in {path}", file=sys.stderr)
    sys.exit(1)

# env 블록 끝 (들여쓰기 2 이하의 줄)
env_end = end
for i in range(env + 1, end):
    if not re.match(r'^\s{3,}|^\s*$', lines[i]):
        env_end = i
        break

kv = f"    {key}: {val}"
pat = re.compile(rf'^\s{{3,}}{re.escape(key)}:')
for i in range(env + 1, env_end):
    if pat.match(lines[i]):
        lines[i] = kv
        break
else:
    lines.insert(env + 1, kv)

open(path, 'w').write("\n".join(lines) + "\n")
PY
}

unset_env_kv() {
    python3 - "$VALUES_FILE" "$1" <<'PY'
import re, sys
path, key = sys.argv[1:3]
lines = open(path).read().splitlines()
pat = re.compile(rf'^\s+{re.escape(key)}:')
out = [l for l in lines if not pat.match(l)]
open(path, 'w').write("\n".join(out) + "\n")
PY
}

# ==============================================================================
info "body-logging 상태 (env=$ENV)"
echo "-- tfvars:   $TFVARS"
if tfvars_enabled; then echo "   enable_body_logging = true"
elif tfvars_declared; then warn "   enable_body_logging 이 선언됐지만 true 가 아님"
else warn "   enable_body_logging 미선언 (기본 false — sink 는 만들어지지 않음)"
fi

echo "-- terraform outputs:"
STREAM="$(cd "$TF_DIR" && terraform output -raw body_log_firehose_stream 2>/dev/null || true)"
BUCKET="$(cd "$TF_DIR" && terraform output -raw body_log_bucket 2>/dev/null || true)"
echo "   stream: ${STREAM:-<none>}"
echo "   bucket: ${BUCKET:-<none>}"

echo "-- gateway-proxy pod env:"
PENV="$(pod_env)"
[ -n "$PENV" ] && echo "$PENV" | sed 's/^/   /' || warn "   FIREHOSE_STREAM_NAME / BODY_LOG_S3_BUCKET 미설정 (인프라 게이트 닫힘 → 토글 켜도 수집 안 됨)"

echo "-- values file: $VALUES_FILE"

# ==============================================================================
if [ "$MODE" = "plan" ]; then
    echo
    info "terraform plan (target: module.body_logging + module.irsa)"
    (cd "$TF_DIR" && terraform plan \
        -target=module.body_logging -target=module.irsa \
        -out=/tmp/bodylog-$ENV.tfplan) | tail -15
    echo
    echo "다음: $0 $ENV --apply"
    exit 0
fi

if [ "$MODE" = "verify" ]; then
    echo
    info "검증"
    ok=0
    if [ -n "${BUCKET:-}" ] && aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
        echo "   ✓ bucket $BUCKET"
    else
        warn "   ✗ bucket 확인 실패: ${BUCKET:-<none>}"; ok=1
    fi
    if [ -n "${STREAM:-}" ]; then
        st="$(aws firehose describe-delivery-stream --delivery-stream-name "$STREAM" \
            --query 'DeliveryStreamDescription.DeliveryStreamStatus' --output text 2>/dev/null || echo '?')"
        echo "   stream $STREAM: $st"
        [ "$st" = "ACTIVE" ] || { warn "   스트림이 ACTIVE 가 아님"; ok=1; }
    else
        warn "   ✗ stream 없음"; ok=1
    fi
    if echo "$PENV" | grep -q '^FIREHOSE_STREAM_NAME=.' && echo "$PENV" | grep -q '^BODY_LOG_S3_BUCKET=.'; then
        echo "   ✓ pod env 설정됨 — 인프라 게이트 열림"
    else
        warn "   ✗ pod env 미설정 — 인프라 게이트 닫힘"; ok=1
    fi
    echo
    echo "수집 on/off 는 admin /monitoring 의 body logging 토글이 관리한다."
    [ $ok -eq 0 ] && echo "READY: 토글을 켜면 수집이 시작된다." || echo "NOT READY: 위의 ✗ 항목을 확인."
    exit $ok
fi

if [ "$MODE" = "disable" ]; then
    echo
    info "env 비우기 — 수집 중지 (인프라는 유지)"
    unset_env_kv FIREHOSE_STREAM_NAME
    unset_env_kv BODY_LOG_S3_BUCKET
    helm upgrade "$RELEASE" "$CHART_DIR" -n "$NS" -f "$VALUES_FILE" | tail -3
    kubectl rollout status "deploy/$RELEASE-gateway-proxy" -n "$NS" --timeout=300s
    echo
    echo "env 제거 + 재배포 완료. admin 토글을 켜도 로거는 no-op 이다."
    echo "S3 버킷/Firehose 삭제는 이 스크립트가 하지 않는다 — 버킷엔 프롬프트 본문이"
    echo "있을 수 있어(force_destroy=false) 객체 비우기 후 terraform 에서 수동 처리."
    exit 0
fi

# ==============================================================================
# --apply
# ==============================================================================
if ! tfvars_enabled; then
    if tfvars_declared; then
        # 선언은 있으나 false/기타 — 값만 교체
        python3 - "$TFVARS" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
s = re.sub(r'(?m)^(\s*enable_body_logging\s*=\s*).+$', r'\g<1>true', s)
open(p, 'w').write(s)
PY
        info "tfvars: enable_body_logging → true (기존 선언 교체)"
    else
        cat >> "$TFVARS" <<'EOF'

# 본문 로깅 sink — enable-body-logging.sh --apply 가 추가 (수집 on/off 는 admin 토글)
enable_body_logging = true
EOF
        info "tfvars: enable_body_logging = true 추가"
    fi
fi

info "terraform apply (target: module.body_logging + module.irsa)"
(cd "$TF_DIR" && terraform plan \
    -target=module.body_logging -target=module.irsa \
    -out=/tmp/bodylog-$ENV.tfplan) | tail -15
(cd "$TF_DIR" && terraform apply /tmp/bodylog-$ENV.tfplan) | tail -10

STREAM="$(cd "$TF_DIR" && terraform output -raw body_log_firehose_stream)"
BUCKET="$(cd "$TF_DIR" && terraform output -raw body_log_bucket)"
[ -n "$STREAM" ] && [ -n "$BUCKET" ] || { echo "ERROR: terraform output 이 비어 있다" >&2; exit 1; }

info "values 주입: $VALUES_FILE"
set_env_kv FIREHOSE_STREAM_NAME "$STREAM"
set_env_kv BODY_LOG_S3_BUCKET "$BUCKET"
grep -nE 'FIREHOSE_STREAM_NAME|BODY_LOG_S3_BUCKET' "$VALUES_FILE" | sed 's/^/   /'

info "helm upgrade + rollout"
helm upgrade "$RELEASE" "$CHART_DIR" -n "$NS" -f "$VALUES_FILE" | tail -3
kubectl rollout status "deploy/$RELEASE-gateway-proxy" -n "$NS" --timeout=300s

echo
echo "완료 — 인프라 게이트가 열렸다. 수집은 아직 꺼져 있다:"
echo "  admin /monitoring → body logging 토글 ON (audit_logs 에 기록, ~5초 내 반영)"
echo "  확인: $0 $ENV --verify"
