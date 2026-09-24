# Claude Code 설치기 — 관리자 End-to-End (Windows)

```text
┌─ BUILD PC   (admin) ───────────────────────────────────────────┐
│ 1) get installer source  (vendor 0.2.0, not in this repo)      │
│ 2) edit  packaging\site-config.json   (4 site values)          │
│ 3) run   packaging\build.ps1                                   │
│    out:  dist\installer\gateway-cli-setup-<ver>.exe            │
└───────────┬────────────────────────────────────────────────────┘
            │ 설치 파일 1개를 전달
            │
            ▼
┌─ USER PC   (admin)   -- once per PC ───────────────────────────┐
│ 4) run gateway-cli-setup-<ver>.exe        [installer .exe]     │
│    -> C:\Program Files\GatewayCLI + PATH                       │
│ 5) run gateway-cli setup --model <alias>  [CLI subcommand]     │
│    -> C:\Program Files\ClaudeCode\managed-settings.json        │
└───────────┬────────────────────────────────────────────────────┘
            │ 설정이 깔린 PC 를 사용자에게
            │
            ▼
┌─ USER PC   (each user)   -- once per user ─────────────────────┐
│ 6) install Claude Code  (native installer, no admin)           │
│ 7) run gateway-cli login   (OIDC -> token cache)               │
│ 8) run claude  -> /status  -> first prompt                     │
│                                                                │
└────────────────────────────────────────────────────────────────┘
4) 가 5) 를 자동 실행하지 않습니다 — 설치 후 사람이 따로 실행합니다.
```

| 단계 | 누가 | 횟수 |
|---|---|---|
| 빌드(1~3) | 빌드 담당 관리자 | 배포 접속 정보가 바뀔 때마다 |
| 배포·설정 적용(4~5) | 배포 관리자 | PC 당 1회 (관리자 권한) |
| Claude Code 설치·로그인·사용(6~8) | 최종 사용자 | 사용자당 1회 |

**이름이 비슷한 둘 — 헷갈리지 않게**

| 이름 | 무엇 | 언제 |
|---|---|---|
| `gateway-cli-setup-<ver>.exe` | Inno Setup 이 만든 **설치 파일**. 프로그램을 PC 에 깝니다 | PC 당 1회 |
| `gateway-cli setup` | 설치된 CLI 의 **하위 명령**. Claude Code 설정을 씁니다 | 설치 뒤, 모델·접속 정보가 바뀔 때마다 |

파일 이름은 하이픈으로 이어지고(`gateway-cli-setup-…exe`), 명령은 띄어 씁니다
(`gateway-cli` + `setup`). 설치 파일은 `setup` 명령을 자동 실행하지 않습니다 — 선택값을 받고
되돌리기 단위를 나누기 위해 분리돼 있습니다.

게이트웨이(서버)는 바뀌지 않습니다 — 직원 PC 쪽 절차만 바뀝니다. 지금까지 Windows 는 Python·git
을 깔고 저장소를 받아 `pip install` 한 뒤 스크립트를 돌렸습니다(`US-01` §6-3). 이 문서는 그
절차를 대체하는 방법이며, 둘 중 하나만 하면 됩니다. Cowork 설치기(US-09)와 같은 방식을
Claude Code 에 적용한 것입니다.

## 0. 빌드 전 결정 3가지

| 결정 | 권장 | 한 줄 이유 |
|---|---|---|
| 코드 서명 | 테스트 = 미서명 / 정식 배포 = 사내 코드서명 인증서(`-SignThumbprint`) | 미서명 PyInstaller exe 는 SmartScreen 경고·EDR 오탐 대상 |
| 모델 지정 | `--model` 만 지정하고 모델 목록은 비워 둔다 | 허용 모델은 서버(팀·사용자 허용 목록)가 정한다. `--available-models` 를 박으면 모델을 새로 등록할 때마다 PC 마다 `setup` 을 다시 돌려야 한다 |
| 사용자 환경변수 | 같은 PC 에 Cowork 가 **다른 배포**를 보면 `--no-persist-env` | `setup` 기본값은 접속 정보 4개를 사용자 환경변수로도 남깁니다 |

