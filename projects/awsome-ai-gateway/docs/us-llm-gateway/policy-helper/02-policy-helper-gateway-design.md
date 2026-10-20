# policyHelper 와 LLM Gateway 연동 설계 — 사용자별 관리형 설정 내려주기

- 대상: policyHelper 의 개념은 알고, LLM Gateway 에 실제로 붙이려는 운영자·개발자
- 상태: 설계안입니다. 정책 계산 위치는 PoC 구현과 운영 구현으로 나눠 제시합니다(§5-3).
- 용어: VK·봉투·기본값·정책 조각 같은 용어가 낯설면 맨 아래 「용어」 절을 먼저 보세요.

## 1. 요약

지금은 `gateway-cli setup` 이 PC 마다 한 번 쓰는 **정적 관리형 설정**으로 Claude Code 를 LLM Gateway 에
붙입니다. 이를 **Claude Code 가 시작할 때 `policy-helper.exe` 가 api-key-helper 의 VK 로 게이트웨이에
그 사용자의 정책을 물어, 관리형 설정을 계산하는 방식**으로 바꿉니다. 첫 대상은 모델 선택 목록입니다.

- **정책 경로** — helper 가 VK 로 게이트웨이 `GET /v1/policy` 를 부릅니다. 공개된 게이트웨이 주소를
  쓰므로 VPN 없이 원격 PC 에도 정책이 전달됩니다.
- **계산 위치** — PoC 는 게이트웨이가 기존 테이블로 그때그때 계산합니다. 운영은 admin-api 가 정책을
  저장할 때 미리 계산해 두고, 게이트웨이는 읽기만 합니다.
- **멈추지 않는 helper** — 서버 불통·VK 만료·잘못된 응답은 모두 캐시나 기본값으로 처리하고 항상
  exit 0 으로 끝납니다. Claude Code 가 시작을 거부하는 경우는 helper 파일이 없거나 손상됐을 때뿐입니다.
- **확장** — MCP 허용 목록 같은 새 설정 종류는 서버에만 추가합니다. PC 에 helper 를 다시 배포하지
  않습니다.

## 2. 지금 방식 (As-is)

관리자가 PC 마다 `gateway-cli setup` 을 한 번 실행하면, 게이트웨이 주소·`apiKeyHelper`·환경변수가
`C:\Program Files\ClaudeCode\managed-settings.json` 등에 고정값으로 들어갑니다. 한계는 세 가지입니다.

- **정책을 바꾸려면 모든 PC 에서 `setup` 을 다시 돌려야 합니다.**
- **모든 PC 가 같은 값을 받습니다.** 팀·사용자별로 다르게 줄 수 없습니다.
- **모델 선택 목록에 막힌 모델도 보입니다.** 게이트웨이가 서버에서 막으므로 고르면 요청이 거부됩니다.

## 3. 바뀌는 방식 (To-be)

```text
┌─ Claude Code (CLI) ──┐            ┌─ policy-helper.exe ──┐            ┌─ policy cache ───────┐
│ managed settings     ├─(1) exec──▶│ baseline (built-in)  │            │ last good policy     │
│ = helper output      │            │ + server fragment    ├─(4) save──▶│ per user             │
│                      │◀─(5) JSON──┤                      │            │                      │
└──────────────────────┘            └───────────┬──────────┘            └──────────────────────┘
                                                │ (2) GET /v1/policy, Bearer vk
                                                │
                                                ▼
                                    ┌─ gateway-proxy ──────┐            ┌─ admin UI (prod) ────┐
                                    │ VK auth -> user_id   │            │ policy editor        │
                                    │ no admin-api call    │            │ preview + audit      │
                                    └───────────┬──────────┘            └───────────┬──────────┘
                                                │ (3) read                          │ (a) save
                                                │                                   │
                                                ▼                                   ▼
                                    ┌─ policy DB ──────────┐            ┌─ admin-api (prod) ───┐
                                    │ PoC: existing tables │            │ validate             │
                                    │ prod: saved results  │◀─(b) write─┤ compute on save      │
                                    │                      │            │                      │
                                    └──────────────────────┘            └──────────────────────┘

  (1) Claude Code 가 시작할 때와 refreshIntervalMs 마다 helper 를 인자 없이 실행
  (2) helper 가 api-key-helper 의 VK 캐시를 읽어 게이트웨이(공개 주소)에 정책을 요청
  (3) PoC: 게이트웨이가 기존 테이블(허용 모델 등)로 그때그때 계산
      운영: admin-api 가 미리 계산해 둔 행을 사용자 ID 로 읽기만 — admin-api 호출 없음
  (4) 성공한 조각을 캐시에 저장 — 다음번 서버 불통 때 이 값으로 응답
  (5) 기본값 + 조각을 stdout 으로 출력 -> 그 세션의 유일한 관리형 설정
  (a)(b) 운영: 관리자가 저장하면 admin-api 가 검증하고,
         영향받는 사용자의 조각을 다시 계산해 저장
```

