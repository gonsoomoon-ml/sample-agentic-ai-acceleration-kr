# Cowork 설치기 — 사용자 가이드 (Windows)

```
readiness check → Claude Desktop 설치 → login → 사용 → verify
```

**전제** — 관리자가 이 PC 에 설치기와 `setup` 까지 끝냈다([관리자 E2E](cowork-installer-admin-e2e-windows.md) §3).
값을 채우거나 관리자 권한이 필요한 일은 없다. **모든 명령은 🔵 일반 PowerShell(본인 계정)** 에서 한다.
필요한 것 = 로그인 계정 하나. 어느 쪽인지는 관리자에게 확인한다.

- **사내 인증(ADFS·Okta 등) 연동 배포** — 평소 쓰는 사내 계정으로 로그인한다.
관리자가 그 사용자를 Cognito **팀 그룹**에 넣어 두어야 한다(첫 로그인 후 자동 생성됨).
- **연동이 없는 배포(현재 US 배포)** — 관리자가 만들어 준 **Cognito 이메일 + 임시 비밀번호**로
로그인하고, 첫 로그인에서 새 비밀번호를 정한다.

## 1. Cowork 실행 가능 여부 확인 (Readiness Check)

점검 도구(약 2 MB)는 **관리자에게 받는다** 
직접 받을 수 있는 망이면 **브라우저**로 아래 주소를 연다.

📋 **참고** — 브라우저로 여는 **주소**다 (터미널에서 실행하는 게 아니다)

```
https://claude.ai/api/desktop/win32/x64/cowork-readiness-check/latest/redirect
```

📋 **참고** — 점검 도구의 **결과 문장**이다

```
This computer is ready for Cowork                        ← 이 문장이면 §2 로
This computer does not meet the requirements for Cowork
```

**통과하지 못했다면** 도구가 어느 항목이 왜 막혔는지와 고치는 명령까지 알려준다. 자주 걸리는 둘:

- `Hardware virtualization` — 펌웨어(BIOS/UEFI)에서 가상화가 꺼져 있음
- `Virtual Machine Platform` — Windows 기능이 안 켜져 있음

**고친 뒤에는 반드시 재부팅하고 다시 실행한다.** 기능만 켜고 확인하면 여전히 실패로 나온다.
⚠️ 일반 가상 머신 위에서는 대개 실패한다 — 가상화 기능을 손님에게 넘기지 않는 환경에서는 통과할 수 없다.

파일을 관리자에게 받았더라도 Cowork 는 세션마다 `downloads.claude.ai` 에 닿아야 한다(§5 `cowork-egress`).
막혀 있으면 관리자에게 허용을 요청한다.

## 2. Claude Desktop 설치

먼저 이미 있는지 본다.

▶ **실행** · 🔵 일반 PowerShell — 본인 PC

```powershell
Get-AppxPackage -Name "*laude*" | Select-Object Name, Version
```

출력이 있으면 §3 으로. 비어 있으면 Claude Desktop offline `.msix`(약 1.8 GB)를
**관리자에게 받는다** — 기업 환경의 보통 방식이다. 직접 받을 수 있는 망이면 **브라우저**로
아래 주소를 연다.

📋 **참고** — 브라우저로 여는 **주소**다

```
https://claude.ai/api/desktop/win32/x64/offline/latest/redirect
```

⚠️ **반드시 이 offline 주소의** `.msix` **를 받는다.** `claude.com/download` 는 `.exe` 를 주는데, 그것으로 깔면
Claude Desktop 은 설치되지만 **Cowork 가 빠진다.** ⚠️ **404 가 나면 잠시 뒤 다시 받는다** — 새 버전 직후
offline 판이 아직 안 올라온 구간이 있다.

▶ **실행** · 🔵 일반 PowerShell — 본인 PC (`<받은 파일명>` 을 실제 이름으로)

```powershell
Add-AppxPackage -Path "$env:USERPROFILE\Downloads\<받은 파일명>.msix"
```

몇 분 걸리고, 진행 표시가 없어도 정상이며, 성공하면 아무 출력이 없다. 확인은 위 `Get-AppxPackage` 로.
MSIX 는 **사용자별 설치**라 본인 계정 세션에서 실행해야 그 계정에 깔린다.

