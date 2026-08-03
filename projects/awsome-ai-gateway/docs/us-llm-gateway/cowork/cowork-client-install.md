# Cowork(Claude Desktop 3P) 클라이언트 설치

> **상태: v0.1 초안 — 아직 종단 검증 전.**
> 테스트 머신 준비까지는 실측 완료(`windows-test-machine-setup.md`)이고, 그 이후 절차는 Seoul(macOS) 경험과 벤더 문서를 바탕으로 작성한 계획입니다. 실제로 돌려본 뒤 실측값으로 갱신합니다.

Claude Code 가 아니라 **Cowork(Claude Desktop 3P)** 를 게이트웨이에 붙이는 문서입니다. 게이트웨이 쪽 변경(`update-scripts/`)이 끝난 뒤에 하는 작업입니다.

---

## 전제

**게이트웨이 쪽** — `update-scripts/README.md` 의 실행 순서가 끝나 있어야 합니다.


| 필요한 것                                      | 어디서 나오나                                       |
| ------------------------------------------ | --------------------------------------------- |
| `https://` base URL                        | `03-create-cloudfront.sh --create` 출력         |
| Cowork 라우팅 정상화                             | `01-fix-cowork-routing.sh --apply`            |
| 쓸 모델 alias 목록                              | `00-preflight-check.sh` 의 ACTIVE 목록           |
| **클라이언트 공인 IP 가** `inbound-cidrs` **에 등록** | `05-allow-client-ip.sh --add <IP>/32 --apply` |


마지막 항목이 빠지면 **로그인은 되는데 VK 발급이 타임아웃**납니다. 로그인(Cognito)은 공개고 키 발급(admin-api)은 IP 제한이라 증상이 갈립니다.

**클라이언트 쪽** — 필요한 것은 `gateway-cli` 와 로그인 토큰뿐입니다(아래 절차 1). **Claude Code 는 필요 없습니다** — 같은 PC 에서 이미 쓰고 있다면 그 로그인을 그대로 재사용하므로 절차 1 을 건너뛰십시오.

⚠️ `gateway-cli` 는 **fork 에서** 설치하십시오. upstream 에는 벤더 버그 픽스 3건이 빠져 있어, 설치는 성공한 것처럼 보이는데 인증이 조용히 개인 계정으로 새는 등의 문제가 있습니다.

⚠️ **Windows 는 하드웨어 가상화가 필요합니다.** 일반 EC2 인스턴스에서는 Cowork 가 실행되지 않습니다 — 상세는 `windows-test-machine-setup.md`.

---



## 먼저 정할 것 세 가지

"설치 모드"라고 불리는 축이 세 개고, 서로 독립입니다.


| 축           | 선택지                            | 이 배포의 선택        |
| ----------- | ------------------------------ | --------------- |
| ① 앱 설치 범위   | per-user / **per-machine**     | **per-machine** |
| ② 설정 레이어    | 앱 UI / configLibrary / **관리형** | **관리형**         |
| ③ 관리형 전달 방식 | 수동 / **MDM** / Bootstrap 서버    | 테스트=수동, 배포=MDM  |


**② 는 선택이 아닙니다.** credential helper(`inferenceCredentialHelper`)는 **관리형 레이어에서만 honor 됩니다** — 앱 UI 나 configLibrary 에 넣으면 조용히 무시됩니다(Seoul 실측). helper 방식을 쓰는 이상 관리형이 필수입니다.

**③ Bootstrap 은 필요 없습니다.** Bootstrap 의 명분인 "사용자별 자격 증명"은 helper 가 이미 해결합니다 

---



## 절차



### 1. `gateway-cli` 설치 + 로그인

Cowork 는 VK 를 직접 만들지 못합니다. `api-key-helper` 가 대신 받아오므로 이것이 먼저입니다.

