# Cowork 설치기 — 관리자 End-to-End (Windows)

```text
┌─ BUILD PC   (admin) ───────────────────────────────────────────┐
│ 1) git clone -b feat/cowork-installer-import                   │
│ 2) edit  packaging\site-config.json   (customer values)        │
│ 3) run   packaging\build.ps1                                   │
│    out:  dist\installer\gateway-cli-cowork-setup-<ver>.exe     │
└───────────┬────────────────────────────────────────────────────┘
            │ 설치 파일 1개를 전달
            │
            ▼
┌─ USER PC   (admin)   -- once per PC ───────────────────────────┐
│ 4) run gateway-cli-cowork-setup-<ver>.exe   [installer file]   │
│    -> C:\Gateway-CLI-Cowork + PATH + scope=machine             │
│                                                                │
│ 5) run gateway-cli-cowork setup             [CLI command]      │
│    -> HKLM\SOFTWARE\Policies\Claude : inference* x6            │
└───────────┬────────────────────────────────────────────────────┘
            │ 정책이 깔린 PC 를 사용자에게
            │
            ▼
┌─ USER PC   (each user)   -- once per user ─────────────────────┐
│ 6) run Cowork readiness check  (VM/virtualization ready?)      │
│ 7) install Claude Desktop  (offline .msix)                     │
│ 8) run gateway-cli-cowork login   (OIDC -> VK cache)           │
│ 9) use Chat / Cowork tab                                       │
└────────────────────────────────────────────────────────────────┘
4) 가 5) 를 자동 실행하지 않는다 — 설치 후 사람이 따로 실행한다.
```

| 단계               | 누가        | 문서                                              |
| ---------------- | --------- | ----------------------------------------------- |
| 빌드               | 빌드 담당 관리자 | [빌드 가이드](cowork-installer-build-windows.md)     |
| 배포·정책 적용·검증·탭 제한 | 배포 관리자    | 이 문서                                            |
| 적합성 확인·앱 설치·로그인·사용 | 최종 사용자    | [사용자 가이드](cowork-installer-user-windows.md)     |
| 되돌리기·제거          | 관리자       | [제거 가이드](cowork-installer-uninstall-windows.md) |

## 0. 빌드 전 결정 4가지

| 결정                      | 권장                                                                         | 한 줄 이유                                         |
| ----------------------- | -------------------------------------------------------------------------- | ---------------------------------------------- |
| 정책 스코프                  | **HKLM(머신 전역)** — 설치기 기본값                                                  | 필수                                             |
| 자격 방식                   | **helper-script** — 기본값                                                    | TBD                                            |
| 코드 서명                   | 테스트 = 미서명 / 정식 배포 = 고객 IT 표준(사내 코드서명 인증서 `-SignThumbprint`, 없으면 EDR 허용 목록) | 미서명 PyInstaller exe 는 SmartScreen 경고·EDR 오탐 대상 |
| Claude Desktop App 탭 구성 | **기본 = Chat + Cowork** / Chat 전용 고객 = IT 정책으로 제한(§6)                       | 설치기 빌드는 하나로 유지, 고객별 차이는 정책 값으로                 |

## 1. 빌드

[빌드 가이드](cowork-installer-build-windows.md) §0–§4. 결과물 = `dist\installer\gateway-cli-cowork-setup-<ver>.exe` 하나.

## 2. 배포 — 설치 파일 전달

설치 파일은 **그 PC 에서 4)·5) 를 실행할 사람**에게 전달한다. 둘 다 관리자 권한이 필요하므로 받는 사람은 보통 관리자다(사용자가 본인 PC 의 로컬 관리자라면 사용자 본인).

| 대상 | 방식 | 결정 |
|---|---|---|
| 우리(테스트·소규모) | 파일을 그 PC 의 관리자에게 전달 → 관리자가 §3 진행 | 지금 이것으로 |
| 고객(다수 PC) | 고객 IT 의 소프트웨어 배포 도구로 무인 설치(관리자 컨텍스트) → 이어서 §3 | 고객 IT 결정 |

