# 8-Y. 직원 온보딩 — Cognito 사용자 추가

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-Y**

§3-8 은 **관리자 한 명**만 만든다. 직원이 [§6](../install-guide.md#6-클라이언트-설치--claude-code-awsome-gateway-cli) 의 `gateway-cli login` 을 하려면, 그 전에 **관리자가 직원을 Cognito 에 미리 등록**해둬야 한다. 방법은 두 가지.

**공통 — 어느 그룹에 넣나**

- **팀 그룹**(`Claude_default-department_default-team`) = **필수.** 없으면 로그인은 되지만 VK 발급이 **403**. 이 배포는 팀이 하나뿐이라 전원 이 그룹에 넣는다(§3-2 `cognito_groups`).
- `ClaudeAdmin` = **관리자에게만.** admin-ui(`/models`·예산 등)를 쓸 사람만. 일반 직원은 **넣지 않는다.** Cognito 로그인([8-L](8-L-admin-login.md))을 켠 배포에선 새 관리자가 `gateway-cli login` 을 한 번 해야 admin-ui 에 들어간다(안 하면 403 `user_not_provisioned`).

**관리자 사용량·예산을 따로 떼고 싶다면 — 관리자 팀을 하나 만든다**

기본은 관리자도 **자기 원래 팀 그룹 + `ClaudeAdmin`** 두 개다(관리 권한은 `ClaudeAdmin` 이 준다). 관리자의 사용량·한도를 따로 보고 싶을 때만 팀을 나눈다.

1. 팀 그룹 이름을 정한다 — 예 `Claude_platform_admins`(밑줄이 구분자이므로 부서·팀 이름에는 하이픈만).
2. 아래 [새 팀(부서) 추가](#새-팀부서-추가--그룹-생성만으로는-안-된다) 의 ①~⑤ 를 그대로 밟는다 — tfvars `cognito_groups` 에 추가 → `terraform apply` → 사용자 배정 → 첫 로그인(팀 자동 생성) → **예산 부여**.
3. 관리자에게 **그 팀 그룹과 `ClaudeAdmin` 을 둘 다** 넣고, 원래 팀 그룹에서는 뺀다.

▶ **실행** · 배포 EC2 — 위 ②(`terraform apply`)가 끝난 뒤

```bash
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" \
  --username "$EMAIL" --group-name "Claude_platform_admins"
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" \
  --username "$EMAIL" --group-name "ClaudeAdmin"
aws cognito-idp admin-remove-user-from-group --user-pool-id "$POOL_ID" \
  --username "$EMAIL" --group-name "Claude_default-department_default-team"
```

> ⚠️ **팀 그룹은 한 사람당 하나만.** 팀 그룹 두 개에 들어 있으면 토큰의 그룹 중 먼저 맞는 것 하나가 팀이 되는데 그 순서는 보장되지 않는다 — 어느 팀으로 집계될지 예측할 수 없다.
>
> ⚠️ **`Claude_Admin` 처럼 밑줄을 넣은 이름을 관리 권한용으로 만들지 말 것.** 그건 "Admin" 이라는 **팀**으로 해석돼 예산 $0 짜리 팀만 하나 생기고 관리 권한은 주지 않는다. 관리 권한은 `ClaudeAdmin`(밑줄 없음) 하나뿐이다.
>
> 새 팀이라 예산이 $0 으로 생성된다 — ⑤ 를 건너뛰면 그 관리자의 Claude Code 요청이 전부 429 가 된다(관리 화면만 쓰면 무관). 관리 화면 로그인 자체를 켜는 절차는 [8-L](8-L-admin-login.md).

## 방법 A — admin-ui 화면 (권장, 소수)

admin-ui(`/models` 와 같은 사이트)의 **사용자 관리** 화면에서 초대·그룹 배정. 관리자 로그인 + `inbound-cidrs` 안에서. 몇 명이면 이게 제일 쉽다.

## 방법 B — CLI (대량·자동화)

§3-8 과 같은 명령이다. 직원 이메일만 바꾸고 `ClaudeAdmin` **줄은 뺀다**:

▶ **실행** · 배포 EC2

```bash
POOL_ID=$(cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev \
  && terraform output -raw cognito_user_pool_id)
EMAIL="employee@your-org.com"                 # ← 직원 이메일
TEMP_PW='<임시비번 12자+ 대소문자·숫자·특수문자>'   # 직원이 첫 로그인 때 변경

aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$EMAIL" \
  --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --temporary-password "$TEMP_PW" --message-action SUPPRESS
# 팀 그룹만 (관리자 아님 → ClaudeAdmin 안 넣음)
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" --username "$EMAIL" \
  --group-name "Claude_default-department_default-team"
```

> `--message-action SUPPRESS` 는 Cognito 기본 초대 메일을 **안 보낸다**(SES 미설정 배포라). 이메일·임시비번을 관리자가 직원에게 **직접 전달**한다. 직원은 그걸로 §6 `gateway-cli login` 팝업에 로그인 → 첫 로그인 시 새 비번으로 변경.
>
> ⚠️ 위 방법은 **기존 팀에 사용자**를 넣는 것. **새 팀**을 나눌 거면 그룹 생성만으로 안 되니 아래 **새 팀 추가** 절을 본다. 이 배포는 팀 하나라 해당 없음.

## 새 팀(부서) 추가 — 그룹 생성만으로는 안 된다

**새 팀**을 하나 만들려면 Cognito 그룹 생성 하나로 안 끝난다 — **이름 규칙 → terraform 그룹 → (멤버 첫 로그인) → 예산** 을 다 밟아야 admin-ui 에서 실제로 쓸 수 있다.

```text
  ①  이름 정하기 — Claude_<부서>_<팀>   ("Claude_" 없으면 매핑 실패)
        └ 예) Claude_AI-department_agent-team → 부서 AI-department · 팀 agent-team
        └ 밑줄 _ = 구분자 → 부서·팀 이름엔 하이픈만
                    │
                    ▼
  ②  그룹 생성   — tfvars cognito_groups 에 추가 → terraform apply
        └ 콘솔 수동 생성 X — 이 목록에서만 관리된다
                    │
                    ▼
  ③  사용자 배정 — 방법 A(admin-ui) / B(CLI) 로 그 그룹에 add-user
                    │
                    ▼
  ④  첫 로그인   — ⚡ 이 순간 팀이 DB 에 "자동 생성"(lazy) → admin-ui 에 등장
        └ 단, 예산 $0 · HARD_BLOCK 으로 생성 → 그 팀 요청 전부 429 (로그 없음)
                    │
                    ▼
  ⑤  예산 부여   — admin-ui /budgets 에서 그 팀에 한도 설정 → ✅ 사용 가능

  ──────────────────────────────────────────────────────────────
  ✕ 흔한 실패
        · 이름에 Claude_ 없음  → 로그인 시 "no group mapping found"
        · ⑤ 예산을 건너뜀      → "로그인은 되는데 그 팀 전부 429"
```

**②의 terraform 그룹** — tfvars 에 한 줄 더하고 apply:

```hcl
# §3-2 terraform.tfvars — cognito_groups (그룹은 이 목록에서만 생성·관리)
cognito_groups = [
  "Claude_default-department_default-team",
  "Claude_AI-department_agent-team",     # ← 새 팀 (밑줄=구분자, 이름엔 하이픈)
]
```

> 🔴 **가장 흔한 함정 = ⑤ 예산 누락.** 그룹만 만들고 예산을 안 주면 "로그인·토큰은 정상인데 그 팀 요청 전부 429, 로그도 없음" — 자동 생성 팀이 `$0`·`HARD_BLOCK` 이라 그렇다(§6 도입부 예산 🔴와 같은 원인). 근거: `oidc_service.py` 의 `_parse_group`(이름 매핑) · `_get_or_create_team`(첫 로그인 시 팀 + `$0 HARD_BLOCK` 자동 생성).