## 3. 로그인

▶ **실행** · 🔵 일반 PowerShell — 본인 PC

```powershell
gateway-cli-cowork login --redirect-port 8091
```

브라우저에 로그인 화면이 뜬다.

- 사내 인증 연동 배포 — 사내 계정으로 로그인한다.
- 연동이 없는 배포 — 관리자가 준 이메일·임시 비밀번호를 넣고, 첫 로그인에서 새 비밀번호를 정한다.

`Login successful` 이 나오면 끝 — 토큰은 `%LOCALAPPDATA%\gateway-cli-cowork\` 에 본인 계정 전용으로 저장된다.

- **포트는 8090·8091·8092 중에서만** 고른다 — Cognito 에 등록된 콜백이 그 3개뿐이다. 다른 포트면
브라우저에 Cognito 오류 페이지가 뜬다.
- ⚠️ `Login failed` **와 HTTP 오류 코드가 뜨면** — 로그인은 됐고 그다음 **VK 발급**이 막힌 것이다. 대개 본인 PC 의
공인 IP 가 admin-api 허용 목록에 없어서다. 아래 값을 관리자에게 보내 등록을 요청한다:

▶ **실행** · 🔵 일반 PowerShell — 본인 PC

```powershell
(irm https://checkip.amazonaws.com).Trim()
```



## 4. 사용

1. Claude Desktop 을 열고 **Cowork 탭** → 모델 선택기에 관리자가 등록한 alias 가 보이는지 확인.
2. 아무 모델이나 골라 `hi` 를 보낸다. 응답이 오면 **PC → 게이트웨이 → Bedrock** 이 전부 통한 것이다.

Chat 탭만 보이고 Cowork 탭이 없으면 관리자 정책이다(관리자 E2E §6) — 관리자에게 문의.

## 5. 확인 — verify

▶ **실행** · 🔵 일반 PowerShell — 본인 PC

```powershell
gateway-cli-cowork verify
```

`overall: warn` **이 이 배포의 정상 종착 상태다** — 사내 프록시가 없어 `cowork-ca` 만 warn 이고 나머지는 ok 다.
(실측 후 갱신)


| 검사                         | 기대                                | 뜻                                          |
| -------------------------- | --------------------------------- | ------------------------------------------ |
| `cowork-config`            | ✓ `HKLM\SOFTWARE\Policies\Claude` | 관리자가 깐 정책 6키를 읽음                           |
| `cowork-hklm`              | ✓                                 | HKLM 스코프 배포라 충돌 아님                         |
| `cowork-credential`        | ✓ helper-script                   | `C:\Gateway-CLI-Cowork\api-key-helper.exe` |
| `cowork-inference-url`     | ✓ HTTP 401                        | 게이트웨이에 닿음 (VK 없이 401 이 정답)                 |
| `cowork-egress`            | ✓ HTTP 403                        | `downloads.claude.ai` 도달                   |
| `cowork-ca`                | ⚠ warn                            | 사내 프록시 CA 없음 — **이 배포에선 정상**               |
| `oidc-tokens` / `vk-cache` | ✓ valid                           | §3 로그인이 유효                                 |


실패했을 때:

- `cowork-config` ✗ `no Cowork managed config` → 관리자가 `setup` 을 안 한 PC — 관리자 E2E §3
- `oidc-tokens` ✗ 또는 `vk-cache` ✗ → §3 `login` 다시
- `cowork-inference-url` ✗ / `cowork-egress` ✗ → 네트워크(VPN·방화벽) — 관리자에게



## 6. 문제가 있을 때

- **한동안 잘 쓰다가 응답이 안 옴** — 로그인 만료. §3 `login` 을 다시 하고 Claude Desktop 을 재시작.
- **모델 목록이 비어 있음 / 원하는 모델이 없음** — 관리자가 정책에 넣은 alias 만 보인다 — 관리자에게.
- **Cowork 세션이 시작되지 않음** — §1 점검 도구를 다시 돌린다(가상화 설정 변경 후 미재부팅이 흔함).
- **되돌리기·제거** — 관리자 작업: [제거 가이드](cowork-installer-uninstall-windows.md).