- 관리형 설정 파일에는 `policyHelper` 포인터만 추가합니다. 나머지 키는 그대로 두되, helper 가 성공하면
  helper 출력이 그 세션의 **유일한** 관리형 설정이 됩니다.
- 정책 요청은 **공개된 게이트웨이 주소**로 가므로, 운영 admin-api 가 내부 전용이어도 VPN 없이 전달됩니다.
- 정책을 바꾸면 **다음 실행부터**, 실행 중인 세션은 `refreshIntervalMs`(5분) 갱신 때 재시작 없이 적용됩니다.
- 실제 적용 값은 파일이 아니라 Claude Code 의 `/status` 로 확인합니다.

## 4. 핵심 설계 결정

- **정책 경로 = VK 로 게이트웨이 호출** — 신원 확인은 기존 VK 인증을 그대로 쓰고, VPN 없이 닿습니다.
  (OIDC 토큰으로 admin-api 를 직접 부르는 방식은 admin-api 가 내부 전용이라 VPN 이 필요합니다.)
- **helper 는 설정 종류를 모름** — 서버가 완성된 `managedSettings` 조각을 주고 helper 는 덮기만 합니다.
  새 설정 종류는 서버에만 추가합니다.
- **계산 위치 = PoC 는 게이트웨이, 운영은 admin-api 저장 시 미리 계산** — 운영에서는 정책 로직을 컨트롤
  플레인에 모으고, 게이트웨이가 admin-api 에 의존하지 않게 합니다.

## 5. 구현할 구성요소

### 5-1. policy-helper.exe

```text
┌─ 1. Start ─────────────────────────────┐
│ run by Claude Code, no args            │
└────────────────────┬───────────────────┘
                     │
                     ▼
┌─ 2. Load baseline (built-in) ──────────┐
│ gateway URL, apiKeyHelper,             │
│ fixed env                              │
└────────────────────┬───────────────────┘
                     │
                     ▼
┌─ 3. Read VK (api-key-helper cache) ────┐            ┌─ A. no VK / expired ───────────────────┐
│ vk-cache.json, read only               ├─no VK─────▶│ skip fetch -> use cache/baseline       │
└────────────────────┬───────────────────┘            └────────────────────────────────────────┘
                     │
                     ▼
┌─ 4. GET /v1/policy (timeout 3s) ───────┐            ┌─ B. gateway down / timeout ────────────┐
│ server fragment                        ├─fail──────▶│ use cache, else baseline only          │
└────────────────────┬───────────────────┘            └────────────────────────────────────────┘
                     │
                     ▼
┌─ 5. Check fragment ────────────────────┐            ┌─ C. bad fragment ──────────────────────┐
│ JSON object, no "_" keys               ├─invalid───▶│ drop it -> use cache, else baseline    │
└────────────────────┬───────────────────┘            └────────────────────────────────────────┘
                     │
                     ▼
┌─ 6. Merge + save cache ────────────────┐
│ baseline <- fragment, save to cache    │
└────────────────────┬───────────────────┘
                     │
                     ▼
┌─ 7. Print envelope, exit 0 ────────────┐
│ {"managedSettings": {...}}             │
└────────────────────────────────────────┘

  A·B·C 는 실패가 아니라 대체 경로입니다 — 모두 6·7 로 이어져 항상 exit 0 으로 끝납니다
  4 의 타임아웃(3초)은 policyHelper.timeoutMs(10초)보다 충분히 짧게 잡습니다
```

지켜야 할 규칙입니다.

- **항상 exit 0 으로 끝냅니다.** 모든 문제는 A·B·C 대체 경로(캐시·기본값)로 처리합니다.
- **서버 호출은 3초 안에 끝냅니다.** `policyHelper.timeoutMs`(10초)를 넘기면 Claude Code 가 시작을 거부합니다.
- **VK 는 읽기만 합니다.** `%LOCALAPPDATA%\gateway-cli\vk-cache.json` 을 읽고, 갱신은 api-key-helper 에
  맡깁니다. 같은 파일을 둘이 쓰면 경합이 생깁니다.