[사용자 가이드](cowork-installer-user-windows.md) §1·§2 의 두 파일도 함께 배포한다(기업 환경의 보통 방식) —
Cowork 점검 도구(`…/cowork-readiness-check/latest/redirect`, ~2 MB)·Claude Desktop offline `.msix`(`…/win32/x64/offline/latest/redirect`, ~1.8 GB).
단 `downloads.claude.ai:443` 은 Cowork 세션마다 필요하므로 파일 배포와 별개로 허용 목록에 넣어야 한다.

**설치기 실행** — 먼저 받은 파일이 있는 폴더로 간다. 보통은 다운로드 폴더:

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
cd $env:USERPROFILE\Downloads
```

빌드 PC 와 같은 PC 에서 바로 설치하면 빌드 산출 폴더로:

▶ **실행 (같은 PC 일 때)** · 🔴 관리자 PowerShell

```powershell
cd $env:USERPROFILE\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway\cowork-installer\installer\dist\installer
```

그 폴더에서 실행한다(탐색기 더블클릭도 같다). 미서명 테스트 빌드면 SmartScreen 경고 → "추가 정보" → "실행".

▶ **실행** · 🔴 관리자 PowerShell

```powershell
.\gateway-cli-cowork-setup-<ver>.exe
```

마법사: 정책 스코프 **HKLM(machine-wide)** 기본값 유지 → "Add to PATH" 켜짐 유지 → Install → Finish.
고객 IT 배포 도구로 무인 설치할 때(기본값 그대로 적용):

▶ **실행 (무인)** · 관리자 컨텍스트

```powershell
.\gateway-cli-cowork-setup-<ver>.exe /VERYSILENT /NORESTART
```

설치가 끝나면 사용자 PC 는 이렇게 된다(수동·무인 동일):

| 항목        | 결과                                                                                                                                 |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| 프로그램 폴더   | `C:\Gateway-CLI-Cowork` (exe 2개 + 런타임)                                                                                             |
| 명령 실행     | 새 터미널 어디서나 `gateway-cli-cowork` 가 바로 실행됨 (PATH 자동 등록)                                                                              |
| 설치된 앱 목록  | Windows *설정 → 앱 → 설치된 앱* 에 "LLM Gateway CLI (Cowork)" 로 표시됨                                                                        |
| 정책 스코프 기록 | 마법사에서 **HKLM(machine-wide)** 을 선택(기본값) → `C:\Gateway-CLI-Cowork\registry-scope.conf` = `machine` 으로 저장 → 이후 `setup` 이 읽어 HKLM 에 기록 |

## 3. 게이트웨이 정책 적용 (관리자, PC 당 1회)

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
gateway-cli-cowork setup
```

`HKLM\SOFTWARE\Policies\Claude` 에 `inference*` 6키를 기록한다 — 사용자 PC 의 Claude Desktop 이 claude.ai 대신 게이트웨이를 보게 만드는 단 하나의 스위치다.

**Claude Desktop 이 아직 설치되지 않았어도 지금 실행한다.** 정책을 먼저 깔아 두면 사용자가 나중에 앱을 처음 실행할 때 그대로 적용된다(앱이 이미 있는 PC 면 자동 재시작해 즉시 반영).

**관리자 권한이 필요하다** — `SOFTWARE\Policies` 는 표준 사용자에게 읽기 전용이라 표준 사용자는 실행할 수 없다. 누가 하느냐는 사용자의 권한에 따라 갈린다.

| 사용자가                    | 관리자가 하는 일                                  | 사용자가 하는 일                             |
| ----------------------- | ------------------------------------------ | ------------------------------------- |
| 본인 PC 의 로컬 관리자 (개발자 PC) | —                                          | 설치 exe → `setup`(UAC 자동 상승) → `login` |
| 표준 사용자 (일반 사내 PC)       | 설치 exe + `setup` — **PC 당 1회, 전 사용자에게 적용** | `login` 만 (본인 세션, 사용자당 1회)            |

