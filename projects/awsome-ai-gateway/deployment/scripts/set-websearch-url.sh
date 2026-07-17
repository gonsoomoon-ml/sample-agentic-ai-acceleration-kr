#!/usr/bin/env bash
# ============================================================================
# set-websearch-url.sh — provision_agentcore_websearch.py 가 만든 Gateway 의
#   /mcp URL 을 values-eks-fargate-<env>.yaml 에 넣는다.
#   URL(95자)을 사람이 복사·붙여넣지 않게 해서 오타/줄바꿈 사고를 없앤다.
#     - URL 은 프로비저너 `status` 출력에서 직접 읽는다 (단일 출처)
#     - dev values 엔 AGENTCORE_* 키가 있고(치환), prod values 엔 없다(삽입)
#       → prod 승격(§8-P) 때 web search 가 조용히 꺼지던 구멍도 같이 막는다.
# 멱등: 다시 실행하면 값만 교체(중복 안 생김).
# 사용:  bash deployment/scripts/set-websearch-url.sh [dev|prod]
#        (§5-1 프로비저닝을 먼저 끝낸 뒤에 실행)
# ============================================================================
set -euo pipefail

ENV="${1:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
V="$DEPLOY_DIR/charts/llm-gateway/values-eks-fargate-$ENV.yaml"

# §5-1 과 같은 좌표여야 같은 Gateway 를 찾는다 (프로비저너 기본 GW_NAME 은 env 접미사 없음).
REGION="${REGION:-us-east-1}"      # 관리형 WebSearch 커넥터는 us-east-1 전용
GW_NAME="${GW_NAME:-llm-gateway-websearch-$ENV}"

[ -f "$V" ] || { echo "❌ values 파일 없음: $V"; exit 1; }

echo "→ AgentCore Gateway 조회 중 (region=$REGION, name=$GW_NAME)..."
OUT=$(REGION="$REGION" GW_NAME="$GW_NAME" \
      python3 "$SCRIPT_DIR/provision_agentcore_websearch.py" status)
printf '%s\n' "$OUT"

URL=$(printf '%s\n' "$OUT" | sed -n 's#^  url: ##p')
[ -n "$URL" ] || { echo "❌ Gateway 를 못 찾음 — §5-1 deploy 를 먼저 실행"; exit 1; }
# status 는 raw gatewayUrl 을 찍는다(deploy 와 달리 /mcp 정규화 없음) → 여기서 맞춘다.
case "$URL" in */mcp) ;; *) URL="${URL%/}/mcp" ;; esac

printf '%s\n' "$OUT" | grep -q 'target: .*status=READY' \
  || echo "⚠️  target 이 READY 가 아니다 — 검색 호출이 실패할 수 있음"

echo
echo "── $V 에 넣을 값 ──"
echo "  AGENTCORE_GATEWAY_URL : $URL"
echo "  AGENTCORE_REGION      : $REGION"
echo "  AGENTCORE_TARGET_ID   : web-search-tool"
read -rp "진행할까요? (y/N) " ok
[ "$ok" = y ] || [ "$ok" = Y ] || { echo "취소됨 — 파일 안 건드림"; exit 0; }

if grep -q '^    AGENTCORE_GATEWAY_URL:' "$V"; then
  sed -i 's#\(^    AGENTCORE_GATEWAY_URL: \).*#\1"'"$URL"'"#' "$V"
else
  # prod values 엔 web search 블록이 통째로 없다 → gatewayProxy.env 끝(WORKERS)에 붙인다.
  awk -v u="$URL" -v r="$REGION" '/^    WORKERS: "2"/{print
    print "    # Server-side web search (§5). URL 은 set-websearch-url.sh 가 채운다."
    print "    AGENTCORE_GATEWAY_URL: \"" u "\""
    print "    AGENTCORE_REGION: \"" r "\"     # 커넥터가 us-east-1 전용 (aws.region 과 다름)"
    print "    AGENTCORE_TARGET_ID: \"web-search-tool\""
    next}{print}' "$V" > "$V.tmp" && mv "$V.tmp" "$V"
fi

# 삽입 앵커(WORKERS)가 바뀌면 awk 가 조용히 아무것도 안 한다 → 여기서 잡는다.
grep -q '^    AGENTCORE_GATEWAY_URL:' "$V" \
  || { echo "❌ 값을 못 넣었다 — gatewayProxy.env 에 손으로 추가할 것:"; \
       echo "     AGENTCORE_GATEWAY_URL: \"$URL\""; exit 1; }

echo
echo "✅ 완료. 반영된 줄:"
grep -n '^    AGENTCORE_GATEWAY_URL:\|^    AGENTCORE_REGION:\|^    AGENTCORE_TARGET_ID:' "$V"
echo
echo "다음: ./deployment/scripts/install-eks.sh $ENV   (파드 재시작 = 값 반영)"
