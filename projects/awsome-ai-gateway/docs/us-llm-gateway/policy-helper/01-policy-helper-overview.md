# Claude Code policyHelper 개요 — 관리형 설정을 실행 시점에 계산하기

- 작성일: 2026-09-28

## 1. 요약

`policyHelper` 는 Claude Code 가 **시작할 때 실행하는, 관리자가 지정한 실행 파일**입니다.
이 파일이 stdout 으로 내놓은 JSON(`managedSettings`)이 그 세션의 **관리형 정책**이 됩니다.
정책을 정적 파일에 적어 두는 대신 **실행 시점에 계산**하므로, 재배포 없이 팀·사용자마다 다른
정책을 줄 수 있습니다.

- **설정 위치** — 관리자만 쓸 수 있는 정책 소스(Windows HKLM, 시스템 `managed-settings.json`,
macOS 관리형 plist)에 적었을 때만 동작합니다.
- **출력 규약** — stdout 은 `{"managedSettings": {...}}` 봉투 하나이고, 이 출력이 그 세션의
**유일한** 관리형 설정이 됩니다.
- **실패하면 시작 거부** — 시작 시 helper 가 실패하면 Claude Code 가 시작되지 않습니다. 그래서
helper 는 서버 장애 때도 캐시로 응답하고 exit 0 하도록 만들어야 합니다.
- **주기적 갱신** — `refreshIntervalMs` 마다 다시 실행해 재시작 없이 정책을 바꿉니다. 갱신에
실패하면 마지막 정책을 유지합니다.
- **apiKeyHelper 와의 역할 분담** — apiKeyHelper 는 "누구인가"(자격증명), policyHelper 는
"무엇이 허용되는가"(정책)를 맡습니다. 함께 쓸 수 있습니다.



## 2. 왜 필요한가

- **정적 관리형 설정의 한계** — `managed-settings.json`·GPO·MDM 프로파일은 모든 PC 에 같은 값이
들어가고, 바꾸려면 재배포(GPO·MDM·파일 배포)가 필요합니다.
- **동적 정책 요구** — 사용자·IdP 그룹별 모델 허용 목록, 서로 다른 MCP 서버 허용 목록, 중앙 정책 서비스의 판단을 반영하고 싶습니다.
- **policyHelper 의 해법** — 정책 결정(PDP, Policy Decision Point)을 조직이 만든 실행 파일에
위임합니다. Claude Code 는 결과만 받아 강제(PEP, Policy Enforcement Point)합니다.



## 3. 핵심 용어