## 1. 빌드

빌드 PC 요건 — Windows x64(PyInstaller 는 크로스 컴파일 불가) · Python 3.11 이상 ·
Inno Setup 6(`ISCC.exe`) · 설치기 소스.

설치기 소스는 벤더 배포본 0.2.0 이고, fork 의 **`feat/cc-installer-import`** 브랜치에 있습니다.
주석과 테스트 입력의 고객 식별 문구 4곳만 placeholder 로 바꾼 사본이라 기능 코드는 벤더 원본과
같습니다. 이 브랜치는 `us/deploy-fixes` 에 머지하지 않습니다.

### 1-1. 소스 받기

▶ **실행** · 빌드 PC — 🔵 일반 PowerShell

```powershell
mkdir C:\build -Force | Out-Null
cd C:\build
git clone --depth 1 -b feat/cc-installer-import https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
cd sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway\installer
```

작업 폴더를 먼저 정합니다. 관리자 PowerShell 은 `C:\Windows\system32` 에서 열리므로, 그대로
`git clone` 하면 소스가 시스템 폴더 안에 들어갑니다.

### 1-2. 사내 접속 정보 넣기 — `packaging\site-config.json`

**이 파일은 저장소에 없습니다. 직접 만듭니다.** 넣지 않고 빌드하면 범용 빌드가 되어, 설치한
PC 에서 `setup` 이 `--gateway-url`·`--admin-api-url`·`--oidc-issuer-url`·`--oidc-client-id`
네 개를 직접 달라고 요구합니다. 만든 파일은 커밋하지 않습니다.

▶ **실행** · 빌드 PC — 🔵 일반 PowerShell (`<…>` 를 이 배포 값으로 바꿉니다)

```powershell
cd C:\build\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway\installer
$c = @'
{
  "oidcIssuerUrl": "https://cognito-idp.<region>.amazonaws.com/<pool-id>",
  "oidcClientId":  "<client-id>",
  "gatewayUrl":    "https://gateway-dev.example.com",
  "adminApiUrl":   "https://admin-api-dev.example.com",
  "caBundle":      ""
}
'@
[IO.File]::WriteAllText("$PWD\packaging\site-config.json", $c)
```

확인 — 넣은 값이 그대로 보여야 합니다.

```powershell
Get-Content .\packaging\site-config.json -Raw
```

### 1-3. 기본 오버레이 넣기 — `packaging\site-extra.json`

`ENABLE_TOOL_SEARCH=true` 한 줄이라 설치한 PC 는 MCP 도구 정의를 매 요청에 싣지 않습니다
(도구 100개 실측 기준 요청당 입력 ~180K → ~30K). 프록시·권한 같은 사내 값이 더 있으면 같은
파일에 키를 덧붙입니다.

▶ **실행** · 빌드 PC — 🔵 일반 PowerShell

```powershell
cd C:\build\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway\installer
$j = '{ "managed": { "env": { "ENABLE_TOOL_SEARCH": "true" } } }'
[IO.File]::WriteAllText("$PWD\packaging\site-extra.json", $j)
```

확인 — 넣은 값이 그대로 보여야 합니다.

```powershell
Get-Content .\packaging\site-extra.json -Raw
```

⚠️ 이 파일은 그대로 exe 안에 실려 `setup` 때 **BOM 없는 UTF-8** 로만 읽힙니다. `>` 나
`Set-Content -Encoding UTF8` 로 만들면 BOM 이 붙어 **조용히 무시**됩니다 — 위 `WriteAllText`
를 쓰는 이유입니다. 파일이 없으면 빌드는 그대로 되고 이 오버레이만 빠집니다.

### 1-4. 빌드 실행

▶ **실행** · 빌드 PC — 🔵 일반 PowerShell

```powershell
cd C:\build\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway\installer
powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1
```

스크립트가 자기 위치를 기준으로 경로를 잡으므로 어느 폴더에서 실행해도 됩니다. `.build-venv` 를
만들어 의존성을 설치하고, PyInstaller 로 exe 3개를 만든 뒤 Inno Setup 으로 묶습니다.
결과물은 `dist\installer\gateway-cli-setup-<ver>.exe` 하나이고, 직원 PC 로 배포되는 것도
이 exe 하나뿐입니다.