> 이미 그 PC 에서 **Claude Code 를 쓰고 있다면 이 절을 건너뛰십시오.** 토큰(`%USERPROFILE%\.gateway-cli\`)을 그대로 공유합니다.



#### 시작 전 — 어떤 창에서 하나

**시작 메뉴에서** `PowerShell` **을 찾아 그냥 여십시오.** "관리자 권한으로 실행" 할 필요 없습니다.

이 절이 하는 일은 전부 **내 계정 폴더 안**에서 끝납니다 — 프로그램 설치도, 로그인 토큰 저장도.

> 관리자 권한이 필요한 곳은 두 군데뿐입니다 — 아래 ⓪ 에서 Python·git 을 "모든 사용자용"으로 설치할 때, 그리고 **절차 3·4**(Cowork 설치와 관리형 설정). 해당 단계에 따로 적어 두었습니다.



#### ⓪ 사전 요구사항 — 없을 때만

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
py --version     # 3.11+ 가 찍히면 건너뜀
git --version
```

`python` 이 아니라 **`py`** 로 확인하는 이유는, 이 문서의 모든 명령이 `py` 를 쓰기 때문입니다. `py` 는 여러 Python 버전 중 맞는 것을 골라주는 별도 프로그램이고, 설치할 때 빠뜨리면 `python` 만 있고 `py` 는 없는 상태가 됩니다.

환경에 따라 둘 중 하나입니다.


| 환경                          | 방법                                                                                           |
| --------------------------- | -------------------------------------------------------------------------------------------- |
| `winget` 있음 (대부분)          | `winget install --id Python.Python.3.12 -e --scope machine` `winget install --id Git.Git -e` |
| `winget` 없음                | python.org · git-scm.com 에서 설치 파일을 받아 직접 설치 |


설치가 끝나면 **PowerShell 창을 닫고 다시 여십시오.** 새로 깐 프로그램의 위치가 창에 반영되려면 필요합니다.

⚠️ Python 은 **python.org / winget 판**을 쓰십시오. Microsoft Store 판은 경로 리다이렉션 때문에 `py` 런처와 `--user` 설치가 꼬입니다.

#### ① 저장소 — fork 에서

⚠️ **반드시** `gonsoomoon-ml` **fork 를 쓰십시오.** 원본인 `aws-samples` 저장소에는 벤더 버그 픽스가 빠져 있어, 설치는 성공한 것처럼 보이는데 인증이 조용히 개인 AWS 계정으로 새는 등의 문제가 있습니다.

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
cd ~
git clone -b us/deploy-fixes `
  https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
cd ~\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway
```

`-b us/deploy-fixes` 가 브랜치 지정입니다. 빼면 다른 브랜치를 받게 되니 그대로 쓰십시오.

저장소는 `gateway-cli` 를 설치하고 로그인 스크립트를 실행하는 데만 씁니다. 설치가 끝난 뒤에는 지워도 Cowork 동작에 지장이 없습니다.



#### ② `gateway-cli` 설치

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
py -m pip install --user .\gateway-cli
```

`pip --user` 로 깔면 실행파일이 Windows 가 기본으로 찾지 않는 폴더에 들어갑니다. 그 폴더를 **PATH**(명령어를 어디서 찾을지 적어둔 목록)에 등록해야 `gateway-cli` 를 이름만으로 부를 수 있습니다.

```powershell
$s = py -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))"
$env:PATH = "$s;$env:PATH"
[Environment]::SetEnvironmentVariable("PATH", "$s;" +
  [Environment]::GetEnvironmentVariable("PATH","User"), "User")
```

확인:

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
gateway-cli version
```



#### ③ 운영자에게 받은 값 4개

직원은 이 값들을 직접 찾을 수 없습니다 — 두 개는 terraform 상태에, 두 개는 클러스터 안에 있습니다. **운영자가 배포 EC2 에서 아래를 돌려** 나온 4줄을 그대로 전달합니다.

▶ **실행** · 배포 EC2 (운영자)

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 07-client-values.sh
```

읽기 전용이고, PowerShell 문법 4줄과 macOS 용 4줄을 함께 출력합니다. 직원은 받은 4줄을 그대로 붙여넣습니다.

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
$env:OIDC_ISSUER_URL="<운영자가 준 값>"
$env:OIDC_CLIENT_ID="<운영자가 준 값>"
$env:ADMIN_API_URL="<운영자가 준 값>"
$env:ANTHROPIC_BASE_URL="<운영자가 준 값 — https:// 로 시작>"
```

