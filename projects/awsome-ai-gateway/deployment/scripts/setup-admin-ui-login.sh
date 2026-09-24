#!/usr/bin/env bash
# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

# ==============================================================================
# setup-admin-ui-login.sh — admin-ui Cognito 로그인(ROPC) 활성화 1회성 셋업
# ------------------------------------------------------------------------------
# admin-ui 커스텀 로그인 폼(POST /v1/auth/admin/login) 이 쓰는 admin-api 자체
# 서명 세션 JWT(RS256) 키쌍을 새로 만들고:
#   1. RSA 키쌍 생성 (admin-api/scripts/generate_admin_jwt_keypair.py)
#   2. 공개키 → auth.admin_jwt_configs.public_key_pem UPDATE
#      (admin-api pod 안에서 asyncpg 로 직접 실행 — 로컬에서 Aurora 로 갈 네트워크
#      경로가 없어도 동작)
#   3. 개인키 → Secret 저장
#      - ExternalSecrets 모드: AWS Secrets Manager `/llm-gateway/<env>/app` 에
#        admin_ui_jwt_private_key_pem 프로퍼티 추가
#      - K8s Secret 직접 모드: `kubectl patch secret llm-gateway-app` (JSON merge
#        patch — 기존 키(virtual_key_encryption_key 등) 보존)
#   4. values-eks-fargate-<env>.yaml 에 auth.adminUiJwt.privateKeySecretName 반영
#      (다음 install-eks.sh 실행에서 값이 유지되도록)
#
# 이 스크립트는 Secret/DB 만 바꾼다 — 실제 반영은 여전히 마지막에 안내하는
# install-eks.sh <env> 를 별도로 실행해야 한다 (checksum/secret annotation 이
# admin-api pod 롤아웃을 트리거함).
#
# 사용법:  bash deployment/scripts/setup-admin-ui-login.sh <dev|prod>
# 전제:    admin-api/admin-ui 가 이미 배포되어 있고(install-eks.sh 로) kubectl
#          context 가 그 클러스터를 가리키고 있어야 함.
# ==============================================================================
set -euo pipefail