- **같은 배포의 VK 만 씁니다.** `vk-cache.json` 의 `admin_api_url` 이 기본값의 `env.GATEWAY_CLI_GATEWAY_URL`
  과 같을 때만 보냅니다. 요청 주소는 기본값의 `env.ANTHROPIC_BASE_URL` + `/v1/policy` 입니다.
- **stdout 에는 봉투 하나만 씁니다.** 진단 메시지는 stderr 로 보냅니다(`claude --debug` 로 확인).

### 5-2. 게이트웨이 `GET /v1/policy`

- **VK 인증 경로에 등록합니다.** 인증 방식 목록(`resolve_auth_strategy`)의 VK 허용(DUAL) 목록에 넣지 않으면
  JWT 전용으로 처리되어 모든 VK 가 401 이 됩니다.
- **앱 권한 검사에서 뺍니다.** `ClientAuthorizationMiddleware` 는 User-Agent 로 앱을 판별하므로 helper 요청은
  통과하지 못합니다. 이 경로를 예외로 두고, 엔드포인트가 `claude-code` 앱 기준으로 정책을 계산합니다.
- **사용량으로 잡히지 않습니다.** 예산·요청 제한·다운그레이드는 추론 경로에만 걸려 있어 따로 손댈 것이 없습니다.
- **동작**: VK 인증에서 얻은 `user_id` 로 조각을 만들어 `{"version", "managedSettings"}` 로 돌려줍니다(부록 C).

### 5-3. 정책 계산 — PoC 는 게이트웨이, 운영은 admin-api

- **최신 값으로 계산합니다.** VK 에 붙은 허용 모델은 발급 당시 값이라 쓰지 않고, `user_id` 로 DB 를 읽습니다.
- **사용자를 만들지 않습니다.** 모르거나 비활성인 사용자면 빈 조각을 돌려줍니다.
- **모델 계산 규칙** — 게이트웨이가 이미 쓰는 규칙과 같습니다.
  - 허용 모델 = 사용자 목록 → 없으면 팀 목록 → 없으면 전체 허용. 여기에 `claude-code` 앱의 허용 모델을
    교집합으로 겁니다.
  - 전체 허용이면 `availableModels` 를 **내보내지 않습니다**(빈 목록 `[]` 은 "전부 막음"이라 뜻이 다릅니다).
  - 앱 기본 모델이 있으면 `model` 도 함께 내보냅니다.
- **PoC** — gateway-proxy 가 기존 테이블(`team_allowed_models`·`user_allowed_models`·앱 정책)을 바로 읽어
  계산합니다. 서비스 간 호출이 없어 가장 빨리 만들 수 있습니다.
- **운영** — 관리자가 저장하면 admin-api 가 검증하고, 영향받는 사용자의 조각을 다시 계산해 DB 에 저장합니다.
  게이트웨이는 사용자 ID 로 한 행만 읽습니다. admin-api 가 멈춰도 정책 전달은 계속됩니다.

### 5-4. 캐시

- **위치**: `%LOCALAPPDATA%\policy-helper\policy-cache.json` (사용자별)
- **내용**: 마지막으로 받은 응답과 요청 주소. 요청 주소가 지금 게이트웨이와 다르면 쓰지 않습니다.
- **위변조 위험**: 사용자가 쓸 수 있는 폴더입니다. 운영에서는 서버 서명을 검증해야 합니다. 다만 모델은
  게이트웨이가 다시 강제하므로 캐시를 고쳐도 막힌 모델로 요청할 수는 없습니다.

### 5-5. 정책 저장소와 관리 화면 (설정 종류를 늘릴 때)

모델만 다룰 때는 새로 만들 것이 없습니다. 설정 종류가 늘어나면 아래가 필요합니다.

- **등록표** — 설정 종류마다 `managedSettings` 키 이름, 값 검증 규칙, 병합 규칙, 화면 입력 형태를 적습니다.
- **정책 테이블 하나** — `설정 종류 + 범위(조직·팀·사용자) + 대상 + 값` 을 저장합니다.
- **저장할 때 검증** — 잘못된 값 하나가 helper 출력의 스키마 위반이 되면, 그 정책을 받는 **모든 PC 에서
  Claude Code 시작이 거부될 수 있습니다.**
- **미리보기와 감사 로그** — 저장 전에 "이 사용자에게 나갈 최종 JSON" 을 보여 주고, 변경 이력을 남깁니다.

## 6. 구현 순서

