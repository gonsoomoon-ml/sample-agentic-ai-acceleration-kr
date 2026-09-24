# 8-L. admin 콘솔 Cognito 로그인 (US-12)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-L**

관리 화면(admin-ui)에 **Cognito 로그인**을 켠다. 지금은 개발용 로그인(dev-login)이 켜져 있어 **admin 주소에 닿는 사람은 누구나 관리자**다 — 역할만 고르면 서명 없는 관리자 토큰이 나온다. 로그인 코드는 이미 배포된 이미지에 있고(US-10), 켜는 데 필요한 것은 **설정 3가지**다: Cognito 콜백 주소 1개 · admin-ui 설정 4줄 · dev-login 끄기. 셋 다 `update-scripts/19-admin-login.sh` 가 계산해서 넣는다. 약 30분, 배포 두 번.

## 언제

- admin 콘솔을 IP·VPN 이 아니라 **계정**으로 막을 때. 네트워크로만 막는 방법은 [8-S](8-S-hardening.md) — admin 이 internal 인 배포(prod)는 둘이 겹쳐 이중 보호가 된다.
- 신규 설치도 설치(§3~§5)를 마친 뒤 이 문서로 켠다 — `19` 는 살아 있는 Ingress·Cognito 를 읽으므로 설치 전에는 못 돌린다.

## 전제 3가지

- **https 주소** — admin-ui 가 `https://<host>` 여야 한다([8-H](8-H-alb-https.md), US-06). Cognito 는 localhost 가 아닌 http 콜백을 거부한다. `19` 가 먼저 확인하고 아니면 멈춘다.
- **관리자가 `ClaudeAdmin` 그룹** — Cognito 로 들어온 사람 중 이 그룹만 관리자다(그룹 이름 = values `adminApi.adminBootstrap.groups`). 추가는 [8-Y](8-Y-onboarding.md).
- **관리자가 게이트웨이 로그인을 한 번 했다** — `gateway-cli login`(VK 발급)이 DB 에 사용자를 만든다. 없으면 로그인 직후 403 `user_not_provisioned`. ⓪ 이 확인한다.

## 흐름

```text
┌─ Browser  (prod: VPN-connected PC) ──────┐
│ https://<admin-ui host>/                 │
│ (1) no admin_jwt cookie                  │
└───────────────────┬──────────────────────┘
                    │ (2) 307 /api/auth/login -> 302 Cognito
                    │
                    ▼
┌─ Cognito Hosted UI ──────────────────────┐
│ (3) sign in: email + password            │
└───────────────────┬──────────────────────┘
                    │ (4) 302 /api/auth/callback?code=...
                    │
                    ▼
┌─ admin-ui pod ───────────────────────────┐           ┌─ Cognito /oauth2/token ────┐
│ (5) code + PKCE verifier                 ├─(5) code─▶│ (5) code -> id_token       │
│ (6) Set-Cookie admin_jwt = id_token      │           │     pod -> NAT -> internet │
│                                          │◀─id_token─┤                            │
└───────────────────┬──────────────────────┘           └────────────────────────────┘
                    │ (7) 매 페이지 요청: Bearer id_token
                    │
                    ▼
┌─ admin-api pod ──────────────────────────┐
│ (8) signature check (Cognito JWKS)       │
│ (9) auth.users by sub  (none -> 403)     │
│ (10) cognito:groups has ClaudeAdmin      │
│      -> ADMIN   (else UI /403)           │
└──────────────────────────────────────────┘

(1)(2) 쿠키가 없으면 admin-ui 가 Cognito 로 보낸다 · (3) 직원과 같은 Cognito 계정
(5) 토큰 교환은 파드가 서버끼리 · (9) DB 사용자는 gateway-cli login 이 만든다
```

## 절차

