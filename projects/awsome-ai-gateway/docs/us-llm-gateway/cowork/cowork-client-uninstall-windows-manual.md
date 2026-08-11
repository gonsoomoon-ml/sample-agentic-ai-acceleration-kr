# Cowork(Claude Desktop 3P) 제거 — Windows - 수동설치시

**위에서부터 순서대로** 하십시오. 어디서 멈출지는 목적에 따라 다릅니다.


| 어디까지    | 결과                               | **Claude Code 도 쓰는 PC 라면** |
| ------- | -------------------------------- | -------------------------- |
| **1–2** | 게이트웨이 연결만 끊김 (앱은 남음)             | 영향 없음                      |
| **3–4** | Cowork 앱까지 제거                    | 영향 없음                      |
| **5**   | helper 삭제                        | 영향 없음                      |
| **6–7** | `gateway-cli`·토큰·저장소까지 제거        | 🔴 **Claude Code 도 끊깁니다**  |
| **8**   | Python·Git 등 사전 요구사항까지 = 설치 전 상태 | 🔴 위와 같음                   |




### 이 PC 에서 Claude Code 도 게이트웨이로 쓰고 있다면 — **5 에서 멈추십시오**

두 클라이언트는 **로그인 자원을 공유**합니다. 1–5 는 Cowork 전용이라 안전하고, **6 부터가 공유 자원**입니다.


| 자원                                                    | Cowork | Claude Code                                                             |
| ----------------------------------------------------- | ------ | ----------------------------------------------------------------------- |
| `HKLM\SOFTWARE\Policies\Claude` · Cowork 앱 · 앱 데이터    | ✅      | ❌ Claude Code 는 `C:\Program Files\ClaudeCode\managed-settings.d\` 를 봅니다 |
| `C:\ProgramData\llm-gateway\helper.cmd`               | ✅      | ❌ `api-key-helper` 를 직접 부릅니다                                            |
| `gateway-cli` **패키지** (`api-key-helper`·`statusline`) | ✅      | ✅ **공유**                                                                |
| `%USERPROFILE%\.gateway-cli` (로그인 토큰·VK 캐시)           | ✅      | ✅ **공유**                                                                |
| `%LOCALAPPDATA%\gateway-cli` (설정)                     | ✅      | ✅ **공유**                                                                |
| Python · git · PATH                                   | ✅      | ✅ **공유**                                                                |


⚠️ **6 을 하면 Claude Code 가 조용히 망가집니다** — `apiKeyHelper` 가 가리키는 실행파일과 상태줄(`statusline`)이 함께 사라지고, 로그인 토큰도 지워집니다. 오류 메시지가 Cowork 제거를 가리키지 않아 원인을 찾기 어렵습니다.

ℹ️ Claude Code 쪽을 **따로** 게이트웨이에서 떼려면 `gateway-cli disable` 하나면 됩니다(`50-gateway.json` 만 지웁니다). Cowork 제거와 무관하게 언제든 할 수 있습니다.


| 표시                           | 어느 창인가                                           |
| ---------------------------- | ------------------------------------------------ |
| ▶ 🔵 **실행 · 일반 PowerShell**  | 직원 본인 계정으로 그냥 연 창 (토큰·설치물이 여기 있습니다)              |
| ▶ 🔴 **실행 · 관리자 PowerShell** | **"관리자 권한으로 실행"** 으로 연 창 (제목 표시줄에 `관리자:` 가 보입니다) |


---



## 1. 앱 종료

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
Get-Process -Name "*laude*" -ErrorAction SilentlyContinue | Stop-Process -Force
```

창을 닫는 것만으로는 부족합니다. 하나라도 남으면 다음 단계가 반영되지 않습니다.

⚠️ `*laude*` **는 Claude Code CLI 의** `claude.exe` **에도 걸립니다.** 그 PC 에서 Claude Code 를 쓰는 중이면 세션이 함께 끊깁니다. 가려서 끄려면 먼저 목록을 보고 데스크톱 앱 것만 `Stop-Process -Id <id>` 하십시오.

```powershell
Get-Process -Name "*laude*" | Select Id,ProcessName,Path
```

---



## 2. 관리형 설정(레지스트리) 삭제

먼저 지금 값을 백업합니다. 되돌릴 때 이 파일을 더블클릭하면 됩니다.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
reg export "HKLM\SOFTWARE\Policies\Claude" "$env:USERPROFILE\claude-policy-backup.reg" /y
```

```powershell
Remove-Item "HKLM:\SOFTWARE\Policies\Claude" -Recurse -Force
```

**확인** — `False` 가 나와야 합니다.

```powershell
Test-Path "HKLM:\SOFTWARE\Policies\Claude"
```

⚠️ **값을 하나씩 지우지 마십시오.** 키가 남아 있으면 앱이 "머신 정책 있음"으로 세어 설정이 하나도 없는 상태로 동작합니다.

⚠️ 앱을 켜면 **claude.ai 로그인 화면**이 뜹니다 — 설치 때는 실패 신호였지만 **여기서는 성공 신호**입니다.

⚠️ 회사가 GPO·Intune 으로 관리하는 PC 면 다음 정책 적용 때 되살아납니다. IT 에 배포 제외를 요청하십시오.

---



## 3. 앱 제거 — **두 가지를 모두**

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
Get-AppxProvisionedPackage -Online | Where-Object DisplayName -like "*laude*" |
  ForEach-Object { Remove-AppxProvisionedPackage -Online -PackageName $_.PackageName }
```

