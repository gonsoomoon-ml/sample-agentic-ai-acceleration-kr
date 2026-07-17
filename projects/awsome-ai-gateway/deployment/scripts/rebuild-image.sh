#!/usr/bin/env bash
# ============================================================================
# rebuild-image.sh — 서비스 하나만 다시 빌드해서 ECR 에 올린다 (코드 패치 후 사용).
#   §3-5 는 6종을 전부 굽지만, 소스 한 곳을 고쳤을 때 필요한 건 그 서비스 하나뿐이다.
#   태그는 **helm 이 실제로 당길 값**을 그대로 쓴다(values 에 tag 가 없는 서비스는
#   Chart.appVersion 으로 폴백되므로 values 를 grep 하면 틀린다 = §3-5 와 같은 규칙).
#
#   ⚠️ 같은 태그로 덮어쓴다 → helm upgrade 만으로는 파드가 안 바뀔 수 있다(매니페스트
#      무변경이면 롤아웃이 안 일어남). 값도 같이 바뀌는 경우(예: §5-2 의 URL 주입)는
#      install-eks.sh 가 롤아웃을 일으켜 새 이미지를 당긴다. 코드만 바뀐 경우엔
#      마지막에 안내되는 rollout restart 를 쓴다. (Fargate 는 노드 캐시가 없어
#      새 파드는 항상 레지스트리에서 당긴다 → 같은 태그라도 새 내용이 반영된다.)
#
# 사용:  bash deployment/scripts/rebuild-image.sh gateway-proxy [dev|prod]
# ============================================================================
set -euo pipefail

# ---- 리전 확정 (install-eks.sh 와 동일 규약: 추측하지 않고 중단) ----
: "${AWS_REGION:=${AWS_DEFAULT_REGION:-}}"
if [ -z "$AWS_REGION" ]; then
  echo "ERROR: region is not set. export AWS_DEFAULT_REGION=<region> (e.g. us-west-2) and retry." >&2
  exit 1
fi
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

SVC="${1:-}"
ENV="${2:-dev}"
[ -n "$SVC" ] || { echo "Usage: $0 <service> [dev|prod]   (예: $0 gateway-proxy dev)"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHART="$ROOT/deployment/charts/llm-gateway"
V="$CHART/values-eks-fargate-$ENV.yaml"
[ -f "$V" ] || { echo "❌ values 없음: $V"; exit 1; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR_BASE="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/llm-gateway"

# ---- helm 이 당길 태그 그대로 (§3-5 와 같은 출처) ----
IMG=$(helm template t "$CHART" -f "$V" \
      | grep -oE 'image: "[^"]+"' | sed 's/image: "//; s/"$//' | sort -u \
      | grep "/llm-gateway/$SVC:" | head -1)
[ -n "$IMG" ] || { echo "❌ helm 이 당기는 이미지 목록에 '$SVC' 가 없다. 서비스명 확인:"; \
                   helm template t "$CHART" -f "$V" | grep -oE 'image: "[^"]+"' \
                     | sed 's/image: "//; s/"$//' | sort -u | grep /llm-gateway/; exit 1; }
TAG="${IMG##*:}"
CTX="$ROOT/$SVC"; [ "$SVC" = migration ] && CTX="$ROOT/db"
[ -d "$CTX" ] || { echo "❌ 빌드 컨텍스트 없음: $CTX"; exit 1; }

echo "── 재빌드 ──"
echo "  service : $SVC"
echo "  tag     : $TAG   (helm 기준 — 손으로 정하지 않음)"
echo "  context : $CTX"
echo "  target  : $ECR_BASE/$SVC:$TAG"
echo

aws ecr get-login-password | docker login --username AWS --password-stdin \
  "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com" >/dev/null
docker build --platform linux/amd64 -t "$ECR_BASE/$SVC:$TAG" "$CTX"
docker push "$ECR_BASE/$SVC:$TAG"

cat <<EOF

✅ push 완료: $ECR_BASE/$SVC:$TAG

다음 — 값도 함께 바뀌었으면(예: §5-2 URL 주입) 그냥:
    ./deployment/scripts/install-eks.sh $ENV
코드만 바뀌었으면 같은 태그라 롤아웃이 안 일어나므로:
    kubectl -n llm-gateway rollout restart deploy/llm-gateway-$SVC
    kubectl -n llm-gateway rollout status  deploy/llm-gateway-$SVC --timeout=5m
EOF