- **Managed settings (관리형 설정)** — 설정 계층의 최상위입니다. 사용자·프로젝트·`--settings`
값이 덮어쓸 수 없습니다.
- **Admin-controlled policy source** — 관리자만 쓸 수 있는 정책 소스입니다. policyHelper 는
아래 3곳에 적혀 있을 때만 동작합니다.
  - Windows 레지스트리 `HKLM\SOFTWARE\Policies\ClaudeCode` 의 `Settings` 값
  - 시스템 `managed-settings.json` (Windows `C:\Program Files\ClaudeCode\`)
  - macOS 관리형 환경설정(plist, MDM 구성 프로파일)
- **Server-managed settings** — claude.ai 콘솔이 원격 배포하는 설정입니다. 이것이 있으면
helper 는 실행되지 않습니다.
- **HKCU registry** — 사용자가 쓸 수 있는 레지스트리입니다. admin source 가 아니므로 여기 넣은
policyHelper 는 무시됩니다.
- **Parent settings** — Claude Desktop·IDE·Agent SDK 같은 호스트가 넘겨주는 설정입니다.
helper 가 정책을 내면 병합되지 않습니다.
- **Envelope (봉투)** — helper stdout 의 최상위 JSON 객체입니다. 설정은 반드시 `managedSettings`
키 아래에 둡니다.
- **Fail-closed** — 실패하면 허용이 아니라 거부로 끝나는 설계입니다. policyHelper 시작 실패가
그렇습니다.

근거:

- Deploy managed settings › Where each mechanism stores the policy
[https://code.claude.com/docs/en/managed-settings#where-each-mechanism-stores-the-policy](https://code.claude.com/docs/en/managed-settings#where-each-mechanism-stores-the-policy)
- Deploy managed settings › How Claude Code combines managed sources
[https://code.claude.com/docs/en/managed-settings#how-claude-code-combines-managed-sources](https://code.claude.com/docs/en/managed-settings#how-claude-code-combines-managed-sources)
- Deploy managed settings › Let an embedding host add policy
[https://code.claude.com/docs/en/managed-settings#let-an-embedding-host-add-policy](https://code.claude.com/docs/en/managed-settings#let-an-embedding-host-add-policy)



## 4. 아키텍처

```text
┌─ Admin policy source ────────────┐
│ Windows HKLM | macOS plist       │
│ managed-settings.json            │
│ "policyHelper": { path, ... }    │
└───────────┬──────────────────────┘
            │ (1) read at launch
            │
            ▼
┌─ Claude Code  (CLI / Agent SDK) ─┐              ┌─ policyHelper executable ───────┐
│ loads settings at launch         ├─(2) exec────▶│ no args                         │
│ managed settings <- helper       │              │ env: CLAUDE_CODE_VERSION        │
│                                  │◀─(4) stdout──┤ stdout: JSON <= 1 MiB           │
└───────────┬──────────────────────┘              └───────────┬─────────────────────┘
            │ (5) apply                                       │ (3) HTTPS (optional)
            │                                                 │
            ▼                                                 ▼
┌─ Session ────────────────────────┐              ┌─ Policy service ────────────────┐
│ permissions, availableModels,    │              │ e.g. admin-api GET /policy      │
│ env, apiKeyHelper, MCP ...       │              │ user/group -> models, env       │
└──────────────────────────────────┘              └─────────────────────────────────┘
```

- **(1) 정책 소스 고르기** — Claude Code 는 시작할 때 관리형 소스를 **우선순위 순서대로** 확인해, 정책 키가
  있는 **첫 번째 소스 하나만** 씁니다(first-wins). `policyHelper` 도 그 소스에서만 읽습니다.
  - 순서: 원격 설정 → OS 정책(Windows HKLM, macOS plist) → `managed-settings.json` → HKCU
  - LLM Gateway 환경은 원격 설정을 쓰지 않으므로 실제로는 **HKLM → 파일** 순입니다.
  - ⚠️ HKLM 에 다른 정책 키가 있으면 파일의 `policyHelper` 는 무시됩니다. HKCU 에 넣은 것도 실행되지 않습니다.
- **(2) helper 실행** — 첫 프롬프트 전에 helper 를 **인자 없이** 실행하고, 환경변수 `CLAUDE_CODE_VERSION` 을
  더해 줍니다. `timeoutMs`(기본 10초) 안에 끝나야 합니다.
- **(3) 정책 계산** — 정책을 어디서 가져올지는 조직이 정합니다(정책 서버·로컬 캐시·디바이스 상태). 서버 장애
  때도 캐시로 응답하고 exit 0 해야 합니다.
- **(4) 출력 읽기** — stdout 의 **JSON 객체 하나**(봉투, 1 MiB 이하)를 읽습니다. 설정은 `managedSettings` 키
  아래에 둡니다.
- **(5) 적용** — `managedSettings` 가 그 세션의 **유일한** 관리형 설정이 됩니다. HKLM·파일 값은 쓰이지
  않으므로, 필요한 `apiKeyHelper`·`env` 도 출력에 다시 담습니다.

근거:

- Settings reference › policyHelper
[https://code.claude.com/docs/en/settings-reference#policyhelper](https://code.claude.com/docs/en/settings-reference#policyhelper)
- Deploy managed settings › Compute the policy with a helper program
[https://code.claude.com/docs/en/managed-settings#compute-the-policy-with-a-helper-program](https://code.claude.com/docs/en/managed-settings#compute-the-policy-with-a-helper-program)



## 5. 실행 흐름

```text
┌─ 1. Launch ──────────────────────────┐
│ claude / claude -p / Agent SDK       │
└─────────────────┬────────────────────┘
                  │
                  ▼
┌─ 2. Select managed source ───────────┐            ┌─ Helper NOT run ──────────────┐
│ server-managed > HKLM/plist > file   ├─shadowed──▶│ server-managed settings win   │
│ policyHelper key in selected source? │            │ (no key -> static policy)     │
└─────────────────┬────────────────────┘            └───────────────────────────────┘
                  │ yes
                  ▼
┌─ 3. Run helper ──────────────────────┐            ┌─ FAIL -> refuse to start ─────┐
│ no args, within timeoutMs            ├─fail──────▶│ non-zero / timeout / bad JSON │
│ parse stdout (single JSON object)    │            │ print reason and exit         │
│                                      │            └───────────────────────────────┘
│                                      │
│                                      │            ┌─ Output w/o managedSettings ──┐
│                                      ├─empty─────▶│ exit 0, contributes nothing   │
│                                      │            │ other sources apply           │
│                                      │            └───────────────────────────────┘
└─────────────────┬────────────────────┘
                  │ OK: {"managedSettings": {...}}
                  │
                  ▼
┌─ 4. Session starts ──────────────────┐
│ helper output = ONLY managed source  │
│ MDM / file / HKCU / parent ignored   │
└─────────────────┬────────────────────┘
                  │ every refreshIntervalMs
                  ▼
┌─ 5. Background refresh ──────────────┐
│ OK   -> replace policy (no restart)  │
│ FAIL -> keep last policy, /status    │
└──────────────────────────────────────┘
```

- **shadowed** — 원격 설정이 있으면 그쪽이 우선하고 helper 는 실행되지 않습니다(LLM Gateway 환경은 해당
  없음). 선택된 소스에 `policyHelper` 가 없으면 그 소스의 정적 설정이 적용됩니다.
- **fail** — 파일 없음·exit 0 아님·시간 초과·출력 1 MiB 초과·JSON 아님·스키마 위반이면 **시작을 거부**합니다
  (대화형·`-p`·SDK·백그라운드 모두). exit 0 이 아닐 때는 helper 의 stderr 가 메시지에 함께 나옵니다.
- **empty** — exit 0 인데 `managedSettings` 가 없으면 helper 는 아무것도 적용하지 않고 원래 소스가 쓰입니다.
  봉투 없이 설정을 바로 출력하는 실수도 조용히 여기에 해당합니다.
- **refresh** — `refreshIntervalMs`(최소 60000, 0 은 끔) 마다 다시 실행합니다. 성공하면 재시작 없이 바뀌고,
  실패하면 마지막 정책을 유지하며 `/status` 에 이유를 보여 줍니다.

근거:

- Settings reference › policyHelper
[https://code.claude.com/docs/en/settings-reference#policyhelper](https://code.claude.com/docs/en/settings-reference#policyhelper)
- Settings reference › Write the helper output
[https://code.claude.com/docs/en/settings-reference#write-the-helper-output](https://code.claude.com/docs/en/settings-reference#write-the-helper-output)
- Settings reference › Helper failures
[https://code.claude.com/docs/en/settings-reference#helper-failures](https://code.claude.com/docs/en/settings-reference#helper-failures)



## 6. 설정 방법



### 6-1. 정책 소스에 키 배포

📋 참고 — `managed-settings.json` (5초 제한, 5분마다 갱신)

```json
{
  "policyHelper": {
    "path": "C:\\Program Files\\PolicyHelper\\policy-helper.exe",
    "timeoutMs": 5000,
    "refreshIntervalMs": 300000
  }
}
```

- Windows 레지스트리로 배포할 때는 같은 JSON 을 `HKLM\SOFTWARE\Policies\ClaudeCode` 의
`Settings` 값(REG_SZ)에 넣습니다. HKLM 이 파일보다 우선합니다.
- 새로 넣거나 바꾼 `policyHelper` 는 **다음 실행부터** 적용됩니다.

근거:

- Settings reference › policyHelper
[https://code.claude.com/docs/en/settings-reference#policyhelper](https://code.claude.com/docs/en/settings-reference#policyhelper)
- Deploy managed settings › Where each mechanism stores the policy
[https://code.claude.com/docs/en/managed-settings#where-each-mechanism-stores-the-policy](https://code.claude.com/docs/en/managed-settings#where-each-mechanism-stores-the-policy)



### 6-2. 필드

- `path` — 절대경로, 정규화된 형식(`.`·`..` 금지)입니다. Windows 는 드라이브/UNC 경로이며
`.exe` **로 끝나야** 합니다(`.ps1`·`.cmd` 불가).
- `timeoutMs` — 기본 10000, 최소 1000 입니다. 초과하면 실패로 처리됩니다.
- `refreshIntervalMs` — 기본은 미설정(시작 시 1회만)입니다. `0` 은 끔이고, 켜려면 최소 60000 입니다.

근거:

- Settings reference › policyHelper.path
[https://code.claude.com/docs/en/settings-reference#policyhelper-path](https://code.claude.com/docs/en/settings-reference#policyhelper-path)
- Settings reference › policyHelper.timeoutMs
[https://code.claude.com/docs/en/settings-reference#policyhelper-timeoutms](https://code.claude.com/docs/en/settings-reference#policyhelper-timeoutms)
- Settings reference › policyHelper.refreshIntervalMs
[https://code.claude.com/docs/en/settings-reference#policyhelper-refreshintervalms](https://code.claude.com/docs/en/settings-reference#policyhelper-refreshintervalms)



### 6-3. helper 출력 규약

📋 참고 — stdout 예시

```json
{
  "managedSettings": {
    "availableModels": ["claude-sonnet-5", "claude-haiku-4-5-20251001"],
    "apiKeyHelper": "C:\\Program Files\\GatewayCLI\\api-key-helper.exe",
    "env": { "ANTHROPIC_BASE_URL": "https://gateway.example.com" },
    "permissions": { "deny": ["Read(//etc/secrets/**)"] }
  }
}
```

- stdout 은 **단일 JSON 객체**이고, stdout·stderr 는 각각 1 MiB 이하입니다.
- ⚠️ `managedSettings` 키 없이 설정을 바로 출력하면 **아무것도 적용되지 않고 오류도 없습니다.**
- 출력이 곧 유일한 관리형 설정이므로, 기존 파일·레지스트리·MDM 에 있던 값(`apiKeyHelper`·`env` 등)도
**필요하면 전부 출력에 다시 담아야** 합니다.
- 스키마 위반은 정적 파일보다 엄격합니다 — 복구되지 않는 위반 하나로 실행 전체가 실패합니다.

근거:

- Settings reference › Write the helper output
[https://code.claude.com/docs/en/settings-reference#write-the-helper-output](https://code.claude.com/docs/en/settings-reference#write-the-helper-output)
- Deploy managed settings › Find entries Claude Code dropped
[https://code.claude.com/docs/en/managed-settings#find-entries-claude-code-dropped](https://code.claude.com/docs/en/managed-settings#find-entries-claude-code-dropped)



## 7. 운영 주의

- **가용성** — 시작 실패 = 시작 거부는 의도된 설계입니다. 정책 서버 장애·VPN 끊김에 대비해
helper 가 **자체 캐시로 응답하고 exit 0** 하게 만듭니다.
- **무결성** — helper 실행 파일은 관리자만 쓸 수 있는 경로(예: `C:\Program Files\...`)에 둡니다.
일반 사용자가 파일을 추가할 수 있는 폴더면 교체·DLL 삽입으로 정책을 우회할 수 있습니다.
`C:\` 바로 아래 만든 폴더는 `BUILTIN\Users` 의 파일 추가 권한을 상속하는 경우가 많으니
설치 전에 권한을 확인합니다.
- **캐시 위변조** — 캐시를 사용자가 쓸 수 있는 위치에 두면 정책을 바꿔치기할 수 있습니다.
서명 검증 등은 설계 과제로 남습니다.
- **관측** — `/status` 의 Setting sources 에 `(helper)` 로 표시됩니다. `claude --debug` 는 매
실행의 helper stderr 를 debug log 에 남깁니다.
- **잘못된 키 값** — 경로 문자열만 넣거나 `timeoutMs` 가 최소값 미만이면 dropped entry 로
보고되고 helper 없이 나머지 관리형 설정으로 시작합니다.
- **버전** — 2.1.257 에서 타이머 값 clamp·진단 개선이 들어갔습니다. 최신 버전으로 테스트합니다.
- **비공개 변형** — 바이너리에는 OS별 `policyHelpers`(inline script·`onFailure`·`merge`)도
있으나 `@internal` 이며 문서에 없습니다. 사용하지 않습니다.

근거:

- Settings reference › Helper failures
[https://code.claude.com/docs/en/settings-reference#helper-failures](https://code.claude.com/docs/en/settings-reference#helper-failures)
- Deploy managed settings › Read the source in /status
[https://code.claude.com/docs/en/managed-settings#read-the-source-in-/status](https://code.claude.com/docs/en/managed-settings#read-the-source-in-/status)
- Claude Code CHANGELOG › 2.1.257
[https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)



## 8. apiKeyHelper 와의 관계

```text
┌─ Claude Code ──────────┐              ┌─ policyHelper  (policy plane) ────────────┐
│ CLI / Agent SDK        │              │ when: launch + every refreshIntervalMs    │
│                        │              │ out : {"managedSettings": {...}}          │
│ at launch:             ├─(1) launch──▶│ sets: models, permissions, env,           │
│  resolve policy        │              │       apiKeyHelper path                   │
│                        │              └───────────────────────────────────────────┘
│                        │
│                        │              ┌─ apiKeyHelper  (credential plane) ────────┐
│                        │              │ when: before API calls (cached, TTL)      │
│ per API call:          ├─(2) request─▶│ out : API key (VK: vk-...)                │
│  get credential        │              │ uses: OIDC login cache                    │
│                        │              └───────────────────────────────────────────┘
│                        │
│                        │              ┌─ LLM Gateway -> Bedrock ──────────────────┐
│ then:                  ├─(3) Bearer──▶│ Authorization: Bearer vk-...              │
│  send request          │              │ gateway-proxy checks VK, budget           │
└────────────────────────┘              └───────────────────────────────────────────┘
```

- **apiKeyHelper = 자격증명 평면** — "누구인가". 요청에 붙일 키를 냅니다(LLM Gateway 는 VK).
- **policyHelper = 정책 평면** — "무엇이 허용되는가". 모델·권한·env 를 정합니다.
- 서로 대체하지 않습니다. policyHelper 출력 안에 `apiKeyHelper` 경로를 담아 함께 쓸 수 있습니다.

근거:

- Settings reference › apiKeyHelper
[https://code.claude.com/docs/en/settings-reference#apikeyhelper](https://code.claude.com/docs/en/settings-reference#apikeyhelper)
- Settings reference › policyHelper
[https://code.claude.com/docs/en/settings-reference#policyhelper](https://code.claude.com/docs/en/settings-reference#policyhelper)



## 9. Claude Desktop 앱(Cowork·Chat)에서 쓸 수 있나

**결론 — Desktop 에는 직접 못 쓰지만, 같은 PC 의 Cowork 에는 영향을 줍니다.** `policyHelper` 는
Claude Code 설정 키이고, Claude Desktop 관리형 설정에는 이 키가 없습니다. Cowork 는 게이트웨이 연결(VK·모델
목록)을 Desktop 관리형 설정(`inferenceCredentialHelper` 등)으로 받으므로 policyHelper 가 필요 없고, 모델 목록도
policyHelper 로 바뀌지 않습니다. 다만 Cowork 탭은 내장 Claude Code 세션으로 돌고 PC 의 Claude Code 관리형 설정을
읽으므로, 같은 PC 에 policyHelper 를 배포하면 **두 설정이 충돌하지 않는지 확인해야 합니다**(아래 "쓰임새별 판단").

```text
┌─ Desktop managed config ───────────┐            ┌─ Bootstrap endpoint (optional) ─┐
│ HKLM\SOFTWARE\Policies\Claude      ├─(a) fetch─▶│ HTTPS, per-user settings        │
│ gateway URL, credential helper     │            │ Desktop keys only               │
│ bootstrapUrl  (no policyHelper)    │            │                                 │
└───────────┬────────────────────────┘            └─────────────────────────────────┘
            │ (b) apply
            │
            ▼
┌─ Claude Desktop app ───────────────┐
│ Cowork / Code / Chat tabs          │
│ model list, disabled tools         │
└───────────┬────────────────────────┘
            │ (c) parent settings
            │
            ▼
┌─ Claude Code inside Desktop ───────┐            ┌─ Claude Code admin source ──────┐
│ no admin source -> Desktop rules   │            │ HKLM\...\Policies\ClaudeCode    │
│ policyHelper -> helper output only │◀─(d) read──┤ or managed-settings.json        │
│ helper fail -> session refused     │            │ "policyHelper": { ... }         │
└────────────────────────────────────┘            └─────────────────────────────────┘
```

- (a) Desktop 은 `bootstrapUrl` 이 있으면 실행 시 사용자별 설정 overlay 를 받아옵니다.
- (b) Desktop 은 자기 관리형 설정(게이트웨이 URL·credential helper·모델 목록 등)을 적용합니다.
- (c) Cowork·Code·Chat 탭은 내장 Claude Code 세션이며, Desktop 이 정책을 parent settings 로 넘깁니다.
- (d) 내장 세션도 PC 의 Claude Code 관리형 소스(HKLM `ClaudeCode`·`managed-settings.json`)를
읽습니다(문서 기준).

**쓰임새별 판단**

- **Desktop 설정에 policyHelper 넣기** — ❌ 불가합니다. 해당 키가 없습니다.
- **Desktop 에서 사용자별 동적 설정** — ✅ `bootstrapUrl` 계열로 합니다.
  - `bootstrapUrl`: HTTPS 엔드포인트가 사용자별 JSON overlay 를 반환하며, 로컬 값을 덮고 읽기 전용입니다
  - 인증: `bootstrapOidc`(브라우저 로그인 Bearer) · `bootstrapHeaders` · `bootstrapHeadersHelper`
  (헤더만 만드는 helper — 설정을 계산하지는 않습니다)
  - LLM Gateway 에는 bootstrap 엔드포인트가 없습니다(Claude apps gateway 는 `/user/bootstrap` 제공).
- **PC 에 Claude Code 용 policyHelper 배포** — ⚠️ Desktop 내장 세션에도 적용되므로(문서 기준) 두 설정이
충돌하지 않는지 확인합니다.
  - **게이트웨이 연결** — helper 출력의 `env.ANTHROPIC_BASE_URL`·`apiKeyHelper` 와 Desktop 의
  `inferenceGatewayBaseUrl`·`inferenceCredentialHelper` 가 서로 다른 게이트웨이나 키를 가리키지 않는지
  확인합니다. 둘이 겹칠 때 어느 쪽이 쓰이는지는 문서에 없습니다.
  - **Desktop 제한 설정** — Desktop 이 넘기는 parent settings(외부 접속 허용 목록 → `WebFetch` 도메인·
  샌드박스 네트워크 규칙)는 helper 가 있으면 병합되지 않습니다. 이 제한에 기대고 있다면 같은 규칙을
  helper 출력에도 넣어야 합니다.
  - **helper 실패** — helper 가 실패하면 Cowork 내장 세션도 시작이 거부될 수 있습니다.
  - 모델 목록·비활성 도구 목록은 Desktop 이 내장 세션에 직접 적용하므로 policyHelper 의 영향이 적습니다.

근거:

- Claude apps gateway › Deliver policy to Claude Desktop sessions
[https://code.claude.com/docs/en/claude-apps-gateway#deliver-policy-to-claude-desktop-sessions](https://code.claude.com/docs/en/claude-apps-gateway#deliver-policy-to-claude-desktop-sessions)
- Claude apps gateway › Which machines need the opt-in
[https://code.claude.com/docs/en/claude-apps-gateway#which-machines-need-the-opt-in](https://code.claude.com/docs/en/claude-apps-gateway#which-machines-need-the-opt-in)
- Claude apps gateway › Connect Claude Desktop
[https://code.claude.com/docs/en/claude-apps-gateway#connect-claude-desktop](https://code.claude.com/docs/en/claude-apps-gateway#connect-claude-desktop)
- Claude Desktop configuration › bootstrapUrl
[https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapurl](https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapurl)
- Claude Desktop configuration › bootstrapHeadersHelper
[https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapheadershelper](https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapheadershelper)



## 10. 참고

**Claude Code — policyHelper**

- Settings reference › policyHelper — 키 정의·범위·동작
[https://code.claude.com/docs/en/settings-reference#policyhelper](https://code.claude.com/docs/en/settings-reference#policyhelper)
- Settings reference › Write the helper output — stdout 봉투 규약
[https://code.claude.com/docs/en/settings-reference#write-the-helper-output](https://code.claude.com/docs/en/settings-reference#write-the-helper-output)
- Settings reference › Helper failures — 실패 조건·시작 거부·갱신 실패
[https://code.claude.com/docs/en/settings-reference#helper-failures](https://code.claude.com/docs/en/settings-reference#helper-failures)
- Settings reference › policyHelper.path
[https://code.claude.com/docs/en/settings-reference#policyhelper-path](https://code.claude.com/docs/en/settings-reference#policyhelper-path)
- Settings reference › policyHelper.timeoutMs
[https://code.claude.com/docs/en/settings-reference#policyhelper-timeoutms](https://code.claude.com/docs/en/settings-reference#policyhelper-timeoutms)
- Settings reference › policyHelper.refreshIntervalMs
[https://code.claude.com/docs/en/settings-reference#policyhelper-refreshintervalms](https://code.claude.com/docs/en/settings-reference#policyhelper-refreshintervalms)

**Claude Code — 관리형 설정 일반**

- Deploy managed settings › Compute the policy with a helper program
[https://code.claude.com/docs/en/managed-settings#compute-the-policy-with-a-helper-program](https://code.claude.com/docs/en/managed-settings#compute-the-policy-with-a-helper-program)
- Deploy managed settings › Where each mechanism stores the policy — HKLM·HKCU·파일·plist 경로
[https://code.claude.com/docs/en/managed-settings#where-each-mechanism-stores-the-policy](https://code.claude.com/docs/en/managed-settings#where-each-mechanism-stores-the-policy)
- Deploy managed settings › How Claude Code combines managed sources — 소스 우선순위
[https://code.claude.com/docs/en/managed-settings#how-claude-code-combines-managed-sources](https://code.claude.com/docs/en/managed-settings#how-claude-code-combines-managed-sources)
- Deploy managed settings › Let an embedding host add policy — parent settings
[https://code.claude.com/docs/en/managed-settings#let-an-embedding-host-add-policy](https://code.claude.com/docs/en/managed-settings#let-an-embedding-host-add-policy)
- Deploy managed settings › Find entries Claude Code dropped — 잘못된 값 처리
[https://code.claude.com/docs/en/managed-settings#find-entries-claude-code-dropped](https://code.claude.com/docs/en/managed-settings#find-entries-claude-code-dropped)
- Settings reference › apiKeyHelper
[https://code.claude.com/docs/en/settings-reference#apikeyhelper](https://code.claude.com/docs/en/settings-reference#apikeyhelper)
- Settings reference › parentSettingsBehavior
[https://code.claude.com/docs/en/settings-reference#parentsettingsbehavior](https://code.claude.com/docs/en/settings-reference#parentsettingsbehavior)

**Claude Desktop**

- Claude apps gateway › Deliver policy to Claude Desktop sessions
[https://code.claude.com/docs/en/claude-apps-gateway#deliver-policy-to-claude-desktop-sessions](https://code.claude.com/docs/en/claude-apps-gateway#deliver-policy-to-claude-desktop-sessions)
- Claude apps gateway › Which machines need the opt-in — policyHelper fleet 제약 명시
[https://code.claude.com/docs/en/claude-apps-gateway#which-machines-need-the-opt-in](https://code.claude.com/docs/en/claude-apps-gateway#which-machines-need-the-opt-in)
- Claude apps gateway › Connect Claude Desktop — `bootstrapUrl`·`chatTabEnabled`
[https://code.claude.com/docs/en/claude-apps-gateway#connect-claude-desktop](https://code.claude.com/docs/en/claude-apps-gateway#connect-claude-desktop)
- Claude Desktop configuration › bootstrapUrl
[https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapurl](https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapurl)
- Claude Desktop configuration › bootstrapOidc
[https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapoidc](https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapoidc)
- Claude Desktop configuration › bootstrapHeadersHelper
[https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapheadershelper](https://claude.com/docs/third-party/claude-desktop/configuration#bootstrapheadershelper)
- Claude Desktop configuration › trustBootstrapDelivery
[https://claude.com/docs/third-party/claude-desktop/configuration#trustbootstrapdelivery](https://claude.com/docs/third-party/claude-desktop/configuration#trustbootstrapdelivery)

**변경 이력**

- Claude Code CHANGELOG — 2.1.257(타이머 clamp·진단)·2.1.259·2.1.274 policy helper 항목
[https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)

