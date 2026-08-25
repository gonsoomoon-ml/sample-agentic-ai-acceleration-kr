# Cowork 설치기 — 되돌리기·제거 (Windows)

범위가 작은 것부터 큰 것 순으로 고른다. 🔴 = 관리자 PowerShell(HKLM 스코프는 관리자 필수, 이 PC 전 사용자에게 영향).


| 목적                                       | 명령                                           |
| ---------------------------------------- | -------------------------------------------- |
| 1st-pary 로 원복: 게이트웨이 설정만 원복 (바이너리·토큰 유지) | `gateway-cli-cowork disable`                 |
| 설정·토큰·백업 전부 정리 (바이너리 유지)                 | `gateway-cli-cowork clear`                   |
| 전부 제거 (설정 원복 → 바이너리 삭제)                  | `gateway-cli-cowork uninstall --clear-first` |


## 1. 설정만 원복 — 1st-party(claude.ai 직접) Cowork 로 전환할 때

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
gateway-cli-cowork disable
```

→ Claude Desktop 이 재시작되고 **claude.ai 로그인 화면**이 뜨면 게이트웨이 해제 완료. 그 계정으로 Anthropic 에 직접 추론한다.

IT 가 탭 제한을 걸어 둔 PC 면 Cowork 탭을 되살린다:

▶ **실행 (탭 제한 PC 만)** · 🔴 관리자 PowerShell

```powershell
reg add HKLM\SOFTWARE\Policies\Claude /v coworkTabEnabled /t REG_SZ /d true /f
```

게이트웨이로 되돌아오려면 `gateway-cli-cowork setup`.

## 2. 전부 제거

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
gateway-cli-cowork uninstall --clear-first
```

확인 프롬프트에 `y`. (미리보기: `--dry-run`, 프롬프트 생략: `-y`)

확인:

▶ **실행** · 🔴 관리자 PowerShell

```powershell
Test-Path C:\Gateway-CLI-Cowork
```

→ `False`

▶ **실행** · 🔴 관리자 PowerShell

```powershell
Get-ItemProperty HKLM:\SOFTWARE\Policies\Claude
```

→ `inference*` 키 없음 (IT 가 넣은 탭 제한 값은 남을 수 있음 — 정상)

▶ **실행** · 🔴 관리자 PowerShell — 새 창

```powershell
Get-Command gateway-cli-cowork, api-key-helper -EA SilentlyContinue
```

→ 출력 없음 (새 창에서)

## 3. Claude Desktop 앱까지 제거 (선택)

앱(msix)과 앱 데이터(약 10 GB)는 설치기와 무관하다 → [수동 제거 가이드](../manual/cowork-client-uninstall-windows-manual.md) §3·§4.

## 4. "설치된 앱"에서 먼저 지워 버렸다면

exe 가 없어 `clear` 를 실행할 수 없으므로 손으로 정리한다:

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
reg delete HKLM\SOFTWARE\Policies\Claude /f
```

▶ **실행** · 🔴 관리자 PowerShell

```powershell
Remove-Item "$env:LOCALAPPDATA\gateway-cli-cowork" -Recurse -Force -EA SilentlyContinue
```

▶ **실행** · 🔴 관리자 PowerShell

```powershell
Remove-Item "$env:ProgramData\gateway-cli-cowork" -Recurse -Force -EA SilentlyContinue
```

→ Claude Desktop 재시작. (`reg delete` 는 키 전체를 지우므로 IT 탭 제한 값도 함께 사라진다 — GPO 면 다음 갱신 때 복원됨.)

---



## 참고

- **토큰은 로그인한 계정에 남는다** — `clear` 는 실행한 계정의 `%LOCALAPPDATA%\gateway-cli-cowork`(토큰·VK 캐시)만 지운다. 관리자 계정으로 `clear` 했다면 직원 계정의 토큰은 그대로다 → 직원이 `gateway-cli-cowork logout`(관리자 권한 불필요) 하거나, 관리자가 그 프로필의 폴더를 지운다. (정책은 이미 사라졌으므로 남아 있어도 인증에 쓰이지 않는다.)
- `clear` 가 지우는 것: 정책 키(설치 전 스냅샷으로 원복) · OIDC 토큰·VK 캐시(`%LOCALAPPDATA%\gateway-cli-cowork`) · 머신 상태(`%ProgramData%\gateway-cli-cowork`) · 이 도구의 백업. `--keep-tokens` 로 토큰 보존 가능.
- IT 가 넣은 탭 제한 값(`coworkTabEnabled` 등)은 설치기가 관리하지 않는다 — IT 정책 쪽에서 `reg delete HKLM\SOFTWARE\Policies\Claude /v coworkTabEnabled /f`.
- 업그레이드는 제거 없이 새 `setup.exe` 를 덮어 실행(`AppId` 동일) — 정책 키·토큰 유지.

