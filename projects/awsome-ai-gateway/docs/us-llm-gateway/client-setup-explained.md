# 클라이언트 설치는 어떻게 동작하나 (초보자용)

> 이 문서는 [install-guide.md §6](install-guide.md#6-클라이언트-설치--claude-code-awsome-gateway-cli) 를 **개념부터** 이해하려는 사람을 위한 것이다. 설치 명령은 §6 에 있고(OS별), 여기서는 *"왜 이렇게 하는지 · 인증이 어떻게 흐르는지"* 만 그림으로 설명한다.

---

## 한 문장

직원 PC 의 Claude Code 가 **개인 Anthropic 계정 대신 우리 게이트웨이**로 가게 만든다. 그러면 예산·사용량·모델이 회사 관리 아래로 들어온다.

---

## 무엇을 설치하나 — 두 조각

`gateway-cli` 를 깔면 실행 파일이 두 개 생긴다. 역할이 다르다:

| | 언제 도나 | 하는 일 |
|---|---|---|
| **gateway-cli** | **설치할 때 딱 1회** | 로그인(`login`) + Claude Code 설정(`setup`) |
| **api-key-helper** | **Claude Code 가 질문할 때마다** | 게이트웨이 열쇠(Virtual Key)를 자동 발급 |

Claude Code 는 `api-key-helper` 를 **매 요청 직전에 자동 실행**해서 열쇠를 받아 쓴다. 직원은 이걸 의식하지 못한다.

---

## 흐름 ① — 설치 (직원 PC 에서 딱 한 번)

```
직원 PC
  │
  │  [1] gateway-cli login
  │        └ 브라우저 열림 → Cognito 로그인 팝업
  │             · 관리자: §3-8 에서 만든 이메일 + 비번
  │                       (첫 로그인 때 임시비번 → 새 비번으로 변경 강제)
  │             · 직원:   각자 Cognito 계정 (관리자가 미리 온보딩 — 방법은 operations.md 8-Y)
  │             ⚠️ AWS 콘솔/CLI 계정이 아니다 — Cognito 는 별개 시스템
  │        └ 로그인 성공 → OIDC 토큰을 PC 에 저장
  │                         (~/.gateway-cli/oidc-tokens.json)
  ▼
  │  [2] gateway-cli setup   (관리자 권한 필요)
  │        └ Claude Code 의 "관리 설정" 파일에 아래를 박는다:
  │             · ANTHROPIC_BASE_URL   = 게이트웨이 주소   (어디로 갈지)
  │             · apiKeyHelper         = api-key-helper   (열쇠는 이걸로 받아라)
  │             · OIDC_ISSUER/CLIENT   = 로그인 정보       (열쇠 받을 때 필요)
  ▼
설치 끝. 이제 Claude Code 는 개인 계정이 아니라 게이트웨이를 본다.
```

- **[1] login** = "**나는 회사 직원이다**" 를 한 번 증명(브라우저 로그인). 그 증거(OIDC 토큰)를 PC 에 저장해둔다. 관리자는 §3-8 계정으로, 직원은 각자 계정으로 — 직원 계정은 **관리자가 미리 Cognito 에 등록**해둬야 한다([operations.md 8-Y 직원 온보딩](ops/8-Y-onboarding.md)).
- **[2] setup** = Claude Code 에게 "**게이트웨이로 가라 + 열쇠는 helper 로 받아라**" 를 알려준다. 관리 설정(managed-settings)은 최상위 우선순위라, 직원이 다른 설정을 해도 이게 이긴다.

> 🔴 **왜 관리자 권한?** setup 이 쓰는 관리 설정 파일은 시스템 폴더에 있다(OS 마다 위치 다름 — §6 참조). 그래서 sudo(Mac/Linux)·관리자 PowerShell(Windows)이 필요하다.

---

## 인증은 어떻게 흐르나 — 세 주체, 두 번의 증명

핵심: **아무도 서로를 처음부터 믿지 않는다.** 직원은 Cognito 에게 신원을 증명하고(1단계), admin-api 는 그 신원을 검증한 뒤에야 게이트웨이 열쇠(VK)를 내주고(2단계), gateway-proxy 는 그 열쇠를 검증한 뒤에야 Bedrock 을 부른다(3단계). **증명이 두 번**(신원 증명 → 열쇠 검증) 일어난다.

```
   세 주체                    무엇을 하나
  ──────────────────────────────────────────────────────────────
   Cognito         신원 발급자(IdP). "이 사람 맞다" 는 서명된 토큰을 준다.
   admin-api       신원 검증 + 열쇠 발급자. Cognito 서명을 확인하고 VK 를 준다.
   gateway-proxy   열쇠 검증 + 추론. VK 를 확인하고 Bedrock 을 부른다.
  ──────────────────────────────────────────────────────────────
   Cognito 와 admin-api 는 서로 직접 통신하지 않는다 —
   admin-api 는 Cognito 의 공개키(JWKS)만 미리 받아 서명을 로컬 검증한다.


  ═══ 1단계 · 신원 증명 (login, 브라우저) — 직원이 Cognito 에게 ═══

   직원 PC ──①── Cognito 로그인 페이지 (PKCE)
        │         email + 비번 입력
        └──②── Cognito 가 서명된 토큰 발급
                 · id_token      = 서명된 신분증 (email·groups 담김)
                 · refresh_token = 재발급용
               → PC 에 저장 (oidc-tokens.json)

   ▶ 직원은 "내가 누구인지" 증거(id_token)를 쥐었다. 아직 게이트웨이 열쇠는 없다.


  ═══ 2단계 · 열쇠 발급 (요청마다, 자동) — admin-api 가 검증 후 ═══

   Claude Code ──③── api-key-helper 실행
        │            id_token 을 admin-api 로 보냄
        │            POST /v1/auth/exchange  (Bearer id_token)
        ▼
   admin-api ──④── id_token 서명 검증
        │           · Cognito 공개키(JWKS)로 서명 확인 → 위조 아닌가
        │           · issuer·만료·audience claim 확인
        │           · email·group 으로 사용자·팀 조회
        └──⑤── 통과 → Virtual Key(vk-...) 발급
                 · 이 사용자 전용, 짧은 수명, AES-256-GCM 암호화 저장
                 · 사용량·예산이 이 VK 에 귀속
               → helper 가 받아 캐시 (vk-cache.json)

   ▶ 직원은 게이트웨이 열쇠(VK)를 쥐었다. Bedrock 키가 아니라 게이트웨이 발급 임시 열쇠다.


  ═══ 3단계 · 열쇠 사용 (추론) — gateway-proxy 가 검증 후 ═══

   Claude Code ──⑥── 게이트웨이로 요청
        │            Authorization: Bearer vk-...
        ▼
   gateway-proxy ──⑦── VK 검증 + 미들웨어 통과
        │              auth → rate limit → budget → (통과하면)
        └──⑧── IRSA 로 Bedrock 호출 → 답변

   ▶ Bedrock 자격증명은 직원에게 없다. gateway-proxy 의 IRSA(파드 역할)뿐.
     직원은 끝까지 VK 만 쥔다.
```

**관리자가 이 그림에서 가져갈 것**:

- **증명이 두 번**: ① 직원→Cognito(신원), ⑦ VK→gateway-proxy(열쇠). 한쪽만 통과해선 Bedrock 에 못 닿는다.
- **admin-api 는 Cognito 를 실시간으로 안 부른다** — Cognito 의 **공개키(JWKS)** 만 미리 받아 id_token 서명을 **로컬 검증**한다. 그래서 login 은 Cognito(공개)로, VK 발급은 admin-api(IP 제한)로 — **경로가 다르다.** → *"login 은 되는데 VK 발급이 타임아웃"* 이면 뒤(admin-api)만 막힌 것(operations.md §8-S · `inbound-cidrs`).
- **Bedrock 자격증명은 직원 손에 없다** — 실제 호출은 gateway-proxy 의 **IRSA** 로만. VK 가 유출돼도 Bedrock 직접 접근은 불가하고, VK 는 곧 만료된다.
- **왜 id_token 인가** — Cognito access_token 엔 email 이 없어서, 신원(email·group)이 담긴 **id_token** 으로 검증한다.

> ℹ️ **저장 파일 두 개**: `oidc-tokens.json`(신원 = 1단계 산출물, login 이 만듦) · `vk-cache.json`(게이트웨이 열쇠 = 2단계 산출물, helper 가 만듦).

---

## 언제 다시 인증해야 하나 (만료 조건)

위 3단계는 **매번 처음부터 하지 않는다.** 산출물(VK·id_token)이 캐시돼 있고, 만료가 다가올 때만 그 단계를 자동으로 되돌린다.

| 산출물 | 수명(TTL) | 만료되면 | 누가 처리 · 직원 체감 |
|---|---|---|---|
| **VK** (게이트웨이 열쇠) | 1시간 (`OIDC_VK_TTL_HOURS=1`) | **2단계 재실행** — id_token 으로 VK 재발급 | helper 자동 · 체감 없음 |
| **id_token** (신분증) | ~1시간 (Cognito access TTL) | refresh 로 자동 재발급 → 2단계 | helper 자동 · 체감 없음 |
| **refresh_token** (갱신 열쇠) | **7일** (`refresh_token_validity=7`) | ❌ 자동 불가 → **재로그인 요구** | 직원이 다시 `gateway-cli login` (브라우저 1회) |

helper 는 매 요청 직전 **"만료 직전에 미리"** 갱신한다(요청 도중 끊김 방지):

```
· VK       : 남은 수명 < 30분  → 미리 재발급
· id_token : 남은 수명 < 60초  → 미리 refresh
```

**핵심 — 직원이 실제로 로그인하는 건 refresh_token 만료 때뿐**(이 배포는 **7일**마다 한 번). VK·id_token 은 helper 가 자동 갱신하므로, 대부분의 요청은 캐시된 VK 로 ⑥부터 시작한다(②~⑤ 없이).

**만료 관련 진단**:

| 증상 | 원인 | 조치 |
|---|---|---|
| `re-login required` / `SSO session expired` | **refresh_token 만료** — 정상 수명이다 | 직원에게 "다시 `gateway-cli login`" |
| refresh 는 되는데 VK 발급 실패 | **토큰 만료 아님** — admin-api 네트워크 | admin-api `inbound-cidrs` 확인 (operations §8-S) |
| 매 요청이 느림 | VK 캐시가 안 남 | `~/.gateway-cli/vk-cache.json` 권한·경로 확인 |

**TTL 조절** — 바꾸는 방법(terraform·values 명령)은 [operations.md 8-Z 토큰 TTL 조절](ops/8-Z-token-ttl.md):
- **재로그인 주기 ↑** → Cognito `refresh_token_validity` 를 키운다 (이 배포 기본 **7일**, `cognito/main.tf:123`; 편의 ↑ · 유출 노출 창 ↑).
- **VK 수명** → admin-api `OIDC_VK_TTL_HOURS`(기본 1). 짧을수록 유출 내성 ↑ · admin-api 부하 ↑.

---

## 왜 이렇게 복잡한가 (개인 API 키를 안 쓰는 이유)

직원에게 그냥 Bedrock/Anthropic API 키를 나눠주면 안 되나? — 안 된다:

- **키가 유출되면 회수 불가** — VK 는 짧은 수명이라 유출돼도 곧 만료.
- **누가 얼마나 썼는지 모름** — VK 는 사용자별로 발급돼 예산·집계가 된다.
- **직원이 키를 관리해야 함** — helper 가 자동 갱신하니 직원은 로그인 한 번뿐.

그래서 **"로그인(신원) → 임시 열쇠(VK) 자동 발급"** 구조를 쓴다. 회사가 통제하고, 직원은 편하다.

---

## 설치가 깨지는 흔한 지점 (§6 실측)

이 흐름의 각 단계가 어긋나면 이렇게 실패한다 — 상세·해결은 [install-guide.md §6](install-guide.md#6-클라이언트-설치--claude-code-awsome-gateway-cli):

| 증상 | 어느 단계 | 원인 |
|---|---|---|
| `claude: 명령 없음` | 설치 전 | Claude Code 가 안 깔림 (§6-1) |
| `/status` 에 게이트웨이 주소 안 뜸 | 흐름① [2] setup | macOS 는 관리설정 경로가 달라 무시됨(fork 픽스 필요) |
| `Failed to write managed settings` | 흐름① [2] setup | 관리자 권한 아님 (Windows) |
| `401 Unauthorized` | 2단계 ③ | 개인 계정 로그인이 남아 VK 대신 그걸 보냄 |
| `SSO session expired. Run aws sso login` | 2단계 ③ | OIDC 정보가 env 에 없어 STS 로 폴백(fork 픽스 필요) |
| `429 Budget limit exceeded` | 3단계 ⑦ budget | 팀 예산 미부여 (§3-8 뒤 필요 — operations.md 8-Y) |
| 요청 타임아웃 (VK 발급) | 2단계 ④ | admin-api 가 `inbound-cidrs` 밖 (operations §8-S) |

---

## 한눈에 (요약 카드)

| 질문 | 답 |
|---|---|
| 직원이 하는 일은? | **로그인 한 번**(브라우저). 그 뒤는 자동. |
| 열쇠(VK)는 누가 만드나? | api-key-helper 가 **매 요청 직전 자동** 발급·캐시. |
| 왜 관리자 권한? | setup 이 **시스템 폴더**의 관리 설정을 쓰기 때문. |
| 개인 API 키 안 쓰는 이유? | VK 는 짧은 수명 + 사용자별 집계 → 유출·예산 통제. |
| 저장 파일 두 개? | `oidc-tokens.json`(신원) · `vk-cache.json`(열쇠). |
| 설치가 깨지면? | 위 "흔한 지점" 표 → [§6](install-guide.md#6-클라이언트-설치--claude-code-awsome-gateway-cli). |