⓪ 점검 → ① 콜백 → ② 로그인 켜기 → ③ 확인 → ④ dev-login 끄기. **dev-login 은 ③ 까지 켜 둔다** — Cognito 로그인이 안 되면 그 길로 다시 들어간다. 명령은 dev·prod 가 같다(환경 = `config.env` 의 `DEPLOY_ENV`, 값 = terraform output · Ingress). 아래는 dev 기준 — prod 는 [아래](#prod-에-적용).

### ⓪ 점검

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 19-admin-login.sh
```

①~④ 상태와 DB 의 관리자 등록 여부를 보여 주고 `Next` 에 다음 명령을 적는다(약 1.5분 — DB 조회용 임시 파드). 관리자 줄이 `OK … in auth.users, active` 가 아니면 그 사람이 `gateway-cli login` 을 한 번 한 뒤 다시 돌린다.

### ① Cognito 에 콜백 주소 등록

▶ **실행** · 배포 EC2 — 먼저 `--apply` 없이 돌려 붙일 내용을 본다

```bash
bash 19-admin-login.sh callback --apply
```

`terraform.tfvars` 끝에 `cognito_callback_urls` 를 붙인다 = 지금 목록 + `https://<admin-ui host>/api/auth/callback`. 지금 목록(localhost 3개)은 **직원의 `gateway-cli login` 이 쓰는 주소**라 반드시 남긴다 — 같은 앱 클라이언트다. tfvars 에 이미 그 변수가 있으면 멈추고 손으로 넣을 한 줄을 알려준다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform plan -no-color 2>/dev/null | grep -E '^\s*# .* (will be|must be)|^Plan:'
```

📋 **참고** — 기대 출력(이 2줄뿐)

```text
# module.cognito.aws_cognito_user_pool_client.cli will be updated in-place
Plan: 0 to add, 1 to change, 0 to destroy.
```

다른 줄이 있으면 apply 하지 말고 멈춘다. 2줄뿐이면:

▶ **실행** · 배포 EC2

```bash
terraform apply
```

앱 클라이언트의 콜백 목록만 바뀐다 — client ID · 직원 로그인 · 이미 받은 토큰은 그대로.

### ② Cognito 로그인 켜기

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 19-admin-login.sh login --apply
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```

`19` 가 values 의 `adminUi.env` 에 4줄(`OIDC_CLIENT_ID` · `OIDC_AUTHORIZE_URL` · `OIDC_TOKEN_URL` · `OIDC_REDIRECT_URI`)을 넣고, helm 렌더로 확인한 뒤 쓴다(백업 = `snapshots/`). values 만 바뀌므로 설치 스크립트는 admin-ui 파드만 교체한다 — 추론은 무중단.

> ⚠️ 이 배포부터 admin 주소(`/`)는 Cognito 로 간다. dev-login 은 아직 켜져 있지만 **주소를 직접 쳐야** 열린다: `https://<admin-ui host>/api/auth/dev-login` — ③ 에서 막히면 이 길로 들어간다.

### ③ 확인

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 19-admin-login.sh verify
```

📋 **참고** — 기대 출력(약 1~2분, 클러스터 안 임시 파드)

```text
OK  /  → 307 → /api/auth/login (no cookie = sent to sign in)
OK  /api/auth/login → 302 Cognito authorize (client_id, redirect_uri match)
OK  Cognito accepted the authorize request → its sign-in page (callback registered)
    /api/auth/dev-login → 200: dev-login still open (off: dev-login-off)
```

그다음 **사람이** 확인한다 — 브라우저 **시크릿 창**으로 `https://<admin-ui host>` → Cognito 로그인 화면 → 관리자 계정 → 대시보드. 여기까지 되어야 ④.

### ④ dev-login 끄기

▶ **실행** · 배포 EC2

```bash
bash 19-admin-login.sh dev-login-off --apply
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```

admin-api · admin-ui **둘 다** `DEV_LOGIN_ENABLED: "false"` 로 바꾼다(한쪽만 끄면 화면과 API 가 어긋난다). `19` 는 Cognito 로그인이 배포돼 있고 DB 에 관리자가 있을 때만 진행한다 — 아니면 모두가 잠긴다. 이번엔 admin-api·admin-ui 파드가 교체된다(추론 무중단).

> ⚠️ **이 차트에는 `global.devLoginEnabled` 단일 스위치가 있다** — `_helpers.tpl` 이 이 값으로 admin-api·admin-ui 양쪽에 `DEV_LOGIN_ENABLED` 를 주입한다. `19` 의 `dev-login-off` 가 `adminApi.env` / `adminUi.env` 에 같은 env 를 **한 번 더** 쓰면, values 에 두 군데가 생겨 helm upgrade 가 "duplicate env" 로 실패한다(atomic 롤백). 따라서 `dev-login-off` 실행 후에는 스크립트가 넣은 `adminApi.env` / `adminUi.env` 의 `DEV_LOGIN_ENABLED` 항목을 지우고 **`global.devLoginEnabled: false` 만** 남긴다. (`ADMIN_ROPC_ENABLED: "false"` 항목은 유지 — 폼 로그인 엔드포인트를 닫는 별개 스위치다.)

▶ **실행** · 배포 EC2 — 끝 확인

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 19-admin-login.sh verify
bash status.sh
```

`/api/auth/dev-login → 503: dev-login closed` 와 `status.sh` 의 `US-12` 가 `OK` 면 끝.

## prod 에 적용

dev 에서 ④ 까지 끝낸 뒤, **prod 계정의 배포 EC2** 에서 같은 순서(⓪→④)로 한다. 명령의 `dev` 는 `prod` 로(`llm-gateway-prod` · `install-eks.sh prod`). 다른 점 3가지:

- **저장소부터 최신화** — `19` 가 들어 있는 코드여야 한다. [README §3 ①](../README.md#3-적용하기-배포-ec2-에서) 대로(values 백업·복원 확인).
- **사람 확인은 VPN PC 에서** — prod admin 은 internal 이라 배포 EC2 에서도 브라우저로 닿지 않는다. `verify` 는 클러스터 안에서 도니 그대로 되지만, ③ 의 브라우저 확인은 생략하지 말고 Client VPN 에 연결된 PC 에서 한다. Cognito 로그인 화면은 인터넷으로, admin 주소는 VPN 으로 간다(split tunnel).
- **적용 시각** — ② ④ 모두 관리 화면(④ 는 VK 발급 API 도)의 파드가 교체된다. 추론은 그대로지만 사용자가 적은 시간에.

## 문제 해결

- **Cognito 화면에 `redirect_mismatch`** — ① 의 `terraform apply` 가 안 됐다. ⓪ 의 ① 이 `OK registered` 인지 본다.
- **로그인 뒤 403 `user_not_provisioned`** — 그 관리자가 DB 에 없다. 본인이 `gateway-cli login` 한 번 → 다시 로그인.
- **로그인 뒤 `/403` 화면** — 그 계정이 `ClaudeAdmin` 그룹이 아니다([8-Y](8-Y-onboarding.md)).
- **로그인은 되는데 대시보드가 비고 API 가 전부 401** — admin-api 가 쿠키 토큰의 서명을 검증하지 못하는 상태다.
  - 이 배포의 콜백은 Cognito id_token 을 `POST /v1/auth/admin-session` 으로 교환해 **내부 JWT**(`iss=ds-gateway-admin`)를 쿠키에 넣는다. 이 키는 DB seed 에 이미 있으므로 별도 등록은 필요 없다. 단, 콜백이 IdP 토큰을 쿠키에 그대로 넣는 배포(상향 코드 그대로)라면 **Cognito JWKS 를 `auth.admin_jwt_configs` 에 등록**해야 한다 — issuer = user pool issuer URL, audience = 앱 클라이언트 ID, RS256 PEM. 등록 후 admin-api 재시작(키는 시작 시에만 로드). Cognito 가 서명키를 로테이션하면 같은 절차로 다시 등록한다.
  - Cognito id_token 의 `at_hash` 클레임은 `python-jose` 가 access_token 없이는 검증하지 못해 실패한다 — `JWTVerifier.verify()` 가 `options={"verify_at_hash": False}` 를 쓰는지 확인한다(1.0.92 이상).
- **팀 리더가 로그인하면 `/403`** — Cognito id_token 에는 `role`/`team_id` 클레임이 없어 middleware 가 DEVELOPER 로 깐다. admin-session 교환(내부 JWT 에 DB 의 role/team_id 가 박힘)이 들어간 이미지(1.0.94/1.0.173 이상)인지 확인한다.
- **로그아웃 눌러도 바로 대시보드로 돌아온다** — admin 쿠키만 지우면 Cognito 세션이 살아 있어 즉시 재로그인된다. 로그아웃이 Cognito `/logout` 으로 보내는 이미지(1.0.172 이상)인지, Cognito 앱 클라이언트의 **로그아웃 URL** 에 admin 주소가 등록됐는지(`cognito_logout_urls`) 본다.

## 되돌리기

- **④ 만** — `19` 가 출력한 백업 `snapshots/<시각>-values-<env>-19-dev-login-off.bak` 을 values 로 복사 → `install-eks.sh <env>`.
- **② 까지** — `…-19-login.bak`(② 직전 상태)을 values 로 복사 → `install-eks.sh <env>`. ④ 도 함께 되돌아가고 admin-ui 는 dev 폼으로 돌아간다.
- **콜백 주소** — 남겨 둬도 해가 없다.

## 알아둘 점

- **세션 12시간** — admin 쿠키는 admin-api 가 발급한 내부 JWT(`ADMIN_UI_JWT_TTL_HOURS`, 기본 12h)다. Cognito 토큰 TTL 과 무관하다. 수명은 [8-Z](8-Z-token-ttl.md) ②.
- **로그아웃은 Cognito 세션까지 끊는다** — 로그아웃 라우트가 admin 쿠키를 지우고 Cognito `/logout` 으로 보낸다(로그아웃 URL 등록 필요 — ① 의 `cognito_logout_urls`).
- **관리자 권한은 그룹이 정한다** — `ClaudeAdmin` 에서 빼면 그 사람의 다음 토큰(최대 1시간 뒤)부터 관리자가 아니다.