⚠️ `ANTHROPIC_BASE_URL` 은 `https://` **여야 합니다.** Cowork 가 평문 HTTP 주소를 거부합니다. `07-client-values.sh` 는 Cowork 기준이라 CloudFront 주소를 넣어 줍니다.

> 다음 단계의 로그인 스크립트가 이 주소의 `/health` 를 먼저 칩니다(`onboard-windows.ps1:42`). 그래서 이 값이 CloudFront 면, **직원 PC → CloudFront → ALB 경로가 거기서 한 번에 검증됩니다.**

#### ④ 로그인

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\onboard-windows.ps1
```

⚠️ `-SetupClaudeCode` **를 주지 마십시오.** 그 스위치는 Claude Code 의 managed-settings 를 고치는 것이고, Cowork 는 그 파일을 읽지 않습니다. 없이 돌리면 **로그인만** 합니다.

기본 브라우저에 Cognito 로그인이 뜹니다. 운영자가 발급한 이메일 + 비밀번호를 넣으십시오. **첫 로그인이면** 임시 비밀번호로 들어간 뒤 새 비밀번호를 설정하라고 요구합니다.

> 콜백은 `localhost:8090` 이고 **같은 PC 안**이라 터널이 필요 없습니다. 8090 이 다른 도구에 점유돼 실패하면 스크립트 대신 손으로 포트를 바꿉니다 — Cognito 콜백 화이트리스트가 `8090`·`8091`·`8092` **3개뿐**이라 그중에서 골라야 합니다.
>
> ```powershell
> gateway-cli login --issuer-url $env:OIDC_ISSUER_URL `
>   --client-id $env:OIDC_CLIENT_ID --redirect-port 8091
> ```



#### ⑤ 확인

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
api-key-helper 2>$null | Select-String "^vk-"
```

`vk-` 로 시작하는 한 줄이 나오면 완료입니다.

⚠️ **로그인이 됐다고 안심하면 안 됩니다.** Cognito 는 공개라 ALB 와 무관하게 성공합니다. VK 발급은 admin-api 를 치고 그쪽은 IP 로 잠겨 있어, 그 PC 의 공인 IP 가 허용목록에 없으면 여기서 타임아웃납니다.

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
(iwr "<ADMIN_API_URL>/health" -TimeoutSec 10).StatusCode   # 200 이어야 함
(irm https://checkip.amazonaws.com).Trim()                 # 막혔으면 이 IP 를 운영자에게
```

**macOS** 는 `uv` 설치 후 같은 저장소에서 `bash scripts/onboard-macos-linux.sh` 를 `--setup-claude-code` **없이** 돌리면 동일합니다.

### 2. credential helper 작성

`gateway-cli` 의 `api-key-helper` 를 감싸 **VK 한 줄만** 출력하는 스크립트입니다.

**Windows** — `C:\ProgramData\llm-gateway\helper.cmd`

```bat
@echo off
set OIDC_ISSUER_URL=<운영자가 준 값>
set OIDC_CLIENT_ID=<운영자가 준 값>
set ADMIN_API_URL=<운영자가 준 값>
api-key-helper 2>nul | findstr /b "vk-"
```

**macOS** — `~/bin/cowork-gw-credential-helper.sh`

```bash
#!/bin/bash
export OIDC_ISSUER_URL=<운영자가 준 값>
export OIDC_CLIENT_ID=<운영자가 준 값>
export ADMIN_API_URL=<운영자가 준 값>
api-key-helper 2>/dev/null | grep -m1 '^vk-'
```

`chmod +x` 후 직접 실행해 `vk-` **로 시작하는 한 줄**이 나오는지 확인합니다.

⚠️ **출력은 개행으로 끝나야 합니다.** 개행 없이 끝나면 앱이 값을 못 읽습니다(Seoul 실측). `grep`/`findstr` 은 개행을 붙이므로 그대로 두면 됩니다 — `echo -n` 등으로 직접 출력하지 마십시오.

⚠️ `api-key-helper` 출력 끝에 빈 줄이 붙는 경우가 있어 `grep -m1` / `findstr /b` 로 **첫 줄만** 뽑습니다.

