#!/usr/bin/env bash
# ============================================================================
# fill-org-values.sh — values-eks-fargate-<env>.yaml 의 배포별 값을 채운다.
#   install-eks.sh 가 helm --set 으로 자동 주입하는 값(IRSA·엔드포인트·region·
#   issuer 등) 말고, terraform 으로 못 구하는 org 값만 다룬다:
#     - COGNITO_USER_POOL_ID / COGNITO_REGION  (terraform output 에서 유도)
#     - adminBootstrap.emails                  (프롬프트)
#     - ingress inbound-cidrs = 배포EC2/32,관리자PC/32
#         (배포 EC2 IP 는 checkip 로 자동, 관리자 PC IP 는 프롬프트)
#   ⚠️ inbound-cidrs 를 비우면 ALB 기본이 0.0.0.0/0 (전 세계 오픈)이므로,
#      이 스크립트가 반드시 채워 그 실수를 막는다.
# 멱등: 다시 실행하면 값만 교체(중복 안 생김). 요약 확인 후에만 파일을 고친다.
# 사용:  bash deployment/scripts/fill-org-values.sh [dev|prod]
# ============================================================================
set -euo pipefail

ENV="${1:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$DEPLOY_DIR/terraform/environments/llm-gateway-$ENV"
V="$DEPLOY_DIR/charts/llm-gateway/values-eks-fargate-$ENV.yaml"

[ -f "$V" ]      || { echo "❌ values 파일 없음: $V"; exit 1; }
[ -d "$TF_DIR" ] || { echo "❌ terraform 환경 없음: $TF_DIR"; exit 1; }

# ---- terraform output 에서 유도 (region 은 issuer URL 에서 추출 = 리전 하드코딩 없음) ----
echo "→ terraform output 읽는 중 ($TF_DIR)..."
POOL_ID=$(cd "$TF_DIR" && terraform output -raw cognito_user_pool_id)
ISSUER=$(cd "$TF_DIR"  && terraform output -raw cognito_issuer_url)
REGION=$(printf '%s' "$ISSUER" | sed -n 's#https://cognito-idp\.\([^.]*\)\.amazonaws\.com/.*#\1#p')
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
[ -n "$POOL_ID" ] || { echo "❌ cognito_user_pool_id 를 못 읽음 (terraform apply 완료?)"; exit 1; }
[ -n "$REGION" ]  || { echo "❌ issuer URL 에서 region 추출 실패: $ISSUER"; exit 1; }
[ -n "$ACCOUNT" ] || { echo "❌ 계정번호 확인 실패 (aws sts)"; exit 1; }

echo "→ 배포 EC2 공인 IP 확인 중..."
EC2_IP=$(curl -s https://checkip.amazonaws.com)
[ -n "$EC2_IP" ] || { echo "❌ EC2 공인 IP 확인 실패"; exit 1; }

# ---- 프롬프트 (사람만 아는 값) ----
read -rp "운영자 이메일 (§3-8 Cognito 사용자와 동일해야 함): " EMAIL
[ -n "$EMAIL" ] || { echo "❌ 이메일이 비었다"; exit 1; }
echo "  (관리자 PC 브라우저에서 https://checkip.amazonaws.com 열어 나온 값)"
read -rp "관리자 PC 공인 IP: " PC_IP
[ -n "$PC_IP" ] || { echo "❌ IP 가 비었다"; exit 1; }
CIDRS="${EC2_IP}/32,${PC_IP}/32"

# ---- 요약 후 확인 ----
cat <<SUMMARY

── 이 값으로 $V 를 고친다 ──
  COGNITO_USER_POOL_ID : $POOL_ID   (terraform)
  COGNITO_REGION       : $REGION    (issuer URL 에서)
  adminBootstrap email : $EMAIL
  inbound-cidrs        : $CIDRS   (EC2 $EC2_IP + PC $PC_IP)
  + placeholder 정리   : 123456789012 → $ACCOUNT, ap-northeast-2 → $REGION
                         chat-agent(미사용) ARN/버킷 → 빈 값
SUMMARY
read -rp "진행할까요? (y/N) " ok
[ "$ok" = y ] || [ "$ok" = Y ] || { echo "취소됨 — 파일 안 건드림"; exit 0; }

# ---- chat-agent 값 비우기 (먼저: 이래야 아래 전역치환이 '진짜 같은 가짜 ARN' 을 안 만든다) ----
# admin-chat-agent 는 별도 배포(§0 out-of-scope). ARN 이 비면 chat 엔드포인트가
# 503 "not configured" 로 깔끔히 꺼진다(chat_agent.py:332). non-empty placeholder 는
# 그 경로를 건너뛰고 없는 ARN 으로 호출을 시도해 지저분한 AWS 에러를 낸다.
sed -i 's#\(AGENTCORE_RUNTIME_ARN: \).*#\1""#' "$V"
sed -i 's#\(CHAT_STAGING_BUCKET: \).*#\1""#' "$V"

# ---- placeholder 계정·리전 정리 (미관: 자동주입 값이 파일에도 실제값으로 보이게) ----
# web search 의 us-east-1 은 다른 토큰이라 안 건드려짐. 자동주입되는 값들도 여기서
# 실제값이 되지만 install-eks.sh 의 --set 값과 동일(역할명·registry 가 결정적)이라 무해.
sed -i "s/123456789012/$ACCOUNT/g; s/ap-northeast-2/$REGION/g" "$V"

# ---- org 값 치환 ----
sed -i 's#\(COGNITO_USER_POOL_ID: \).*#\1"'"$POOL_ID"'"#' "$V"
sed -i 's#\(^    COGNITO_REGION: \).*#\1"'"$REGION"'"#' "$V"
awk -v e="$EMAIL" '/^    emails:$/{print;f=1;next} f&&/^      - /{print "      - \"" e "\"";f=0;next}{print}' "$V" > "$V.tmp" && mv "$V.tmp" "$V"
if grep -q 'inbound-cidrs' "$V"; then
  sed -i 's#\(inbound-cidrs: \).*#\1"'"$CIDRS"'"#' "$V"
else
  awk -v c="$CIDRS" '/^  annotations:$/&&!d{print;print "    alb.ingress.kubernetes.io/inbound-cidrs: \"" c "\"";d=1;next}{print}' "$V" > "$V.tmp" && mv "$V.tmp" "$V"
fi

echo
echo "✅ 완료. 반영된 줄:"
grep -n 'COGNITO_USER_POOL_ID:\|^    COGNITO_REGION:\|inbound-cidrs:' "$V"
grep -n -A1 '^    emails:$' "$V" | tail -1