ENV="${1:-}"
if [ -z "$ENV" ] || { [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; }; then
    echo "Usage: $0 <dev|prod>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
CHART_DIR="$DEPLOY_DIR/charts/llm-gateway"
VALUES_FILE="$CHART_DIR/values-eks-fargate-$ENV.yaml"
KEYGEN_SCRIPT="$ROOT_DIR/admin-api/scripts/generate_admin_jwt_keypair.py"

RELEASE_NAME="${RELEASE_NAME:-llm-gateway}"
NAMESPACE="${NAMESPACE:-llm-gateway}"
SECRET_NAME="${SECRET_NAME:-llm-gateway-app}"
SECRET_KEY="admin_ui_jwt_private_key_pem"
JWT_CONFIG_ID="${ADMIN_UI_JWT_CONFIG_ID:-00000000-0000-4000-a000-000000000030}"
SM_SECRET_ID="/llm-gateway/$ENV/app"

R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; N='\033[0m'
info()  { echo -e "${B}ℹ${N}  $*"; }
ok()    { echo -e "${G}✓${N}  $*"; }
warn()  { echo -e "${Y}⚠${N}  $*"; }
err()   { echo -e "${R}✗${N}  $*" >&2; }

[ -f "$VALUES_FILE" ]   || { err "values 파일 없음: $VALUES_FILE"; exit 1; }
[ -f "$KEYGEN_SCRIPT" ] || { err "키 생성 스크립트 없음: $KEYGEN_SCRIPT"; exit 1; }

for cmd in kubectl aws jq python3; do
    command -v "$cmd" >/dev/null 2>&1 || { err "필요한 도구 없음: $cmd"; exit 1; }
done

DEPLOY="${RELEASE_NAME}-admin-api"
kubectl -n "$NAMESPACE" get deploy "$DEPLOY" >/dev/null 2>&1 \
    || { err "$DEPLOY 를 찾을 수 없습니다 (namespace=$NAMESPACE). install-eks.sh $ENV 를 먼저 실행했나요? kubectl context 도 확인하세요."; exit 1; }

# ---- 0. 키쌍을 admin-api pod 내부에서 생성 ----
# 배포 EC2에 cryptography 를 설치할 필요 없이, admin-api 이미지에 이미 포함된
# cryptography/asyncpg 를 사용한다. 개인키는 pod /tmp 에만 잠깐 존재하며
# 스크립트 종료 전에 삭제한다.
POD_TMP="/tmp"
GEN_SCRIPT="$POD_TMP/admin_jwt_keygen_$$.py"
PRIV_FILE="$POD_TMP/admin_ui_jwt_private_$$.pem"
PUB_FILE="$POD_TMP/admin_ui_jwt_public_$$.pem"

info "admin-api pod 내부에서 RSA 키쌍 생성 중..."
kubectl -n "$NAMESPACE" exec -i "deploy/$DEPLOY" -- sh -c "cat > $GEN_SCRIPT" < "$KEYGEN_SCRIPT"
kubectl -n "$NAMESPACE" exec "deploy/$DEPLOY" -- python3 "$GEN_SCRIPT" \
    --private-out "$PRIV_FILE" --public-out "$PUB_FILE" --quiet
ok "키쌍 생성 완료 (pod /tmp, 종료 시 삭제)"

# ---- ESO 여부 자동 감지 ----
USE_ESO=0
if aws secretsmanager describe-secret --secret-id "$SM_SECRET_ID" >/dev/null 2>&1; then
    USE_ESO=1
fi

cat <<SUMMARY

── 이 값으로 admin-ui Cognito 로그인을 활성화한다 ──
  환경                  : $ENV
  admin-api Deployment  : $NAMESPACE/$DEPLOY
  admin_jwt_configs.id  : $JWT_CONFIG_ID  (공개키 UPDATE 대상)
  개인키 저장 방식      : $([ "$USE_ESO" = 1 ] && echo "ExternalSecrets → Secrets Manager $SM_SECRET_ID" || echo "K8s Secret 직접 patch ($NAMESPACE/$SECRET_NAME)")
  values 파일           : $VALUES_FILE (auth.adminUiJwt.privateKeySecretName 반영)

⚠️  기존에 이미 admin-ui Cognito 로그인이 활성화되어 있었다면, 이 작업으로 키가
    교체되어 기존 admin-ui 세션이 전부 무효화됩니다(재로그인 필요).
SUMMARY
read -rp "진행할까요? (y/N) " ok_confirm
[ "$ok_confirm" = y ] || [ "$ok_confirm" = Y ] || { echo "취소됨 — 아무것도 바꾸지 않음"; exit 0; }

# ---- 2. 공개키 → DB UPDATE (admin-api pod 안에서 asyncpg 로 직접 실행) ----
info "공개키를 auth.admin_jwt_configs 에 반영 중..."
PUB_CONTENT=$(kubectl -n "$NAMESPACE" exec "deploy/$DEPLOY" -- cat "$PUB_FILE")
PUB_B64=$(printf '%s' "$PUB_CONTENT" | base64 -w0 2>/dev/null || printf '%s' "$PUB_CONTENT" | base64 | tr -d '\n')
kubectl -n "$NAMESPACE" exec "deploy/$DEPLOY" -- \
    env PUB_B64="$PUB_B64" JWT_CONFIG_ID="$JWT_CONFIG_ID" python3 -c '
import asyncio, base64, os
from urllib.parse import urlsplit, urlunsplit, parse_qs
import asyncpg

async def main():
    pub = base64.b64decode(os.environ["PUB_B64"]).decode()
    cfg_id = os.environ["JWT_CONFIG_ID"]
    raw = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    # asyncpg.connect() 는 DATABASE_URL 의 "?ssl=require" 쿼리스트링을 SQLAlchemy 처럼
    # 자동 변환하지 않는다 — 그대로 넘기면 RDS Proxy 가 알 수 없는 startup 옵션으로
    # 받아 거부한다("Feature not supported: ... option ssl"). 쿼리스트링을 떼어내고
    # asyncpg 의 ssl= 키워드 인자로 직접 전달한다.
    parts = urlsplit(raw)
    ssl_mode = (parse_qs(parts.query).get("ssl") or ["require"])[0]
    dsn = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    use_ssl = ssl_mode not in ("disable", "false", "0")
    conn = await asyncpg.connect(dsn, ssl=use_ssl)
    try:
        result = await conn.execute(
            "UPDATE auth.admin_jwt_configs SET public_key_pem = $1, updated_at = now() WHERE id = $2",
            pub, cfg_id,
        )
        print(f"DB UPDATE result: {result}")
        if result == "UPDATE 0":
            raise SystemExit(f"admin_jwt_configs.id={cfg_id} 행이 없습니다 — db/init seed 가 적용됐는지 확인하세요.")
    finally:
        await conn.close()

asyncio.run(main())
' || { err "DB UPDATE 실패"; exit 1; }
ok "공개키 DB 반영 완료"

# ---- 3. 개인키 → Secret 저장 ----
info "개인키를 Secret 에 저장 중..."
PRIV_CONTENT=$(kubectl -n "$NAMESPACE" exec "deploy/$DEPLOY" -- cat "$PRIV_FILE")
PRIV_B64=$(printf '%s' "$PRIV_CONTENT" | base64 -w0 2>/dev/null || printf '%s' "$PRIV_CONTENT" | base64 | tr -d '\n')
if [ "$USE_ESO" = 1 ]; then
    CURRENT_JSON=$(aws secretsmanager get-secret-value --secret-id "$SM_SECRET_ID" --query SecretString --output text)
    NEW_JSON=$(PRIV_CONTENT="$PRIV_CONTENT" python3 -c '
import json, os, sys
data = json.loads(sys.argv[1])
data["'"$SECRET_KEY"'"] = os.environ["PRIV_CONTENT"]
print(json.dumps(data))
' "$CURRENT_JSON")
    aws secretsmanager put-secret-value --secret-id "$SM_SECRET_ID" --secret-string "$NEW_JSON" >/dev/null
    ok "Secrets Manager $SM_SECRET_ID 에 $SECRET_KEY 추가 완료"
    info "ESO 강제 재동기화 트리거..."
    kubectl -n "$NAMESPACE" annotate externalsecret "$SECRET_NAME" force-sync="$(date +%s)" --overwrite >/dev/null 2>&1 || true
else
    kubectl -n "$NAMESPACE" patch secret "$SECRET_NAME" --type=merge \
        -p "{\"data\":{\"$SECRET_KEY\":\"$PRIV_B64\"}}"
    ok "K8s Secret $NAMESPACE/$SECRET_NAME 에 $SECRET_KEY 병합 완료 (기존 키 보존)"
fi

# ---- 4. values 파일에 privateKeySecretName 반영 ----
info "values 파일에 auth.adminUiJwt.privateKeySecretName 반영 중..."
python3 - "$VALUES_FILE" "$SECRET_NAME" "$SECRET_KEY" <<'PY'
import re, sys

path, secret_name, secret_key = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()

if re.search(r"privateKeySecretName:\s*", text):
    text = re.sub(
        r"(privateKeySecretName:\s*).*",
        r'\1"' + secret_name + '"',
        text,
        count=1,
    )
elif re.search(r"^auth:\s*$", text, re.M):
    text = re.sub(
        r"(^auth:\s*\n)",
        r"\1  adminUiJwt:\n    privateKeySecretName: \"" + secret_name + "\"\n"
        r"    privateKeySecretKey: \"" + secret_key + "\"\n",
        text,
        count=1,
        flags=re.M,
    )
else:
    text = text.rstrip("\n") + (
        f'\n\nauth:\n  adminUiJwt:\n    privateKeySecretName: "{secret_name}"\n'
        f'    privateKeySecretKey: "{secret_key}"\n'
    )

open(path, "w", encoding="utf-8").write(text)
PY
ok "values 파일 반영 완료"

info "pod 임시 키 파일 삭제 중..."
kubectl -n "$NAMESPACE" exec "deploy/$DEPLOY" -- sh -c "rm -f '$GEN_SCRIPT' '$PRIV_FILE' '$PUB_FILE'"
ok "pod /tmp 임시 파일 삭제 완료"

echo
ok "완료. 마지막으로 실제 배포에 반영하세요:"
echo "    bash deployment/scripts/install-eks.sh $ENV"
echo
warn "위 install-eks.sh 실행 전까지는 admin-api 가 여전히 예전 개인키(또는 미설정) 상태로 떠 있습니다."