## 2. 배포 — 설치 파일 전달

설치 파일은 **그 PC 에서 4)·5) 를 실행할 사람**에게 전달합니다. 둘 다 관리자 권한이 필요합니다.

| 대상 | 방식 | 결정 |
|---|---|---|
| 우리(테스트·소규모) | 파일을 그 PC 의 관리자에게 전달 → 관리자가 §3 진행 | 지금 이것으로 |
| 고객(다수 PC) | 고객 IT 배포 도구로 무인 설치(관리자 컨텍스트) → 이어서 §3 | 고객 IT 결정 |

**설치 파일 실행** — 먼저 받은 파일이 있는 폴더로 갑니다. 보통은 다운로드 폴더입니다.

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
cd $env:USERPROFILE\Downloads
```

그 폴더에서 실행합니다(탐색기 더블클릭도 같습니다). 미서명 테스트 빌드면 SmartScreen 경고가
뜨므로 "추가 정보" → "실행" 으로 진행합니다.

▶ **실행** · 🔴 관리자 PowerShell

```powershell
.\gateway-cli-setup-<ver>.exe
```

마법사: 설치 위치 기본값 유지 → "Add to PATH" 켜짐 유지 → Install → Finish.
고객 IT 배포 도구로 무인 설치할 때(기본값 그대로 적용):

▶ **실행 (무인)** · 관리자 컨텍스트

```powershell
.\gateway-cli-setup-<ver>.exe /VERYSILENT /NORESTART
```

설치가 끝나면 사용자 PC 는 이렇게 됩니다(수동·무인 동일):

| 항목 | 결과 |
|---|---|
| 프로그램 폴더 | `C:\Program Files\GatewayCLI` (exe 3개 + 공유 런타임) |
| 명령 실행 | 새 터미널 어디서나 `gateway-cli`·`api-key-helper`·`statusline` 을 실행할 수 있습니다 (PATH 자동 등록) |
| 설치된 앱 목록 | Windows *설정 → 앱 → 설치된 앱* 에 "LLM Gateway CLI" 로 표시됩니다 |
| 권한 | 설치 자체가 관리자 권한을 요구합니다(마법사 대화상자에서 사용자 설치 선택 가능) |

## 3. 게이트웨이 설정 적용 (관리자, PC 당 1회)

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
gateway-cli setup --model claude-sonnet-5
```

Claude Code 의 관리형 설정 파일을 써서 요청이 게이트웨이로 가게 만드는 단 하나의 스위치입니다.

| 기록 위치 | 기록되는 것 |
|---|---|
| `C:\Program Files\ClaudeCode\managed-settings.json` | `apiKeyHelper`(절대경로) · `model` · `env.ANTHROPIC_BASE_URL` · `env.ADMIN_API_URL` · `env.OIDC_*` · `env.OTEL_*` · `statusLine` |
| `%USERPROFILE%\.claude\settings.json` | 위 값 중 사용자 범위 일부 |
| 사용자 환경변수(User) | `ANTHROPIC_BASE_URL` · `ADMIN_API_URL` · `OIDC_ISSUER_URL` · `OIDC_CLIENT_ID` — 끄려면 `--no-persist-env` |

**Claude Code 가 아직 설치되지 않았어도 지금 실행합니다.** 설정을 먼저 깔아 두면 사용자가
나중에 설치해도 그대로 적용됩니다.

**관리자 권한이 필요합니다** — `C:\Program Files\ClaudeCode` 는 표준 사용자에게 읽기
전용입니다. 설치 파일에 사내 접속 정보가 들어 있으므로 주소를 손으로 넣을 일은 없고, 고르는 값은
보통 `--model` 하나입니다.

모델 선택 목록까지 PC 에 고정하고 싶을 때만 `--available-models` 를 줍니다. 목록 밖의 값은
`setup` 이 거부하므로 오타는 막히지만, 모델을 새로 등록하면 그 PC 들은 `setup` 을 다시 돌려야
합니다. 평소에는 `--model` 만 주고, 허용 범위는 서버의 팀·사용자 허용 목록에 맡깁니다.