⚠️ `api-key-helper` **를 PATH 에서 못 찾는 경우가 있습니다.** helper 를 실행하는 것은 Cowork 이므로, **Cowork 가 보는 PATH** 에 설치 위치가 들어 있어야 합니다. 절차 1 의 ② 에서 등록했지만 **로그아웃 후 다시 로그인하기 전까지는 반영되지 않습니다.** 확실히 하려면 helper 에 절대경로를 쓰십시오.

설치 위치는 이렇게 확인합니다.

▶ **실행** · 직원 PC (Windows) — 일반 PowerShell

```powershell
py -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))"
```

출력된 경로를 helper 에 그대로 넣습니다.

```bat
"C:\Users\<사용자>\AppData\Roaming\Python\Python312\Scripts\api-key-helper.exe" 2>nul | findstr /b "vk-"
```

### 3. Cowork 설치

🔴 **여기부터는 관리자 권한이 필요합니다.** PowerShell 을 **"관리자 권한으로 실행"** 으로 여십시오(제목 표시줄에 `관리자:` 가 보입니다). 모든 사용자에게 앱을 깔고 시스템 설정을 쓰기 때문입니다. 절차 1 과 달리 여기서는 어느 관리자 계정으로 승격해도 상관없습니다 — 내 계정 폴더에 쓰는 것이 아니라 시스템 영역에 쓰기 때문입니다.

**설정보다 앱을 먼저 켜지 마십시오.** 관리형 설정 없이 처음 실행하면 claude.ai 로그인 화면이 뜨고, 거기서 개인 계정으로 들어가 버릴 여지가 생깁니다. 벤더 문서도 "설정 먼저, 앱 나중"을 권합니다.

**Windows (per-machine)**

▶ **실행** · 직원 PC (Windows) — 관리자 PowerShell

```powershell
Add-AppxProvisionedPackage -Online -SkipLicense `
  -PackagePath "<Claude .msix 경로>"
Get-AppxPackage -AllUsers -Name "*laude*" | Select Name,Version
```

사이드로딩 정책이 막혀 있으면 먼저 열어야 합니다:

▶ **실행** · 직원 PC (Windows) — 관리자 PowerShell

```powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Appx" `
  -Name AllowAllTrustedApps -PropertyType DWord -Value 1 -Force
```

**macOS** — `/Applications` 에 설치(조직) 또는 `~/Applications`(개인).

### 4. 관리형 설정

🔴 **관리자 PowerShell** 에서 진행합니다(절차 3 과 같은 창을 쓰면 됩니다).

**Windows** — 설정을 **레지스트리**(Windows 가 프로그램 설정을 모아두는 시스템 저장소)의 `HKLM\SOFTWARE\Policies\Claude` 에 씁니다. 값은 전부 문자열(`REG_SZ`)입니다.

`HKLM` 은 컴퓨터 전체 설정, `HKCU` 는 사용자별 설정입니다. `HKLM` **에 값이 있으면** `HKCU` **는 통째로 무시됩니다** — 조직이 정한 설정을 사용자가 못 바꾸게 하는 구조입니다.

▶ **실행** · 직원 PC (Windows) — 관리자 PowerShell

```powershell
$K = "HKLM:\SOFTWARE\Policies\Claude"
New-Item -Path $K -Force | Out-Null
Set-ItemProperty $K inferenceProvider           "gateway"
Set-ItemProperty $K inferenceGatewayBaseUrl     "https://<CloudFront 도메인>"
Set-ItemProperty $K inferenceGatewayAuthScheme  "bearer"
Set-ItemProperty $K inferenceCredentialHelper   "C:\ProgramData\llm-gateway\helper.cmd"
Set-ItemProperty $K inferenceCredentialHelperTtlSec 1800
Set-ItemProperty $K inferenceModels             '["claude-opus-5","claude-opus-4-8","claude-sonnet-5","claude-haiku-4-5-20251001"]'
```

> ❓ `inferenceModels` 를 JSON 문자열로 넣을지 `REG_MULTI_SZ` 로 넣을지는 **첫 실행 때 Managed Configuration Report 로 확인**해야 합니다. 리포트에 배열로 파싱돼 보이면 맞습니다.

**macOS** — `.mobileconfig` 프로파일. 생성 스크립트는 `cowork-llm-gateway/client/macos/install-cowork-llm-gateway.py` 를 참고하십시오(Seoul 배포에서 쓰던 것).

### 설정 키


| 키                                 | 값                                                |
| --------------------------------- | ------------------------------------------------ |
| `inferenceProvider`               | `"gateway"`                                      |
| `inferenceGatewayBaseUrl`         | `https://<03 이 출력한 CloudFront 도메인>`              |
| `inferenceGatewayAuthScheme`      | `"bearer"`                                       |
| `inferenceCredentialHelper`       | helper **절대경로**                                  |
| `inferenceCredentialHelperTtlSec` | `1800`                                           |
| `inferenceModels`                 | `config.env` 의 `MODEL_ALIAS` + 기존 ACTIVE alias 들 |