HKLM 은 머신 전역이라 `setup` 은 PC 당 1회면 되고, 사용자 신원(OIDC 토큰·VK 캐시)은 사용자별 `%LOCALAPPDATA%` 에 따로 저장된다 → 사용량도 사용자별로 집계된다.

**다음** — 사용자가 본인 세션에서 Claude Desktop 설치 → `login` → 사용 ([사용자 가이드](cowork-installer-user-windows.md)).

## 4. 검증 체크리스트

**§3 직후 — 관리자**

| 확인     | 기준                                                                                       |
| ------ | ---------------------------------------------------------------------------------------- |
| CLI 설치 | `gateway-cli-cowork` 가 `C:\Gateway-CLI-Cowork\` 에서 실행됨                                   |
| 정책 스코프 | `registry-scope.conf` = `machine`                                                        |
| 정책 키   | `HKLM\SOFTWARE\Policies\Claude` 의 `inference*` 6키가 빌드 때 넣은 고객 좌표(`site-config.json`)와 일치 |

**사용자 단계 후 — 사용자 본인 세션**: `gateway-cli-cowork verify` 전 항목 ✓ → Claude Desktop 에서 모델 선택 후 응답이 옴. 명령·기대 출력은 [사용자 가이드](cowork-installer-user-windows.md) §5.

## 5. 업그레이드 · 되돌리기 · 제거

업그레이드 = 새 `setup.exe` 를 덮어 실행(`AppId` 동일, 정책 키·토큰 유지). 제거 없이 된다.

되돌리기(1st-party 전환)·전부 제거·"설치된 앱에서 먼저 지운 경우" 복구는 → [제거 가이드](cowork-installer-uninstall-windows.md).

## 6. 고객별 탭 제한 — Chat 전용 ↔ Cowork 활성화 (고객 IT 정책)

설치기 빌드는 그대로 두고, 고객 IT 가 같은 정책 키에 값을 추가·변경한다(`reg add` 또는 GPO).
🔴 관리자 PowerShell, `setup` 완료 후. 값 변경은 앱 **재시작 후** 반영된다.

### 6-1. Chat 전용으로 제한

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
reg add HKLM\SOFTWARE\Policies\Claude /v coworkTabEnabled /t REG_SZ /d false /f
```

▶ **실행** · 🔴 관리자 PowerShell

```powershell
reg add HKLM\SOFTWARE\Policies\Claude /v isClaudeCodeForDesktopEnabled /t REG_SZ /d false /f
```

▶ **실행** · 🔴 관리자 PowerShell

```powershell
gateway-cli-cowork relaunch
```

→ Claude Desktop 에 **Chat 탭만** 보이면 적용.

### 6-2. 나중에 Cowork 활성화

먼저 대상 PC 가 Cowork 를 돌릴 수 있는지 확인한다 — [사용자 가이드](cowork-installer-user-windows.md) §1 Readiness Check(`This computer is ready for Cowork`). Cowork 는 가상화 기능이 필요해 통과 못 하는 PC 에서는 탭을 켜도 세션이 시작되지 않는다.

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
reg add HKLM\SOFTWARE\Policies\Claude /v coworkTabEnabled /t REG_SZ /d true /f
```

▶ **실행** · 🔴 관리자 PowerShell

```powershell
gateway-cli-cowork relaunch
```

→ **Cowork 탭**이 나타나고 모델 선택 후 `hi` 응답이 오면 활성화 완료. (Claude Code for Desktop 도 켜려면 `isClaudeCodeForDesktopEnabled` 를 같은 방법으로 `true`.)

GPO 로 관리하는 조직은 같은 값 이름·문자열 값(`true`/`false`)을 정책으로 배포하고, 갱신 주기 후 앱을 재시작한다.

### 6-3. 확인

▶ **실행** · 🔴 관리자 PowerShell

```powershell
Get-ItemProperty HKLM:\SOFTWARE\Policies\Claude | Select coworkTabEnabled, isClaudeCodeForDesktopEnabled
```

---