```powershell
Get-AppxPackage -AllUsers -Name "*laude*" |
  ForEach-Object { Remove-AppxPackage -Package $_.PackageFullName -AllUsers }
```

**순서를 지키십시오 — 프로비저닝본이 먼저입니다.**

**확인** — 둘 다 아무것도 출력하지 않아야 합니다.

```powershell
Get-AppxPackage -AllUsers -Name "*laude*"
Get-AppxProvisionedPackage -Online | Where-Object DisplayName -like "*laude*"
```

---



## 4. 앱 데이터 삭제

▶ 🔵 **실행 · 일반 PowerShell** — 앱을 **쓴 본인 계정으로**

**이 단계를 건너뛰면 약 10 GB 가 그대로 남습니다.** 대부분이 Cowork 작업 환경 VM 이미지입니다 — `vm_bundles\claudevm.bundle\rootfs.vhdx` **8.1 GB**, `rootfs.vhdx.zst` 1.2 GB, `sessiondata.vhdx` 484 MB 등(2026-08-11 실측).

🔴 `Remove-Item` **만으로는 지워지지 않습니다** — Cowork 세션 폴더가 **260자 경로 한계**를 넘습니다(2026-08-11 실측). 아래 `robocopy` 로 비운 뒤 지웁니다.

```powershell
New-Item -ItemType Directory -Force "$env:TEMP\empty" | Out-Null
robocopy "$env:TEMP\empty" "$env:LOCALAPPDATA\Claude-3p" /MIR /NFL /NDL /NJH /NJS
Remove-Item "$env:LOCALAPPDATA\Claude-3p","$env:TEMP\empty" -Recurse -Force
```

```powershell
Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter "*laude*" |
  Remove-Item -Recurse -Force
```

**확인** — `False` 가 나와야 합니다. **이 줄을 거르지 마십시오.**

```powershell
Test-Path "$env:LOCALAPPDATA\Claude-3p"
```

---



## 5. credential helper 삭제

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
Remove-Item "C:\ProgramData\llm-gateway" -Recurse -Force
```

✅ **Claude Code 는 영향받지 않습니다.** `gateway-cli setup` 이 붙인 Claude Code 는 이 `.cmd` 가 아니라 `api-key-helper` 실행파일을 직접 부릅니다. helper 경로를 손으로 이쪽으로 바꿔 둔 클라이언트(Codex 등)가 있을 때만 확인하십시오.

---



## 6. `gateway-cli` 제거 + 토큰·설정 삭제

🔴 **이 PC 에서 Claude Code 도 게이트웨이로 쓴다면 6·7 을 하지 마십시오.** 여기서 지우는 것이 전부 공유 자원입니다(위 표). 5 까지가 Cowork 제거의 완결입니다.

⚠️ **설치할 때** `-SetupClaudeCode` **를 줬다면, 아래** `pip uninstall` **전에 이것부터 하십시오.** 패키지를 먼저 지우면 명령이 사라져 되돌릴 수 없습니다.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
Test-Path "C:\Program Files\ClaudeCode\managed-settings.d\50-gateway.json"
gateway-cli disable        # 위가 True 일 때만
```

▶ 🔵 **실행 · 일반 PowerShell** — **설치한 본인 계정으로** (관리자 창에서는 "설치되지 않음" 이 나옵니다)

```powershell
py -m pip uninstall -y gateway-cli
```

```powershell
Remove-Item "$env:USERPROFILE\.gateway-cli" -Recurse -Force -EA SilentlyContinue
Remove-Item "$env:LOCALAPPDATA\gateway-cli" -Recurse -Force -EA SilentlyContinue
```

⚠️ **두 번째 줄을 빠뜨리기 쉽습니다.** 토큰은 `%USERPROFILE%\.gateway-cli\`, 설정은 `%LOCALAPPDATA%\gateway-cli\` 로 **자리가 다릅니다.**

⚠️ **여기서 지워지는 토큰은 Claude Code 도 쓰는 것입니다.** 지웠다면 Claude Code 쪽은 `gateway-cli login` 을 다시 해야 살아납니다 — 그러려면 패키지도 다시 깔아야 합니다.

**확인** — 아무것도 안 나와야 합니다.

```powershell
Get-Command gateway-cli, api-key-helper -EA SilentlyContinue
```

---



## 7. 저장소·설치 파일 삭제

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
Remove-Item "$env:USERPROFILE\sample-agentic-ai-acceleration-kr" -Recurse -Force
Get-ChildItem "$env:USERPROFILE\Downloads" -Filter "*laude*" | Select Name,Length
```

