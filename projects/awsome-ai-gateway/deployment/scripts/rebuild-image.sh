#!/usr/bin/env bash
# ============================================================================
# rebuild-image.sh — 서비스 하나만 다시 빌드해서 ECR 에 올린다 (코드 패치 후 사용).
#   §3-5 는 6종을 전부 굽지만, 소스 한 곳을 고쳤을 때 필요한 건 그 서비스 하나뿐이다.
#   태그는 **helm 이 실제로 당길 값**을 그대로 쓴다(values 에 tag 가 없는 서비스는
#   Chart.appVersion 으로 폴백되므로 values 를 grep 하면 틀린다 = §3-5 와 같은 규칙).
#
#   ECR 에 그 태그가 이미 있으면 **빌드하지 않고 건너뛴다**. 태그가 그대로 = 코드가
#   그대로라는 뜻이고, 다시 빌드하면 실제로 시험·배포한 이미지가 같은 이름의 새 빌드로
#   덮여 되돌릴 수 없다(2026-10-11 dev 에서 실제로 일어남). 그래서 업데이트 때는 6개
#   서비스를 모두 돌려도 (5) 에서 태그가 오른 것만 빌드된다. ECR 조회가 다른 이유로
#   실패하면 있는지 모르므로 멈춘다.
#
#   --force: 같은 태그로 덮어쓴다(개발 중 급한 패치). 이때 helm upgrade 만으로는 파드가
#      안 바뀔 수 있다(매니페스트 무변경이면 롤아웃이 안 일어남) → 마지막에 안내되는
#      rollout restart 를 쓴다. (Fargate 는 노드 캐시가 없어 새 파드는 항상 레지스트리에서
#      당긴다 → 같은 태그라도 새 내용이 반영된다.)
#
# 사용:  bash deployment/scripts/rebuild-image.sh gateway-proxy [dev|prod] [--force]
# ============================================================================
set -euo pipefail

# ---- 리전 확정 (install-eks.sh 와 동일 규약: 추측하지 않고 중단) ----
: "${AWS_REGION:=${AWS_DEFAULT_REGION:-}}"
if [ -z "$AWS_REGION" ]; then
  echo "ERROR: region is not set. export AWS_DEFAULT_REGION=<region> (e.g. us-west-2) and retry." >&2
  exit 1
fi
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

FORCE=0; POS=()
for a in "$@"; do
  case "$a" in --force) FORCE=1 ;; *) POS+=("$a") ;; esac
done
SVC="${POS[0]:-}"
ENV="${POS[1]:-dev}"
[ -n "$SVC" ] || { echo "Usage: $0 <service> [dev|prod] [--force]   (예: $0 gateway-proxy dev)"; exit 1; }

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

# ---- ECR 에 이미 있는 태그는 건너뛴다 (태그가 그대로 = 코드가 그대로) ----
if [ "$FORCE" -eq 0 ]; then
  if out=$(aws ecr describe-images --repository-name "llm-gateway/$SVC" \
             --image-ids imageTag="$TAG" \
             --query 'imageDetails[0].imagePushedAt' --output text 2>&1); then
    echo "⏭️  건너뜀 — ECR 에 이미 있음: $SVC:$TAG (push $out)"
    echo "    태그가 그대로라 다시 빌드하지 않는다(옛 이미지를 덮어쓰지 않음)."
    echo "    코드가 바뀌었으면 13-bump-image-tags.sh 로 태그부터 올린다. 꼭 덮어써야 하면 --force."
    exit 0
  elif [[ "$out" != *ImageNotFoundException* ]]; then
    echo "❌ ECR 조회 실패 — 이 태그가 이미 있는지 알 수 없어 멈춘다(덮어쓰기 방지):"
    echo "$out"
    exit 1
  fi
fi

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
