#!/usr/bin/env bash
# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

# set-notification-provider.sh — notification-worker의 이메일 발송 채널을 변경한다.
# -------------------------------------------------------------------------------
# values-eks-fargate-<env>.yaml 의 notificationWorker.email 블록을 수정하고,
# 마지막에 install-eks.sh <env> 를 실행해서 배포에 반영하도록 안내한다.
#
# 지원 provider:
#   mock         — 실제 발송 안 함(기본, 개발/테스트)
#   internal_api — 사내 메일 API (http extra가 포함된 기본 이미지로 동작)
#   smtp         — SMTP 서버 (aiosmtplib extra 필요 → notification-worker 재빌드)
#
# 사용법: bash deployment/scripts/set-notification-provider.sh <dev|prod> <mock|internal_api|smtp>
# -------------------------------------------------------------------------------
set -euo pipefail

ENV="${1:-}"
PROVIDER="${2:-}"

usage() {
    cat <<EOF
Usage: $0 <dev|prod> <mock|internal_api|smtp|ses>

예시:
    $0 dev mock
    $0 dev internal_api
    $0 prod smtp
    $0 prod ses
EOF
}

if [ -z "$ENV" ] || [ -z "$PROVIDER" ]; then
    usage
    exit 1
fi

if [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; then
    echo "❌ env 는 dev 또는 prod 만 지원합니다: $ENV"
    exit 1
fi

case "$PROVIDER" in
    mock | internal_api | smtp | ses) ;;
    *)
        echo "❌ 지원하지 않는 provider: $PROVIDER"
        usage
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V="$SCRIPT_DIR/../charts/llm-gateway/values-eks-fargate-$ENV.yaml"

[ -f "$V" ] || { echo "❌ values 파일을 찾을 수 없습니다: $V"; exit 1; }

R='\033[0;31m'; G='\033[0;32m'; B='\033[0;34m'; Y='\033[1;33m'; N='\033[0m'
info()  { echo -e "${B}ℹ${N}  $*"; }
ok()    { echo -e "${G}✓${N}  $*"; }
warn()  { echo -e "${Y}⚠${N}  $*"; }
err()   { echo -e "${R}✗${N}  $*" >&2; }

URL=""
FROM_ADDRESS=""
FROM_NAME=""
SMTP_HOST=""
SMTP_PORT=587
SMTP_STARTTLS="true"
CREDENTIALS_SECRET=""
SES_REGION=""

# ── provider 별 추가 입력 ──
if [ "$PROVIDER" = "internal_api" ]; then
    read -rp "Internal API URL (예: http://mail-api.internal/send): " URL
    [ -n "$URL" ] || { err "URL 을 입력해야 합니다."; exit 1; }
    read -rp "From address [no-reply-dev@llm-gateway.local]: " FROM_ADDRESS
    FROM_ADDRESS="${FROM_ADDRESS:-no-reply-dev@llm-gateway.local}"
    read -rp "From name [LLM Gateway]: " FROM_NAME
    FROM_NAME="${FROM_NAME:-LLM Gateway}"
elif [ "$PROVIDER" = "smtp" ]; then
    read -rp "SMTP host: " SMTP_HOST
    [ -n "$SMTP_HOST" ] || { err "SMTP host 를 입력해야 합니다."; exit 1; }
    read -rp "SMTP port [587]: " _port
    SMTP_PORT="${_port:-587}"
    read -rp "Use STARTTLS [true]: " _starttls
    SMTP_STARTTLS="${_starttls:-true}"
    read -rp "From address: " FROM_ADDRESS
    [ -n "$FROM_ADDRESS" ] || { err "From address 를 입력해야 합니다."; exit 1; }
    read -rp "SMTP credentials secret name (optional): " CREDENTIALS_SECRET
    info "SMTP 를 사용하려면 credentialsSecretName 으로 K8s Secret (username/password) 이 필요합니다."
    info "notification-worker 기본 이미지는 aiosmtplib extra 를 포함하고 있습니다."
elif [ "$PROVIDER" = "ses" ]; then
    read -rp "AWS SES region [us-east-1]: " SES_REGION
    SES_REGION="${SES_REGION:-us-east-1}"
    read -rp "From address [no-reply-dev@llm-gateway.local]: " FROM_ADDRESS
    FROM_ADDRESS="${FROM_ADDRESS:-no-reply-dev@llm-gateway.local}"
    read -rp "From name [LLM Gateway]: " FROM_NAME
    FROM_NAME="${FROM_NAME:-LLM Gateway}"
    warn "SES 는 notification-worker pod 의 AWS 권한(IRSA/Fargate 노드 역할)에 ses:SendEmail 이 필요합니다."
fi

# ── values 파일 백업 ──
BACKUP="$V.bak.$(date +%s)"
cp "$V" "$BACKUP"
info "백업 생성: $BACKUP"

# ── 새 email 블록 생성 ──
python3 - "$V" "$PROVIDER" "$URL" "$FROM_ADDRESS" "$FROM_NAME" "$SMTP_HOST" "$SMTP_PORT" "$SMTP_STARTTLS" "$CREDENTIALS_SECRET" "$SES_REGION" <<'PY'
import re, sys

v, provider, url, from_addr, from_name, smtp_host, smtp_port, smtp_starttls, creds, ses_region = sys.argv[1:11]

with open(v, encoding="utf-8") as f:
    text = f.read()

if provider == "mock":
    new = '''  email:
    provider: "mock"
    internalApi:
      url: ""
      fromAddress: "no-reply-dev@llm-gateway.local"
      fromName: "LLM Gateway"
'''
elif provider == "internal_api":
    new = f'''  email:
    provider: "internal_api"
    internalApi:
      url: "{url}"
      fromAddress: "{from_addr}"
      fromName: "{from_name}"
'''
elif provider == "smtp":
    creds_line = f'\n    credentialsSecretName: "{creds}"' if creds else ""
    new = f'''  email:
    provider: "smtp"
    smtp:
      host: "{smtp_host}"
      port: {smtp_port}
      startTls: {smtp_starttls}
      fromAddress: "{from_addr}"{creds_line}
'''
elif provider == "ses":
    new = f'''  email:
    provider: "ses"
    ses:
      region: "{ses_region}"
      fromAddress: "{from_addr}"
      fromName: "{from_name}"
'''
else:
    raise ValueError(f"unknown provider: {provider}")

# notificationWorker: 아래의 2-space email 블록을 통째로 교체
# 원문의 4-space ~ 6-space 라인까지 흡수하고, 빈 줄/다음 키 앞에서 멈춘다.
pattern = r'^(  email:\n(?:    .*\n)*)'
new_text, n = re.subn(pattern, new, text, count=1, flags=re.MULTILINE)

if n == 0:
    # 기존 블록을 못 찾으면 values 파일을 건드리지 않고 실패
    raise SystemExit("❌ values 파일에서 notificationWorker.email 블록을 찾지 못했습니다.")

with open(v, "w", encoding="utf-8") as f:
    f.write(new_text)
PY

ok "values 파일 수정 완료: $V"

echo
info "배포에 적용하려면 아래를 실행하세요:"
echo "    bash deployment/scripts/install-eks.sh $ENV"
echo
info "변경된 email 블록 미리보기:"
grep -A8 '^  email:' "$V"