목록을 확인한 뒤 지웁니다.

```powershell
Get-ChildItem "$env:USERPROFILE\Downloads" -Filter "*laude*" | Remove-Item -Force
```

설치 파일은 **관리자 계정의** `Downloads` 에 있을 수 있습니다(`.msix` 약 1.8 GB).

⚠️ **다시 설치할 가능성이 있으면** `.msix` **는 남겨 두십시오.** 새 버전 직후 내려받기 주소가 **404** 로 실패하는 구간이 있습니다.

---



## 8. 설치 전 상태로 — 사전 요구사항까지 제거

**설치 절차 1-⓪ 에서 직접 깔았을 때만** 하십시오. 원래 있던 Python·Git 이면 건너뜁니다.

🔴 **순서가 있습니다 — PATH 정리가 Python 제거보다 먼저입니다.** Python 을 먼저 지우면 `py` 가 없어져 **지울 PATH 경로를 구할 수 없습니다.**

### ① PATH 정리

⚠️ 이 경로는 `pip install --user` 로 깐 **다른 도구도 함께 씁니다.** 그런 것이 있으면 건너뛰십시오 — 남아 있어도 해가 없습니다.

▶ 🔵 **실행 · 일반 PowerShell** — 설치한 본인 계정으로

```powershell
$s = py -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))"
$cur = [Environment]::GetEnvironmentVariable("PATH","User")
[Environment]::SetEnvironmentVariable("PATH", (($cur -split ';' | Where-Object { $_ -and $_ -ne $s }) -join ';'), "User")
```

**사용자 PATH 만** 건드립니다. 시스템 PATH 는 손대지 않습니다.

### ② Python · Git

🔴 **창을 두 개 다 쓰게 됩니다 — 설치 범위가 서로 다릅니다.** winget 은 **사용자 범위 패키지를 관리자 창에서 지우지 못하고**, 머신 범위 패키지는 일반 창에서 지우지 못합니다. 실측에서 **Python 은 사용자 범위**(설치 때 `--scope machine` 을 줬는데도), **Git 은 머신 범위**(`C:\Program Files\Git`)였습니다.

**일반 창부터 시작하십시오.**

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
winget uninstall --id Python.Python.3.12 -e
winget uninstall --id Git.Git -e
```

거부되면 메시지가 어느 창으로 가라고 알려줍니다.


| 메시지                                                                                                     | 옮겨갈 창                 |
| ------------------------------------------------------------------------------------------------------- | --------------------- |
| `The package installed for user scope cannot be uninstalled when running with administrator privileges` | 🔵 **일반 PowerShell**  |
| 관리자 권한이 필요하다는 메시지                                                                                       | 🔴 **관리자 PowerShell** |


`No installed package found` 가 나오면 버전이 다르거나 설치 파일로 깐 경우입니다. 실제 이름을 보고 그 ID 로 지웁니다.

```powershell
winget list | Select-String "Python|Git"
```



### ④ 남은 흔적

§2 에서 만든 레지스트리 백업입니다. 되돌릴 일이 없으면 지웁니다.

```powershell
Remove-Item "$env:USERPROFILE\claude-policy-backup.reg" -Force
```

⚠️ **재부팅이 필요하고, WSL·Docker 도 이 기능을 씁니다.** 그리고 다시 Cowork 를 깔려면 켜고 **또 재부팅**해야 합니다 — 테스트 머신이라면 켜 둔 채로 두는 편이 낫습니다.

---



## 마무리 확인


| 명령                                                               | 기대 결과               |
| ---------------------------------------------------------------- | ------------------- |
| `Test-Path "HKLM:\SOFTWARE\Policies\Claude"`                     | `False`             |
| `Get-AppxPackage -AllUsers -Name "*laude*"`                      | 출력 없음               |
| `Test-Path "$env:LOCALAPPDATA\Claude-3p"`                        | `False`             |
| `Test-Path "C:\ProgramData\llm-gateway"`                         | `False`             |
| `Get-Command gateway-cli -EA SilentlyContinue`                   | 출력 없음               |
| `Test-Path "$env:LOCALAPPDATA\gateway-cli"`                      | `False`             |
| `Test-Path "$env:USERPROFILE\sample-agentic-ai-acceleration-kr"` | `False` — §7 까지 했으면 |
| `Get-Command py, git -EA SilentlyContinue`                       | 출력 없음 — §8 까지 했으면   |


**다시 설치하려면** — 어디까지 했느냐로 갈립니다. 1–2 → 설치 가이드 **절차 4** 부터 · 3–4 → **절차 3** 부터 · 5 → **절차 2** 부터 · 6–8 → **절차 0** 부터(8 까지 했으면 Python·Git 설치부터 다시).

---

---