⚠️ alias 는 **DB 에 등록된 문자열 그대로** 써야 합니다. 예를 들어 Haiku 는 `claude-haiku-4-5` 가 아니라 `claude-haiku-4-5-20251001` 입니다. 현재 목록은 `00-preflight-check.sh` 가 보여줍니다.

### 5. 실행 및 검증

앱 실행 → **Help → Troubleshooting → Copy Managed Configuration Report**.

리포트에서 확인할 것:

- 위 키들이 **관리형(managed) 출처**로 잡혀 있는가 — 로컬 값으로 잡히면 레지스트리/프로파일이 안 읽힌 것
- `inferenceModels` 가 배열로 파싱됐는가
- 설정 창이 **읽기 전용**인가 (관리형이 걸리면 잠깁니다)

그다음 모델을 골라 짧은 메시지를 보냅니다.

⚠️ 게이트웨이 쪽 변경 직후라면 **캐시 5분**이 지나야 합니다. 그 전에는 신규 모델이 404 로 보입니다.

---



## 문제 판별


| 증상                          | 원인                                                           |
| --------------------------- | ------------------------------------------------------------ |
| 앱이 claude.ai 로그인 화면을 띄움     | 관리형 설정이 안 읽힘 (레지스트리 하이브/경로 확인)                               |
| 설정 창이 편집 가능                 | 위와 동일                                                        |
| helper 는 VK 를 뱉는데 앱은 인증 실패  | helper 를 관리형이 아닌 레이어에 넣었거나, 출력이 개행으로 안 끝남                    |
| 터미널에서는 helper 가 되는데 앱에서만 실패 | 앱이 보는 PATH 에 `api-key-helper` 가 없음 → 절대경로로                   |
| VK 발급 타임아웃 (로그인은 성공)        | 클라이언트 공인 IP 가 `inbound-cidrs` 에 없음 → `05-allow-client-ip.sh` |
| `refresh failed: HTTP 400`  | refresh token 만료 → `gateway-cli login` 재실행                   |
| `pip install` 이 `hatchling` 을 못 받음 | 사내망이 PyPI 를 막음 → 운영자에게 `.whl` 을 받아 `py -m pip install --user <받은경로>\gateway_cli-*.whl` |
| `py` 를 인식할 수 없다고 나옴       | Python 런처 미설치 → python.org 설치 파일을 다시 실행해 `py launcher` 를 체크 |
| 특정 모델만 404                  | alias 오타, 또는 등록 후 5분 미경과                                     |
| 전 요청 502                    | `01` 미적용 또는 CloudFront→ALB 경로 미개방(`03 --allow-cloudfront`)   |


---



## 조직 배포

테스트가 끝나면 관리형 설정을 MDM 으로 밀어넣습니다 — Windows 는 GPO/Intune, macOS 는 Jamf 등으로 `.mobileconfig`. 설정 내용은 위와 동일하고, 전달 수단만 바뀝니다.

Bootstrap 서버 방식은 사용자마다 설정 자체가 달라야 할 때만 검토하십시오. 비교표는 `cowork-anthropic-install-reference.md` 에 있습니다. 주의할 점은 Bootstrap 응답이 **MDM 값을 병합하지 않고 통째로 대체**한다는 것과, 서버 장애 시 앱이 로그인 대기 상태가 된다는 것입니다.