▶ **실행 (선택)** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
$m = "claude-sonnet-5,claude-opus-5,claude-opus-4-8,claude-haiku-4-5-20251001"
gateway-cli setup --model claude-sonnet-5 --available-models $m
```

**다음** — 사용자가 본인 세션에서 Claude Code 설치 → `login` → 사용(§4).

## 4. 사용자 단계 (사용자당 1회)

Claude Code 본체는 게이트웨이 CLI 와 별개입니다. 사용자 폴더에만 쓰므로 관리자 권한이
필요 없습니다.

▶ **실행** · 사용자 PC — 🔵 일반 PowerShell

```powershell
irm https://claude.ai/install.ps1 | iex
```

PATH 자동 등록이 실패한 사례가 있습니다. 새 창에서 `claude --version` 이 안 잡히면 한 번
실행합니다.

▶ **실행** · 🔵 일반 PowerShell

```powershell
$p="$env:USERPROFILE\.local\bin"
$u=[Environment]::GetEnvironmentVariable("PATH","User")
[Environment]::SetEnvironmentVariable("PATH","$p;$u","User")
```

로그인은 기본 브라우저를 열어 회사 계정(OIDC)으로 합니다. 토큰은 사용자별
`%LOCALAPPDATA%\gateway-cli` 에 저장되므로 사용량도 사용자별로 집계됩니다.

▶ **실행** · 🔵 일반 PowerShell

```powershell
gateway-cli login
```

콜백 포트 기본값은 8090 이고, 이 배포에 등록된 포트는 8090·8091 입니다. 8090 이 점유돼 있으면
`gateway-cli login --redirect-port 8091` 로 실행합니다.

▶ **실행** · 🔵 일반 PowerShell

```powershell
claude
```

## 5. 검증 체크리스트

**§3 직후 — 관리자**

| 확인 | 기준 |
|---|---|
| CLI 설치 | `gateway-cli` 가 `C:\Program Files\GatewayCLI\` 에서 실행됩니다 |
| 관리형 설정 | `managed-settings.json` 의 `apiKeyHelper` 가 절대경로이고 `ANTHROPIC_BASE_URL` 이 배포 접속 정보와 일치합니다 |
| 점검 명령 | `gateway-cli verify` 가 전 항목을 통과합니다 |
| 버전 | `gateway-cli version` 이 설치 파일 계열(0.2.0 이상)을 가리킵니다 |

▶ **실행** · 🔴 관리자 PowerShell

```powershell
type "C:\Program Files\ClaudeCode\managed-settings.json"
```

▶ **실행** · 🔵 일반 PowerShell

```powershell
gateway-cli version
gateway-cli verify
gateway-cli env
```

`verify` 는 설정과 연결을 점검하고, `env` 는 지금 실제로 적용된 값을 그대로 보여 줍니다 —
설정 파일이 여러 tier 로 겹칠 때 어느 값이 이겼는지 이 출력으로 확인합니다.

**사용자 단계 후 — 사용자 본인 세션**: `claude` 의 `/status` 에서 `Anthropic base URL` 이
게이트웨이 주소로, `Auth token` 이 `apiKeyHelper` 로 보이고 첫 질의에 응답이 오면 종단
완료입니다.

## 6. 업그레이드 · 되돌리기 · 제거

업그레이드는 새 `setup.exe` 를 덮어 실행하면 됩니다(`AppId` 동일, 설정·토큰 유지). 제거할
필요는 없습니다.

| 목적 | 명령 | 권한 |
|---|---|---|
| 게이트웨이 설정만 끄기 | `gateway-cli disable` | 🔴 관리자 |
| 설정·환경변수·토큰까지 원복 | `gateway-cli clear` (`--dry-run`·`--keep-tokens`·`--keep-os-env`) | 🔵 사용자 |
| 프로그램 제거 | `gateway-cli uninstall --clear-first` 또는 *설정 → 앱* 에서 제거 | 🔴 관리자 |

⚠️ 이 설치 파일에는 Codex 연동 서브커맨드(`gateway-cli codex …`)도 들어 있습니다. 이 문서의
범위는 아니지만, 그 기능을 쓴 PC 는 프로그램을 제거하기 **전에** `gateway-cli codex revert` 를
먼저 실행해야 합니다. 제거 프로그램은 Codex 소유 파일(`~/.codex/config.toml`)을 건드리지 않고,
실행 파일이 사라지면 되돌릴 명령도 같이 사라집니다.

`setup` 은 파일을 고치기 전에 `%LOCALAPPDATA%\gateway-cli\backups\` 에 타임스탬프 스냅샷을
남깁니다. 되돌릴 때는 원하는 `.bak` 를 원래 위치로 복사합니다.

## 7. 함정

| 증상 | 원인 | 조치 |
|---|---|---|
| SmartScreen·EDR 이 설치를 막습니다 | 미서명 빌드 | "추가 정보 → 실행" 또는 사내 인증서로 서명해 재배포 |
| `setup` 이 쓰기에서 실패합니다 | 일반 창에서 실행 | 제목 표시줄에 "관리자" 가 있는 창에서 재실행 |
| 로그인 화면에서 오류가 납니다 | 등록되지 않은 콜백 포트 | `--redirect-port 8091` (등록 포트 8090·8091) |
| 같은 PC 의 Cowork 가 키 발급에 실패합니다 | `setup` 이 남긴 사용자 환경변수를 Cowork helper 가 먼저 읽습니다 | 두 클라이언트가 같은 배포를 보게 하거나 `--no-persist-env` 로 설치 |
| 설치 직후 `apiKeyHelper failed` 트레이스백이 보입니다 | 설정이 있는 PC 에 Claude Code 설치 → helper 시험 호출 | 로그인 전이면 정상입니다. `login` 후 `claude` 가 응답하면 문제가 아닙니다 |
| 설치 후 `ENABLE_TOOL_SEARCH` 가 설정에 없습니다 | `site-extra.json` 이 UTF-16 이거나 BOM 이 붙어 조용히 무시됨 | `[IO.File]::WriteAllText` 로 다시 만들고 재빌드 — 확인은 설치 후 `managed-settings.json` 의 키 |
| `login`·`verify` 가 TLS 오류를 냅니다 | 사내 프록시가 TLS 를 끊음 | 빌드 때 `caBundle` 에 사내 CA PEM 경로를 넣어 재빌드 |

## 8. 진행 상태 (2026-09-23)

- 테스트용 Windows 머신에 설치기 소스를 배치하고 `site-config.json`(이 배포 접속 정보)을 작성한
  단계까지 마쳤습니다. 전송본은 원본과 SHA256 이 일치합니다.
- 빌드·설치·`setup`·로그인은 아직 실행하지 않았습니다. 실행 후 결과와 `/status` 판독, 같은
  PC 의 Cowork 동작 확인 결과를 이 문서에 추가합니다.
- 테스트 머신에는 Python 3.12·git·Inno Setup 6 이 이미 있어 추가 설치가 필요 없었습니다.

## 9. 부록 — 참고

**벤더 문서 4종** — 소스에 함께 들어 있습니다. `entrypoints/gateway-cli-v2/docs/` 의
`CONFIG_ITEMS_AND_DEFAULTS.md`(설정 키 전체), `FILE_AND_ENV_OPERATIONS.md`(어느 파일·
환경변수를 건드리는가), `PROXY_PRECEDENCE.md`, `OTEL_PRECEDENCE.md`. 키 하나의 우선순위가
궁금할 때 그쪽을 봅니다.

**사내 프록시 검사 값 3개** — `-ExpectedProxyUrl`·`-NoProxyValue`·`-ForbiddenNoProxyToken`
은 `site-config.json` 으로 못 넣습니다. `build.ps1` 파라미터나 `GATEWAY_CLI_DEFAULT_*`
환경변수로만 들어갑니다.

**빌드 PC 가 인터넷과 끊겨 있으면** — 같은 Windows·Python 버전의 연결된 PC 에서 wheel 캐시를
만들어 옮긴 뒤 `-WheelDir` 로 지정합니다.