1. **게이트웨이** — `GET /v1/policy` 를 추가하고(5-2), 배포한 뒤 VK 로 직접 호출해 부록 C 모양의 응답이
   오는지 확인합니다.
2. **helper 빌드** — `policy-helper.exe` 에 기본값(게이트웨이 주소·`apiKeyHelper`·고정 환경변수)을 넣어
   빌드하고, 관리자만 쓸 수 있는 폴더(`C:\Program Files\PolicyHelper\`)에 설치합니다.
3. **포인터 배포** — 설치기의 `site-extra.json` 에 `policyHelper` 블록을 넣어(부록 A) 관리형 설정 파일에
   반영합니다.
4. **확인** — Claude Code 를 새로 실행해 확인합니다.
   - `/status` 의 Setting sources 가 `(helper)` 인지
   - 관리 화면에서 팀 허용 모델을 바꾼 뒤 `/model` 목록이 바뀌는지
   - 게이트웨이에 닿지 않을 때도 캐시·기본값으로 시작하는지
5. **운영 전환** — 설정 종류를 늘릴 때 5-3 운영 방식과 5-5 저장소·관리 화면을 만듭니다.

## 7. 새 설정 종류 추가하기 — 예시: MCP 허용 목록

지금은 구현하지 않습니다. 설정 종류를 넓힐 때의 순서를 보여 주는 예시입니다.

1. **등록표에 종류를 추가합니다.** 키는 `allowedMcpServers`, 병합 규칙은 좁히기(조직 목록 안에서 팀·
   사용자는 빼기만)입니다.
2. **함께 내릴 키를 정합니다.** `allowManagedMcpServersOnly: true` 를 같이 내려야 사용자가 목록을 넓히지
   못합니다.
3. **관리 화면에 입력 폼을 추가합니다.** 서버 주소(`serverUrl`)나 명령(`serverCommand`)을 받고 저장할 때 검증합니다.
4. **미리보기로 최종 JSON 을 확인한 뒤 테스트 팀부터 적용합니다.** helper 는 다시 배포하지 않습니다.

주의할 점입니다.

- **비워 두기와 `[]` 는 뜻이 반대입니다.** 안 내리면 전부 허용, `[]` 는 전부 차단입니다. 화면에서
  "제한 없음"과 "전부 차단"을 구분해야 합니다.
- **차단 목록(`deniedMcpServers`)이 항상 이깁니다.** 모든 범위의 값을 합치고, 허용 목록에 있어도 차단됩니다.

## 8. 실패 상황별 동작

| 상황 | helper 동작 | 사용자가 겪는 것 |
|---|---|---|
| VK 없음·만료 | 서버를 부르지 않고 캐시, 없으면 기본값 | 마지막 정책 (캐시도 없으면 모델 목록 제한 없음, 서버 강제는 유지) |
| 게이트웨이 불통·시간 초과 | 캐시, 없으면 기본값 | 마지막 정책 |
| 서버 오류(401 등)·잘못된 응답 | 응답을 버리고 캐시, 없으면 기본값 | 마지막 정책, stderr 에 기록 |
| 실행 중 갱신 실패 | (Claude Code 가 처리) | 마지막 정책 유지 |
| helper 파일이 없거나 손상 | 실행 자체가 안 됨 | **Claude Code 시작 거부** |

마지막 줄만 사용자를 막습니다. 이는 코드가 아니라 배포 절차(설치 확인, 관리자 전용 폴더)로 막습니다.

## 9. 구현 전 알아 둘 점

- **목록 밖 모델 처리** — `availableModels` 에 없는 모델은 `/model` 에서 숨겨지고, 직접 전환하면
  "Your organization restricts model selection." 으로 거부됩니다.
- **관리형 `model` 키** — 앱 기본 모델을 내보낼 때 시작 모델로 어떻게 적용되는지 확인이 필요합니다.
- **Claude Desktop(Cowork)** — 같은 PC 의 Cowork 내장 세션도 helper 를 따르므로, helper 가 실패하면 Cowork 가
  열리지 않을 수 있습니다. 배포 전 Cowork 동작을 확인합니다.
- **사내 CA** — helper 에는 `caBundle` 같은 설정이 없습니다. TLS 를 가로채는 프록시가 있으면 조회가 실패해
  캐시·기본값으로만 동작합니다.
- **캐시 서명** — 운영 전 서명 방식과 키 관리를 정해야 합니다.

## 부록 — 파일 예시

### A. `packaging\site-extra.json` (설치기 빌드 입력)

기존 오버레이에 `policyHelper` 블록만 더합니다. `managed` 아래 키는 관리형 설정 파일에 그대로 병합됩니다.

```json
{
  "managed": {
    "env": {
      "ENABLE_TOOL_SEARCH": "true",
      "OTEL_LOG_USER_PROMPTS": "0",
      "OTEL_LOG_TOOL_DETAILS": "0",
      "OTEL_LOG_TOOL_CONTENT": "0"
    },
    "policyHelper": {
      "path": "C:\\Program Files\\PolicyHelper\\policy-helper.exe",
      "timeoutMs": 10000,
      "refreshIntervalMs": 300000
    }
  }
}
```

### B. `policy-helper.exe` 의 stdout

기본값 + 서버 조각입니다. `_gatewayCli` 같은 모르는 키는 넣지 않습니다 — 스키마 위반 하나로 실행 전체가
실패합니다.

```json
{
  "managedSettings": {
    "apiKeyHelper": "\"C:\\Program Files\\GatewayCLI\\api-key-helper.exe\"",
    "statusLine": {
      "type": "command",
      "command": "\"C:\\Program Files\\GatewayCLI\\statusline.exe\""
    },
    "availableModels": [
      "claude-haiku-4-5-20251001",
      "claude-opus-5-5",
      "claude-sonnet-5"
    ],
    "enforceAvailableModels": true,
    "env": {
      "ANTHROPIC_BASE_URL": "https://gateway.example.com",
      "GATEWAY_CLI_GATEWAY_URL": "https://admin-api.example.com",
      "ENABLE_TOOL_SEARCH": "true"
    }
  }
}
```

### C. 게이트웨이 `GET /v1/policy` 응답

서버는 완성된 조각을 `managedSettings` 모양 그대로 돌려주고, helper 는 기본값 위에 덮기만 합니다.
`version` 은 조각 내용의 해시입니다. 아래는 팀에 모델 3개만 허용했을 때의 예시이고, 제한이 없으면
`"managedSettings": {}` 입니다.

```json
{
  "version": "736afad9037193b8",
  "managedSettings": {
    "availableModels": [
      "claude-haiku-4-5-20251001",
      "claude-opus-5-5",
      "claude-sonnet-5"
    ],
    "enforceAvailableModels": true
  }
}
```

## 용어

- **관리형 설정(managed settings)** — Claude Code 설정 계층의 최상위입니다. 사용자·프로젝트 설정이
  덮어쓸 수 없습니다.
- **봉투(envelope)** — helper 가 stdout 으로 내보내는 최상위 JSON `{"managedSettings": {...}}` 입니다.
- **기본값(baseline)** — helper 실행 파일 안에 넣어 둔 고정 값입니다. 게이트웨이 주소, `apiKeyHelper`
  경로, 고정 환경변수가 들어갑니다.
- **정책 조각(policy fragment)** — 서버가 사용자마다 계산해 주는 `managedSettings` 의 일부입니다.
  helper 는 기본값 위에 조각을 덮어 최종 봉투를 만듭니다.
- **VK(Virtual Key)** — 게이트웨이가 사용자에게 발급하는 키입니다. api-key-helper 가 발급받아
  `vk-cache.json` 에 저장하며, helper 도 이 키로 신원을 밝힙니다.
- **대체 경로** — helper 가 서버에 닿지 못할 때 캐시나 기본값으로 응답하는 길입니다.
- **설정 종류·등록표** — `availableModels` 처럼 `managedSettings` 키 하나와 그 검증·병합 규칙을 묶은 단위와,
  그것을 종류별로 적은 서버 쪽 목록입니다.
- **범위·병합 규칙** — 정책을 거는 단위(조직·팀·사용자)와, 여러 범위의 값을 합치는 방법입니다.
  덮어쓰기(좁은 범위가 이김)·합집합(모두 모음, 차단 목록용)·좁히기(넓은 목록 안에서 빼기만)가 있습니다.

## 참고 (공식 문서)

- Settings reference › policyHelper — 키 정의와 실패 동작
[https://code.claude.com/docs/en/settings-reference#policyhelper](https://code.claude.com/docs/en/settings-reference#policyhelper)
- Deploy managed settings › Compute the policy with a helper program
[https://code.claude.com/docs/en/managed-settings#compute-the-policy-with-a-helper-program](https://code.claude.com/docs/en/managed-settings#compute-the-policy-with-a-helper-program)
- Control MCP server access for your organization — 7절의 MCP 허용·차단 규칙
[https://code.claude.com/docs/en/managed-mcp](https://code.claude.com/docs/en/managed-mcp)
