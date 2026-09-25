# 8-L. admin 콘솔 Cognito 로그인 (US-12)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-L**

관리 화면(admin-ui)에 **Cognito 로그인**을 켠다. 지금은 개발용 로그인(dev-login)이 켜져 있어 **admin 주소에 닿는 사람은 누구나 관리자**다 — 역할만 고르면 서명 없는 관리자 토큰이 나온다. 로그인 코드는 이미 배포된 이미지에 있고(US-10), 켜는 데 필요한 것은 **설정 3가지**다: Cognito 콜백 주소 1개 · admin-ui 설정 4줄 · dev-login 끄기. 셋 다 `update-scripts/19-admin-login.sh` 가 계산해서 넣는다. 약 30분, 배포 두 번.

**이 문서가 다루는 두 배포** — 절차(⓪~④)는 같고, 다른 점은 마지막 절에 모았다.

- **Cognito 단독** — 사용자 계정이 Cognito 사용자 풀에 있다. 이 문서를 위에서 아래로 그대로 따라 한다.
- **사내 IdP(ADFS 등) + Cognito** — 사용자는 사내 IdP 로 로그인하고 그 뒤에 Cognito 가 있다. 같은 절차를 따르되 [사내 IdP 연동 배포](#사내-idp-연동-배포-adfs-등)를 먼저 읽는다.

**함께 보기** — [8-Y 직원 온보딩](8-Y-onboarding.md): Cognito 사용자 추가와 그룹 배정(`ClaudeAdmin` 부여 · 관리자 팀 분리 · 새 팀 추가) · [8-S 보안 하드닝](8-S-hardening.md): admin 콘솔을 네트워크로 좁히기 · [8-Z 토큰 TTL](8-Z-token-ttl.md): 로그인·VK 수명.

## 언제

- admin 콘솔을 IP·VPN 이 아니라 **계정**으로 막을 때. 네트워크로만 막는 방법은 [8-S](8-S-hardening.md) — admin 이 internal 인 배포(prod)는 둘이 겹쳐 이중 보호가 된다.
- 신규 설치도 설치(§3~§5)를 마친 뒤 이 문서로 켠다 — `19` 는 살아 있는 Ingress·Cognito 를 읽으므로 설치 전에는 못 돌린다.

## 전제 4가지

- **https 주소** — admin-ui 가 `https://<host>` 여야 한다([8-H](8-H-alb-https.md), US-06). Cognito 는 localhost 가 아닌 http 콜백을 거부한다. `19` 가 먼저 확인하고 아니면 멈춘다.
- **관리자가 `ClaudeAdmin` 그룹** — Cognito 로 들어온 사람 중 이 그룹만 관리자다(그룹 이름 = values `adminApi.adminBootstrap.groups`). 추가는 [8-Y](8-Y-onboarding.md). 사내 IdP(ADFS 등)를 연동한 배포는 [아래 절](#사내-idp-연동-배포-adfs-등)을 먼저 읽는다.
- **관리자가 게이트웨이 로그인을 한 번 했다** — `gateway-cli login`(VK 발급)이 DB 에 사용자를 만든다. 없으면 로그인 직후 403 `user_not_provisioned`. ⓪ 이 확인한다.
- **admin-api 가 `1.0.69-idpjwks` 이상** — 관리 화면 로그인 토큰을 IdP 의 JWKS 로 검증하는 버전이다. 그 전 버전은 정적 키로만 검증해, 설치 직후에는 로그인은 되는데 **모든 API 호출이 500** 이 된다. ⓪ 이 버전을 보여 주고, 낮으면 ② 에서 함께 올린다. 바꾼 이유는 [아래](#왜-admin-api-가-idp-jwks-로-검증하나).

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

- 사내 IdP 연동 배포면 (3) 의 화면이 그 IdP 다 — 나머지 단계는 같다.
- `code` = Cognito 가 로그인 성공 뒤 콜백 주소에 붙여 주는 **일회용 인가 코드**다. 그 자체로는 권한이 없고, 파드가 PKCE verifier 와 함께 제시해 `id_token` 으로 바꾼다.

## 절차

⓪ 최신화·점검 → ① 콜백 → ② 로그인 켜기 → ③ 확인 → ④ dev-login 끄기. **dev-login 은 ③ 까지 켜 둔다** — Cognito 로그인이 안 되면 그 길로 다시 들어간다. 명령은 dev·prod 가 같다(환경 = `config.env` 의 `DEPLOY_ENV`, 값 = terraform output · Ingress). 아래는 dev 기준 — prod 는 [아래](#prod-에-적용).

### ⓪ 저장소 최신화와 점검

`19-admin-login.sh` 가 들어 있는 코드여야 한다. 리베이스 브랜치라 `git pull` 이 아니고, values 는 이 EC2 유일본이라 백업·복원이 핵심이다 — `values restored OK` 를 확인한다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

> ⚠️ `reset --hard` 는 저장소판 `.terraform.lock.hcl` 도 되돌린다. 그 lock 은 OpenTofu 레지스트리 주소라 EC2 의 terraform 이 받아 둔 플러그인과 맞지 않아, 바로 아래 점검이 `ABORTED: terraform output failed …` 로 멈춘다(2026-09-25 dev 실측). 그때만 아래를 한 번 실행한다 — 플러그인만 다시 받고 **state 는 건드리지 않는다**(`terraform apply` 금지). 이후 lock 파일이 `git status` 에 `M` 으로 남는 것은 정상이고 커밋하지 않는다.

▶ **실행 (해당할 때만)** · 배포 EC2 — prod 는 `llm-gateway-prod`

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform init
```

`Terraform has been successfully initialized!` 를 확인하고 점검으로 돌아온다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 19-admin-login.sh
```

①~④ 상태 · admin-api 버전 · DB 의 관리자 등록 여부를 보여 주고 `Next` 에 다음 명령을 적는다(약 1.5분 — DB 조회용 임시 파드). 관리자 줄이 `OK … in auth.users, active` 가 아니면 그 사람이 `gateway-cli login` 을 한 번 한 뒤 다시 돌린다. 관리자 확인은 Cognito 그룹 멤버로 먼저 보고, 멤버가 없으면 DB 의 활성 ADMIN 사용자로 판정한다.

**중간부터 이어서 할 때** — 이미 끝낸 단계는 `OK` 로 나온다. `Next` 가 가리키는 단계부터 이어서 한다(예: ① 을 끝냈으면 ② 부터, admin-api 버전이 `XX` 면 ② 의 admin-api 갱신부터).

### ① Cognito 에 콜백 주소 등록

▶ **실행** · 배포 EC2 — 먼저 `--apply` 없이 돌려 붙일 내용을 본다

```bash
bash 19-admin-login.sh callback --apply
```

`terraform.tfvars` 끝에 `cognito_callback_urls` 를 붙인다 = 지금 목록 + `https://<admin-ui host>/api/auth/callback`. 지금 목록(localhost 3개)은 **직원의 `gateway-cli login` 이 쓰는 주소**라 반드시 남긴다 — 같은 앱 클라이언트다. tfvars 에 이미 그 변수가 있으면 멈추고 손으로 넣을 한 줄을 알려준다. 이미 등록된 배포면 `callback already registered … nothing to do` 로 끝난다 — 그때는 아래 terraform 단계를 건너뛰고 ② 로 간다.

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

**admin-api 가 `1.0.69-idpjwks` 미만이면 먼저 올린다** — ⓪ 의 `admin-api version` 이 `XX` 일 때만. 태그를 저장소 값으로 맞추고 그 태그로 이미지를 빌드한다. 아래 설치 스크립트 한 번에 admin-ui 설정과 함께 나간다.

▶ **실행 (해당할 때만)** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 13-bump-image-tags.sh dev --apply
cd ~/awsome-ai-gateway && bash deployment/scripts/rebuild-image.sh admin-api dev
```

`13` 은 쓰기 전에 서비스별 표를 보여 주고 `yes` 를 묻는다. **admin-api 만** `→ 1.0.69-idpjwks` 로 바뀌는지 본다(scheduler 는 같은 이미지라 따라간다). 다른 서비스가 바뀜으로 나오면 `yes` 하지 않는다. 빌드는 수 분.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 19-admin-login.sh login --apply
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```

`19` 가 values 의 `adminUi.env` 에 4줄(`OIDC_CLIENT_ID` · `OIDC_AUTHORIZE_URL` · `OIDC_TOKEN_URL` · `OIDC_REDIRECT_URI`)을 넣고, helm 렌더로 확인한 뒤 쓴다(백업 = `snapshots/`). 설치 스크립트는 admin-ui 파드(admin-api 를 올렸다면 admin-api·scheduler 도)를 교체한다 — 추론은 무중단. `19` 는 다음 배포에 나갈 admin-api 태그가 `1.0.69-idpjwks` 미만이거나 그 이미지가 ECR 에 없으면 `--apply` 를 거부한다. ② 를 이미 한 번 했다면(4줄이 이미 있음) `19` 는 `nothing to write` 로 끝난다 — 정상이니 이어서 설치 스크립트를 돌린다.

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

그다음 **사람이** 확인한다 — 브라우저 **시크릿 창**으로 `https://<admin-ui host>` → Cognito 로그인 화면 → 관리자 계정 → 대시보드 **숫자가 채워지고 모니터링 화면이 열린다**. 여기까지 되어야 ④.

### ④ dev-login 끄기

▶ **실행** · 배포 EC2

```bash
bash 19-admin-login.sh dev-login-off --apply
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```

admin-api · admin-ui **둘 다** `DEV_LOGIN_ENABLED: "false"` 로 바꾼다(한쪽만 끄면 화면과 API 가 어긋난다). `19` 는 Cognito 로그인이 배포돼 있고 DB 에 관리자가 있을 때만 진행한다 — 아니면 모두가 잠긴다. 관리자 확인이 0 명으로 나오면 [사내 IdP 절](#사내-idp-연동-배포-adfs-등)의 「그 밖에」를 본다. 이번엔 admin-api·admin-ui 파드가 교체된다(추론 무중단).

▶ **실행** · 배포 EC2 — 끝 확인

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 19-admin-login.sh verify
bash status.sh
```

`/api/auth/dev-login → 503: dev-login closed` 와 `status.sh` 의 `US-12` 가 `OK` 면 끝.

## prod 에 적용

dev 에서 ④ 까지 끝낸 뒤, **prod 계정의 배포 EC2** 에서 같은 순서(⓪→④)로 한다. 명령의 `dev` 는 `prod` 로(`llm-gateway-prod` · `install-eks.sh prod`). 다른 점 3가지:

- **저장소 최신화는 prod values 로** — ⓪ 의 최신화 블록에서 `V` 를 `values-eks-fargate-prod.yaml` 로 바꿔 실행한다(`values restored OK` 확인).
- **사람 확인은 VPN PC 에서** — prod admin 은 internal 이라 배포 EC2 에서도 브라우저로 닿지 않는다. `verify` 는 클러스터 안에서 도니 그대로 되지만, ③ 의 브라우저 확인은 생략하지 말고 Client VPN 에 연결된 PC 에서 한다. Cognito 로그인 화면은 인터넷으로, admin 주소는 VPN 으로 간다(split tunnel).
- **admin-api 이미지도 prod 에서 빌드** — ECR 은 계정마다 따로다. ② 의 admin-api 갱신을 prod EC2 에서 `prod` 로 한다.
- **적용 시각** — ② ④ 모두 관리 화면(④ 는 VK 발급 API 도)의 파드가 교체된다. 추론은 그대로지만 사용자가 적은 시간에.

## 사내 IdP 연동 배포 (ADFS 등)

> 이 절은 **사내 IdP(ADFS 등)를 Cognito 사용자 풀에 연동한 배포**를 위한 것이다. Cognito 단독 배포는 위 ⓪~④ 로 끝난다.

**무엇이 달라지나** — 사람이 보는 로그인 화면만 사내 IdP 가 된다. admin-ui 가 부르는 주소(Cognito Hosted UI), 토큰을 발행·검증하는 주체(Cognito), `19` 가 넣는 값 4줄은 그대로다.

**연동 형태부터 가른다 — 그룹이 어디서 오는지가 다르다.**

- **링크형** — 사용자를 Cognito 에 미리 만들어 두고(`admin-create-user`), 외부 로그인이 들어오면 PreSignUp 트리거가 같은 사람을 찾아 연결한다(`admin_link_provider_for_user`). 로그인 주체가 그 네이티브 사용자이므로 **그룹은 Cognito 그룹 그대로**다 — 이 배포의 US-12 는 Cognito 단독과 다르지 않다. 사전 등록되지 않은 사람을 PreSignUp 에서 거부하면 그 자체가 화이트리스트가 된다.
- **JIT 형** — 링크 없이 외부 로그인마다 federated 사용자가 만들어진다. 이때는 **그룹이 IdP 클레임으로 와야** 하고, 클레임 이름이 `adminApi.oidc.groupsClaim` 과, 값이 `adminApi.adminBootstrap.groups`·팀 그룹 규칙과 맞아야 한다.

```text
┌─ Corporate IdP  (ADFS, SAML) ──┐            ┌─ Amazon Cognito user pool ──────────┐
│ signs in: kim@corp.example     │            │ PreSignUp: link to pre-created user │
│ no per-app AD group needed     ├─SAML──────▶│ (not pre-created -> denied)         │
└────────────────────────────────┘            │ issues id_token for that user       │
                                              └───────────────┬─────────────────────┘
                                                              │ id_token
                                                              ▼
┌─ id_token  (what the gateway reads) ──────────────────────────────────────────────┐
│ sub    = 8f3a...   -> auth.users row (created by gateway-cli login)               │
│ email  = kim@corp.example                                                         │
│ groups = ["Claude_platform", "ClaudeAdmin"]                                       │
└─────────────┬───────────────────────────────────────────────┬─────────────────────┘
              │ Claude_<team>                                 │ ClaudeAdmin
              ▼                                               ▼
┌─ team  =  budget / rate limit ───────┐     ┌─ admin console ──────────────────────┐
│ VK issue needs it (else 403)         │     │ admin-ui : ADMIN, else /403          │
│ auto-created at $0                   │     │ admin-api: ADMIN                     │
└──────────────────────────────────────┘     └──────────────────────────────────────┘

링크형 — 외부 로그인이 사전 등록된 Cognito 사용자에 연결된다
  그래서 groups 는 그 사용자의 Cognito 그룹 그대로다
Claude_<team> = 팀(예산·한도) · ClaudeAdmin = 관리 화면 권한 · 관리자는 둘 다
JIT 형(링크 없이 federated 사용자 생성)이면 groups 가 IdP 클레임으로 와야 한다
```

- **팀 그룹** — 접두사 `Claude_`(`Claude_팀` · `Claude_부서_팀`, values `adminApi.oidc.groupPrefix`). 팀, 곧 예산·한도를 정한다. VK 발급에 필수이고, DB 에 없는 팀이면 예산 $0 으로 자동 생성된다. 팀 그룹이 하나도 없으면 발급이 거부된다(`adminApi.oidc.rejectUnmatchedGroups` 기본 `true`).
- **관리자 그룹** — `ClaudeAdmin`(values `adminApi.adminBootstrap.groups`). 관리 화면 권한만 정한다. 밑줄이 없어 팀 이름 규칙에 걸리지 않으므로 팀 판정에서는 무시된다. 반대로 이름에 밑줄을 넣으면(`Claude_Admin`) 팀으로 해석돼 권한을 주지 않는다.
- **관리자는 둘 다 필요하다** — 예: `Claude_platform`(팀) + `ClaudeAdmin`(권한). 관리자 사용량·예산을 따로 떼려면 관리자 팀 그룹을 만들어 `ClaudeAdmin` 과 함께 넣는다 — 절차는 [8-Y](8-Y-onboarding.md).

**왜 관리자 그룹 하나가 성패인가**

- admin-api(서버)는 관대하다 — 그룹(`ADMIN_GROUPS`) · 이메일(`ADMIN_EMAILS`) · DB 역할 중 하나만 ADMIN 이면 통과시킨다.
- admin-ui(화면)는 **그룹만** 본다 — `OIDC_GROUPS_CLAIM` 안의 값이 `ADMIN_GROUPS` 와 정확히 일치해야 한다. 못 찾으면 모든 페이지가 `/403` 이다.
- 그래서 **이메일만 등록하면 API 는 되는데 화면이 안 열린다.** 두 값은 차트가 admin-api·admin-ui 에 같이 주입하므로 한쪽만 어긋날 일은 없다.

**시작 전 판정 — 직원의 `gateway-cli login` 이 알려 준다.** VK 발급은 관리 화면과 같은 그룹 클레임을 읽어 팀을 정한다. 팀이 제대로 붙는 배포면 관리자 그룹도 같은 길로 온다.

- **VK 를 받아 쓰고 있고 팀도 그룹대로 붙어 있다** → 그대로 진행해도 된다.
- **로그인은 되는데 VK 발급이 `no_matching_team_group` 으로 막힌다** → 그룹이 안 오거나 이름 규칙이 다르다. 그것부터 고친다.
- **모두 `Default Team` 으로 떨어진다** → `rejectUnmatchedGroups` 가 `false` 인 배포라 그룹이 비어도 통과한 것이다. 증거가 못 되니 클레임부터 확인한다.

**그 밖에**

- 팀 그룹 없는 사용자의 VK 발급 거부를 **접근 통제로 쓰는 배포**는 `rejectUnmatchedGroups` 를 `true`(기본값)로 유지한다. `false` 면 그룹 없는 사용자도 Default Team 으로 통과해 그 통제가 사라진다.
- Cognito 에 IdP 가 둘 이상이면 로그인 전에 선택 화면이 먼저 뜬다. 바로 사내 IdP 로 보내려면 `19` 가 넣은 `OIDC_AUTHORIZE_URL` 끝에 `?identity_provider=<IdP 이름>` 을 붙인다(admin-ui 는 그 쿼리를 보존한다).
- **④ 의 관리자 확인** — `19` 는 Cognito 그룹 멤버를 먼저 보고, 멤버가 없으면 `auth.users` 의 활성 ADMIN 행으로 판정한다(JIT 형이 이 경로다). 둘 다 0 이면 ④ 를 거부한다 — 관리자가 `gateway-cli login` 을 한 번 해 DB 에 등록되게 한 뒤 다시 한다.
- **토큰 대상(`aud`) 검사** — 관리 화면 토큰은 서명·issuer·만료로 검증하고, `aud` 는 values `adminApi.oidc.audience` 가 있을 때만 본다(Cognito 배포는 비워 둔다 — VK 발급과 같은 수준). 같은 사용자 풀을 다른 앱이 함께 쓰는 배포라면, 그 앱용으로 발급된 같은 사람의 토큰도 받아들여진다는 뜻이다(DB 등록·관리자 그룹 판정은 그대로 적용). 배경은 [아래 절](#왜-admin-api-가-idp-jwks-로-검증하나).
- ③ 에서 `/403` 이면 그룹이 안 온 것이다. **④ 로 가지 말 것**(전원 잠김). dev-login 이 아직 열려 있으니 `https://<admin-ui host>/api/auth/dev-login` 으로 들어가 매핑을 고치고 ③ 을 다시 한다.

## 문제 해결

- **Cognito 화면에 `redirect_mismatch`** — ① 의 `terraform apply` 가 안 됐다. ⓪ 의 ① 이 `OK registered` 인지 본다.
- **로그인 뒤 403 `user_not_provisioned`** — 그 관리자가 DB 에 없다. 본인이 `gateway-cli login` 한 번 → 다시 로그인.
- **로그인 뒤 `/403` 화면** — 그 계정이 `ClaudeAdmin` 그룹이 아니다([8-Y](8-Y-onboarding.md)). 사내 IdP 연동 배포면 그룹이 토큰에 안 실린 것일 수 있다 — [사내 IdP 절](#사내-idp-연동-배포-adfs-등).
- **로그인은 되는데 대시보드 숫자가 비고 모니터링이 500** — admin-api 가 `1.0.69-idpjwks` 미만이다(admin-api 로그에 `JWKError … Invalid symbol 95`). ② 의 admin-api 갱신을 하고 설치 스크립트를 다시 돌린다.

## 되돌리기

- **④ 만** — `19` 가 출력한 백업 `snapshots/<시각>-values-<env>-19-dev-login-off.bak` 을 values 로 복사 → `install-eks.sh <env>`.
- **② 까지** — `…-19-login.bak`(② 직전 상태)을 values 로 복사 → `install-eks.sh <env>`. ④ 도 함께 되돌아가고 admin-ui 는 dev 폼으로 돌아간다.
- **콜백 주소** — 남겨 둬도 해가 없다.

## 왜 admin-api 가 IdP JWKS 로 검증하나

관리 화면에 Cognito 로그인을 켜면 admin-ui 는 IdP 가 발급한 `id_token` 을 그대로 쿠키에 담고, 화면이 admin-api 를 부를 때마다 그 토큰을 보낸다. `1.0.69-idpjwks` 전의 admin-api 는 이 토큰을 **`auth.admin_jwt_configs` 에 등록된 정적 공개키로만** 검증했다. 그 방식에는 네 가지 문제가 있었다.

- **설치만으로는 동작하지 않았다** — 그 테이블에는 시드 자리표시 행(`REPLACE_WITH_ACTUAL_RS256_PUBLIC_KEY`)뿐이고, IdP 공개키를 PEM 으로 바꿔 넣는 운영자 작업은 어디에도 문서화돼 있지 않았다. 그래서 로그인은 되는데 모든 API 호출이 실패했다.
- **인증 실패가 서버 오류로 보였다** — 자리표시 PEM 으로 키를 만들다 난 `JWKError` 를 코드가 잡지 않아, 401 이어야 할 것이 500 으로 나갔다.
- **키 교체에 약하다** — IdP 는 서명키를 주기적으로 교체한다. 정적 PEM 은 교체되는 순간 로그인이 끊기고, 사람이 다시 넣어야 한다.
- **같은 토큰을 두 방식으로 검증하고 있었다** — admin-api 는 VK 발급(`/v1/auth/exchange`)에서 이미 같은 IdP 의 토큰을 JWKS 로 검증한다(`OIDCVerifier` — issuer 의 discovery 문서에서 JWKS 주소를 찾아 키를 받고, `kid` 로 고르고, 모르는 `kid` 면 다시 받는다). 관리 화면만 다른 방식이었다.

그래서 `1.0.69-idpjwks` 부터는 토큰의 `iss` 가 설정된 OIDC issuer(values `adminApi.oidc.issuerUrl`)이면 `OIDCVerifier` 로 검증한다. 그 밖의 토큰(개발용 로그인 · 서비스 토큰 · 내부 admin JWT)은 예전과 같다. 정적 키가 깨져 있어도 이제 500 이 아니라 401 이다. 검증 항목은 서명 · issuer · 만료이고, `aud` 는 `adminApi.oidc.audience` 가 있을 때만 본다 — VK 발급과 같은 수준이다.

## 알아둘 점

- **세션 1시간** — admin 쿠키는 Cognito id_token 이라 1시간 뒤 만료되고 로그인 화면으로 돌아간다. 수명은 [8-Z](8-Z-token-ttl.md) ②.
- **로그아웃은 admin 쿠키만 지운다** — Cognito 쪽 로그인 세션은 별개다. 다른 계정으로 바꿀 때는 시크릿 창으로.
- **관리자 권한은 그룹이 정한다** — `ClaudeAdmin` 에서 빼면 그 사람의 다음 토큰(최대 1시간 뒤)부터 관리자가 아니다.
