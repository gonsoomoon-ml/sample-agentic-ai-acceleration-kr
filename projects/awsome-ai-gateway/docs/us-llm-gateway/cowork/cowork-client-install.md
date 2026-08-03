# Cowork(Claude Desktop 3P) 클라이언트 설치

> **상태: 종단 검증 완료 — 2026-08-03, Windows Server 2025.**
> 절차 1~5 를 처음부터 끝까지 실제로 돌려 응답과 웹서치까지 확인했고, 그 과정에서 드러난 문서 문제를 반영했습니다. 검증 내용은 맨 아래 「검증 기록」에 있습니다.

Claude Code 가 아니라 **Cowork(Claude Desktop 3P)** 를 게이트웨이에 붙이는 문서입니다. 게이트웨이 쪽 변경(`update-scripts/`)이 끝난 뒤에 하는 작업입니다.

---

## 1. 한눈에

직원 PC 의 **Cowork 를 회사 게이트웨이에 연결**하는 작업입니다. 연결되면 Cowork 는 claude.ai 대신 회사 게이트웨이로 요청을 보내고, 그때 필요한 열쇠는 매번 자동으로 발급받습니다. 직원이 따로 로그인하거나 키를 관리할 일은 없습니다.

```
gateway-cli 설치·로그인 → helper 작성 → Cowork 설치 → 관리형 설정 → 실행 확인
       절차 1              절차 2        절차 3        절차 4       절차 5
```


| 절차    | 무엇을 하나                                       | 창            | 끝난 것을 아는 법                       |
| ----- | -------------------------------------------- | ------------ | -------------------------------- |
| **1** | Python·git 준비 → `gateway-cli` 설치 → 회사 계정 로그인 | 🔵           | `api-key-helper` 가 `vk-` 한 줄을 출력 |
| **2** | helper 파일 작성 — 열쇠 한 줄만 내보내는 스크립트             | 🔵 → 🔴 → 🔵 | helper 를 직접 실행해 `vk-` 한 줄        |
| **3** | Cowork 앱 설치 (`.msix`, 모든 사용자)                | 🔴           | `Get-AppxPackage` 가 이름·버전을 출력    |
| **4** | 관리형 설정 기록 — 게이트웨이 주소·helper 경로·모델 목록         | 🔴           | 다음 절차의 리포트에서 확인                  |
| **5** | 앱 실행 → 짧은 대화 → 운영자가 게이트웨이 기록 확인                | —            | 응답이 오고 `usage_logs` 에 `client=cowork` |


🔵 일반 PowerShell · 🔴 관리자 PowerShell — 아래 「절차」 앞의 표에 설명이 있습니다.

**운영자에게 미리 받을 것**


| 받을 것            | 어디서 나오나                                  |
| --------------- | ---------------------------------------- |
| env 값 4개        | 운영자가 `07-client-values.sh` 를 돌린 출력       |
| 로그인 계정          | 이메일 + 임시 비밀번호 (첫 로그인 때 새 비밀번호를 정하게 됩니다)  |
| 이 PC 의 공인 IP 등록 | 운영자가 `05-allow-client-ip.sh` 로 미리 넣어 둡니다 |


⚠️ **순서를 바꾸지 마십시오.** 특히 절차 4(설정)보다 앱을 먼저 켜면 claude.ai 로그인 화면이 뜨고, 거기서 개인 계정으로 들어가 버릴 여지가 생깁니다.

⏱️ 앱 내려받기(약 1.8 GB)를 빼면 30분 안팎입니다(실측). 대부분은 `pip install` 과 앱 설치가 차지합니다.

> **로그인 방식은 바뀔 수 있습니다.** 지금은 게이트웨이가 자체 계정 저장소(Amazon Cognito)를 쓰지만, 나중에 **회사가 이미 쓰는 로그인**(사내 계정 통합 로그인)으로 바꾸는 것이 예정돼 있습니다. 그때도 **직원이 하는 일은 이 문서 그대로**입니다 — 운영자가 주는 값 중 `OIDC_ISSUER_URL`·`OIDC_CLIENT_ID` 두 개만 달라지고, 브라우저에 뜨는 로그인 화면이 회사 화면으로 바뀝니다. 절차·명령은 동일합니다.

---



## 2. 전제

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



## 3. 먼저 정할 것 세 가지

"설치 모드"라고 불리는 축이 세 개고, 서로 독립입니다.


| 축           | 선택지                            | 이 배포의 선택        |
| ----------- | ------------------------------ | --------------- |
| ① 앱 설치 범위   | per-user / **per-machine**     | **per-machine** |
| ② 설정 레이어    | 앱 UI / configLibrary / **관리형** | **관리형**         |
| ③ 관리형 전달 방식 | 수동 / **MDM** / Bootstrap 서버    | 테스트=수동, 배포=MDM  |


**② 는 선택이 아닙니다.** credential helper(`inferenceCredentialHelper`)는 **관리형 레이어에서만 honor 됩니다** — 앱 UI 나 configLibrary 에 넣으면 조용히 무시됩니다(Seoul 실측). helper 방식을 쓰는 이상 관리형이 필수입니다.

**③ Bootstrap 은 필요 없습니다.** Bootstrap 의 명분인 "사용자별 자격 증명"은 helper 가 이미 해결합니다 

---



## 4. 절차

명령 블록 앞에는 **어느 창에서 돌리는지** 표시가 붙습니다. 창을 잘못 고르면 오류 없이 조용히 빗나가므로, 매번 확인하십시오.


| 표시                           | 어느 창인가                                           | 왜 나뉘나                              |
| ---------------------------- | ------------------------------------------------ | ---------------------------------- |
| ▶ 🔵 **실행 · 일반 PowerShell**  | 직원 본인 계정으로 그냥 연 PowerShell                       | 설치 파일과 로그인 토큰이 **직원 본인 폴더**에 들어갑니다 |
| ▶ 🔴 **실행 · 관리자 PowerShell** | **"관리자 권한으로 실행"** 으로 연 창 (제목 표시줄에 `관리자:` 가 보입니다) | 모든 사용자에게 앱을 깔고 시스템 영역에 설정을 씁니다     |
| ▶ 🟢 **실행 · 배포 EC2**         | 직원 PC 가 아니라 **운영자**가 게이트웨이 서버에서                  | 직원이 볼 수 없는 값을 뽑습니다                 |


⚠️ 🔵 자리에서 🔴 창을 쓰면 **관리자 계정의 폴더**에 설치되고 토큰도 그쪽에 저장됩니다. 명령은 성공한 것처럼 보이는데 Cowork 는 아무것도 못 찾습니다.

### 절차 1. `gateway-cli` 설치 + 로그인

Cowork 는 VK 를 직접 만들지 못합니다. `api-key-helper` 가 대신 받아오므로 이것이 먼저입니다.

> 이미 그 PC 에서 **Claude Code 를 쓰고 있다면 이 절을 건너뛰십시오.** 토큰(`%USERPROFILE%\.gateway-cli\`)을 그대로 공유합니다.



#### 시작 전 — 어떤 창에서 하나

**시작 메뉴에서** `PowerShell` **을 찾아 그냥 여십시오.** "관리자 권한으로 실행" 할 필요 없습니다.

이 절이 하는 일은 전부 **내 계정 폴더 안**에서 끝납니다 — 프로그램 설치도, 로그인 토큰 저장도.

> 관리자 권한이 필요한 곳은 두 군데뿐입니다 — 아래 ⓪ 에서 Python·git 을 "모든 사용자용"으로 설치할 때, 그리고 **절차 3·4**(Cowork 설치와 관리형 설정). 해당 단계에 따로 적어 두었습니다.



#### ⓪ 사전 요구사항 — 없을 때만

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
py --version     # 3.11+ 가 찍히면 건너뜀
git --version
```

둘 다 버전이 찍히면 ⓪ 은 건너뛰고 ① 로 가십시오.

`python` 이 아니라 `py` 로 확인하는 이유는, 이 문서의 모든 명령이 `py` 를 쓰기 때문입니다. `py` 는 여러 Python 버전 중 맞는 것을 골라주는 별도 프로그램이고, 설치할 때 빠뜨리면 `python` 만 있고 `py` 는 없는 상태가 됩니다.

⚠️ Python 은 **python.org / winget 판**을 쓰십시오. Microsoft Store 판은 경로 리다이렉션 때문에 `py` 런처와 `--user` 설치가 꼬입니다.

설치하는 방법이 두 가지인데, **둘 중 하나만** 하면 됩니다. 어느 쪽인지는 이 명령으로 갈립니다.

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
winget --version
```

버전이 나오면 **아래 A**, `인식할 수 없습니다` 가 나오면 **그 다음 B** 입니다.

---



##### A. `winget` 으로 설치 — 대부분 이쪽입니다

**두 개의 명령이니 한 줄씩** 실행하십시오.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
winget install --id Python.Python.3.12 -e --scope machine
winget install --id Git.Git -e
```

끝나면 **PowerShell 창을 닫고 새로 여십시오.** 새로 깐 프로그램의 위치는 그 뒤에 연 창부터 반영됩니다.

▶ 🔵 **실행 · 일반 PowerShell (새 창)** — 직원 PC

```powershell
py --version
git --version
```

```
Python 3.12.10
git version 2.55.0.windows.3
```

이렇게 둘 다 나오면 ⓪ 이 끝난 것입니다. 버전 숫자는 받은 시점에 따라 다릅니다. **B 는 하지 않습니다** — ① 로 가십시오.

⚠️ **창을 새로 열지 않으면** `py` 는 되는데 `git` 만 `인식할 수 없습니다` 로 나옵니다. git 이 안 깔린 것이 아닙니다 — Python 런처는 `C:\Windows` 에 들어가는데 그 폴더는 원래 PATH 에 있고, git 은 `C:\Program Files\Git\cmd` 라는 **새로 추가된 자리**에 들어가기 때문입니다. 실측에서 이 순서로 겪었습니다.

---

##### B. 설치 파일을 직접 받아 설치 — `winget` 이 없을 때만

**A 를 했다면 여기는 하지 않습니다.**

`winget` 은 App Installer 라는 구성요소가 있어야 동작합니다. 구형 Windows 10, 이미지에 따라 일부 Windows Server, 스토어 접근이 막힌 폐쇄망에서 없을 수 있습니다.

브라우저로 python.org · git-scm.com 에서 받아 실행해도 되고, 아래처럼 받아도 됩니다.

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
[Net.ServicePointManager]::SecurityProtocol = "Tls12"
$py="https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
$g="https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe"
Invoke-WebRequest -Uri $py -OutFile "$env:TEMP\py.exe"  -UseBasicParsing
Invoke-WebRequest -Uri $g  -OutFile "$env:TEMP\git.exe" -UseBasicParsing
```

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
Start-Process "$env:TEMP\py.exe" -Wait -ArgumentList `
  "/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1"
Start-Process "$env:TEMP\git.exe" -Wait -ArgumentList "/VERYSILENT /NORESTART"
```

`Include_launcher=1` 은 빼지 마십시오 — 이것이 `py` 를 설치합니다. 두 명령 모두 조용히 돌고 끝날 때까지 프롬프트가 안 돌아옵니다(각 1~3분).

끝나면 **PowerShell 창을 닫고 새로 여십시오.**

▶ 🔵 **실행 · 일반 PowerShell (새 창)** — 직원 PC

```powershell
py --version
git --version
```

둘 다 버전이 찍히면 ⓪ 이 끝난 것입니다.

⚠️ **창을 새로 열지 않으면** `py` 는 되는데 `git` 만 `인식할 수 없습니다` 로 나옵니다. git 이 안 깔린 것이 아닙니다 — git 이 들어가는 `C:\Program Files\Git\cmd` 가 새로 추가된 자리라 그렇습니다.

---

#### ① 저장소 — fork 에서

⚠️ **반드시** `gonsoomoon-ml` **fork 를 쓰십시오.** 원본인 `aws-samples` 저장소에는 벤더 버그 픽스가 빠져 있어, 설치는 성공한 것처럼 보이는데 인증이 조용히 개인 AWS 계정으로 새는 등의 문제가 있습니다.

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
cd ~
git clone -b us/deploy-fixes `
  https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
cd ~\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway
```

`-b us/deploy-fixes` 가 브랜치 지정입니다. 빼면 다른 브랜치를 받게 되니 그대로 쓰십시오.

저장소는 `gateway-cli` 를 설치하고 로그인 스크립트를 실행하는 데만 씁니다. 설치가 끝난 뒤에는 지워도 Cowork 동작에 지장이 없습니다.

#### ② `gateway-cli` 설치

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

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

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
gateway-cli version
```



#### ③ 운영자에게 받은 값 4개

직원은 이 값들을 직접 찾을 수 없습니다 — 두 개는 terraform 상태에, 두 개는 클러스터 안에 있습니다. **운영자가 배포 EC2 에서 아래를 돌려** 나온 4줄을 그대로 전달합니다.

▶ 🟢 **실행 · 배포 EC2** — 운영자

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 07-client-values.sh
```

읽기 전용이고, PowerShell 문법 4줄과 macOS 용 4줄을 함께 출력합니다. 직원은 받은 4줄을 그대로 붙여넣습니다.

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
$env:OIDC_ISSUER_URL="<from operator>"
$env:OIDC_CLIENT_ID="<from operator>"
$env:ADMIN_API_URL="<from operator>"
$env:ANTHROPIC_BASE_URL="<from operator - starts with https://>"
```

⚠️ `ANTHROPIC_BASE_URL` 은 `https://` **여야 합니다.** Cowork 가 평문 HTTP 주소를 거부합니다. `07-client-values.sh` 는 Cowork 기준이라 CloudFront 주소를 넣어 줍니다.

> 다음 단계의 로그인 스크립트가 이 주소의 `/health` 를 먼저 칩니다(`onboard-windows.ps1:42`). 그래서 이 값이 CloudFront 면, **직원 PC → CloudFront → ALB 경로가 거기서 한 번에 검증됩니다.**



#### ④ 로그인

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
cd ~\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\onboard-windows.ps1
```

첫 줄은 ① 에서 받은 폴더로 돌아가는 것입니다. 이미 거기에 계시면 아무 일도 일어나지 않습니다. `.\scripts\...` 가 "지금 폴더 기준"이라 다른 곳에 있으면 파일을 못 찾습니다.

⚠️ **③ 을 넣은 그 창에서 실행하십시오.** `$env:` 로 넣은 값 4개는 그 창에서만 살아 있습니다. 창을 새로 열었다면 폴더가 맞아도 스크립트가 값이 없다며 멈춥니다 — ③ 부터 다시 하시면 됩니다.

기본 브라우저에 로그인 화면이 뜹니다(현재는 Amazon Cognito 화면입니다 — 「한눈에」의 안내대로 나중에 회사 로그인 화면으로 바뀔 수 있습니다). 운영자가 발급한 이메일 + 비밀번호를 넣으십시오. **첫 로그인이면** 임시 비밀번호로 들어간 뒤 새 비밀번호를 설정하라고 요구합니다.

> 콜백은 `localhost:8090` 이고 **같은 PC 안**이라 터널이 필요 없습니다. 8090 이 다른 도구에 점유돼 실패하면 스크립트 대신 손으로 포트를 바꿉니다 — 로그인 쪽에 등록된 콜백이 `8090`·`8091`·`8092` **3개뿐**이라 그중에서 골라야 합니다. (등록 목록은 운영자가 확인해 줍니다. 로그인 방식이 바뀌면 이 목록도 바뀝니다.)
>
> ```powershell
> gateway-cli login --issuer-url $env:OIDC_ISSUER_URL `
>   --client-id $env:OIDC_CLIENT_ID --redirect-port 8091
> ```



#### ⑤ 확인

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
api-key-helper 2>$null | Select-String "^vk-"
```

`vk-` 로 시작하는 한 줄이 나오면 완료입니다.

⚠️ **로그인이 됐다고 안심하면 안 됩니다.** Cognito 는 공개라 ALB 와 무관하게 성공합니다. VK 발급은 admin-api 를 치고 그쪽은 IP 로 잠겨 있어, 그 PC 의 공인 IP 가 허용목록에 없으면 여기서 타임아웃납니다.

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
(iwr "$env:ADMIN_API_URL/health" -TimeoutSec 10).StatusCode   # 200 이어야 함
(irm https://checkip.amazonaws.com).Trim()                 # 막혔으면 이 IP 를 운영자에게
```



### 절차 2. credential helper 작성

Cowork 는 요청할 때마다 이 스크립트를 실행해서 열쇠(VK)를 받아옵니다. `gateway-cli` 의 `api-key-helper` 를 감싸 `vk-` **로 시작하는 한 줄만** 내보내는 것이 전부입니다.

만들 위치는 `C:\ProgramData\llm-gateway\helper.cmd` 입니다. 모든 사용자가 읽을 수 있고 관리자만 고칠 수 있는 자리라, 앱을 여러 사람이 쓰는 PC 에 맞습니다.

#### ① helper 가 부를 실행파일의 경로 확인

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
py -c "import sysconfig,os; print(os.path.join(sysconfig.get_path('scripts','nt_user'),'api-key-helper.exe'))"
```

출력된 경로를 **복사해 두십시오.** 다음 단계에서 붙여넣습니다.

⚠️ **반드시 직원 본인 계정의 일반 PowerShell 에서** 확인하십시오. 관리자 창에서 하면 관리자 계정의 폴더가 나오고, 그 경로에는 파일이 없습니다.

이름만(`api-key-helper`) 쓰지 않고 전체 경로를 쓰는 이유가 있습니다. helper 를 실행하는 것은 Cowork 인데, **Cowork 가 보는 PATH** 에 설치 위치가 들어 있어야 이름만으로 찾습니다. 절차 1 의 ② 에서 등록했지만 로그아웃 후 다시 로그인하기 전까지는 반영되지 않습니다. 전체 경로를 쓰면 이 문제가 아예 없습니다.

#### ② helper 파일 만들기

🔴 **여기는 관리자 권한이 필요합니다.** `C:\ProgramData` 아래에 파일을 쓰기 때문입니다. PowerShell 을 **"관리자 권한으로 실행"** 으로 여십시오.

먼저 값 네 개를 넣습니다 — 위에서 복사한 경로와, 운영자에게 받은 값 세 개입니다.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
$exe    = "<path printed by the command above>"
$issuer = "<from operator>"
$client = "<from operator>"
$admin  = "<from operator>"
```

이어서 폴더를 만들고 파일을 씁니다.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
$dir = "C:\ProgramData\llm-gateway"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
@"
@echo off
set OIDC_ISSUER_URL=$issuer
set OIDC_CLIENT_ID=$client
set ADMIN_API_URL=$admin
"$exe" 2>nul | findstr /b "vk-"
"@ | Set-Content -Path "$dir\helper.cmd" -Encoding ASCII
```

`-Encoding ASCII` 는 빼지 마십시오. 다른 인코딩으로 저장하면 파일 앞에 눈에 안 보이는 표식이 붙어 `cmd` 가 첫 줄을 못 읽습니다.

#### ③ 확인

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
& "C:\ProgramData\llm-gateway\helper.cmd"
```

`vk-` 로 시작하는 한 줄이 나오면 완료입니다.

⚠️ **관리자 창이 아니라 일반 창에서** 확인하십시오. 로그인 토큰은 직원 본인 폴더에 있어서, 관리자 창에서 돌리면 토큰을 못 찾습니다.

⚠️ **출력은 개행으로 끝나야 합니다.** 개행 없이 끝나면 앱이 값을 못 읽습니다(Seoul 실측). `findstr` 이 개행을 붙이므로 위 형태를 그대로 두면 됩니다.

⚠️ `api-key-helper` 출력 끝에 빈 줄이 붙는 경우가 있어 `findstr /b` 로 **첫 줄만** 뽑습니다.

### 절차 3. Cowork 설치

🔴 **여기부터는 관리자 권한이 필요합니다.** PowerShell 을 **"관리자 권한으로 실행"** 으로 여십시오(제목 표시줄에 `관리자:` 가 보입니다). 모든 사용자에게 앱을 깔고 시스템 설정을 쓰기 때문입니다. 절차 1 과 달리 여기서는 어느 관리자 계정으로 승격해도 상관없습니다 — 내 계정 폴더에 쓰는 것이 아니라 시스템 영역에 쓰기 때문입니다.

**설정보다 앱을 먼저 켜지 마십시오.** 관리형 설정 없이 처음 실행하면 claude.ai 로그인 화면이 뜨고, 거기서 개인 계정으로 들어가 버릴 여지가 생깁니다. 벤더 문서도 "설정 먼저, 앱 나중"을 권합니다.

#### ① 설치 파일 받기

**브라우저**로 이 주소를 여십시오. 약 1.8 GB 파일이 내려받아집니다.

```
https://claude.ai/api/desktop/win32/x64/offline/latest/redirect
```

⚠️ **반드시 이 주소의** `.msix` **를 받으십시오.** `claude.com/download` 는 `setup`(`.exe`) 을 주는데, **그것으로 깔면 Claude Desktop 은 설치되지만 Cowork 가 빠집니다.** 위 offline 판만 Cowork 가 들어 있고, 작업 환경 번들이 파일 안에 있어 설치 중 추가 다운로드도 없습니다.

받은 파일이 **어느 폴더에 있는지 전체 경로를 확인해 두십시오.** 다음 단계는 🔴 관리자 창에서 도는데, 그 창은 관리자 계정을 기준으로 폴더를 찾기 때문에 `내 다운로드` 같은 줄임 표현이 통하지 않습니다.

#### ② 설치

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
Add-AppxProvisionedPackage -Online -SkipLicense `
  -PackagePath "<full path to the .msix you downloaded>"
Get-AppxPackage -AllUsers -Name "*laude*" | Select Name,Version
```

📋 **예시** — 테스트 머신에서 실제로 쓴 명령입니다. 파일명 끝의 버전은 받은 시점에 따라 다르니, 앞 단계에서 확인한 경로로 바꿔 쓰십시오.

```powershell
Add-AppxProvisionedPackage -Online -SkipLicense `
  -PackagePath "C:\Users\Administrator\Downloads\Claude-offline-win32-x64-1.24012.9.msix"
```

`Get-AppxPackage` 가 이름과 버전을 한 줄 찍으면 설치된 것입니다.

`Add-AppxPackage` 가 아니라 `Add-AppxProvisionedPackage` 를 쓰는 이유는, 앞의 것은 **명령을 돌린 계정에게만** 설치되기 때문입니다. 뒤의 것은 이 PC 에 로그인하는 모든 사용자에게 등록됩니다.

#### ③ ② 가 거부됐을 때만

②에서 설치가 **거부**되면(권한 문제가 아니라 "이 앱은 설치할 수 없습니다" 계열) 이 PC 가 외부 앱 설치를 막아 둔 것입니다. 아래를 실행한 뒤 ②를 다시 하십시오.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Appx" `
  -Name AllowAllTrustedApps -PropertyType DWord -Value 1 -Force
```

**대부분의 직원 PC 에서는 필요 없습니다.** Windows 10·11 은 서명된 앱 설치를 기본 허용합니다. 걸리는 경우는 둘입니다 — **Windows Server** 는 기본값이 미설정이라 반드시 필요하고(우리 테스트 머신이 그랬습니다), 회사가 정책으로 막아 둔 PC 도 거부됩니다.

⚠️ **회사가 중앙에서 관리하는 PC 라면 IT 담당자에게 문의하십시오.** 이 값은 회사 전체 정책이 쓰이는 자리라, 직접 바꿔도 다음 정책 적용 때 되돌아갑니다.

### 절차 4. 관리형 설정

🔴 **관리자 PowerShell** 에서 진행합니다(절차 3 과 같은 창을 쓰면 됩니다).

설정을 **레지스트리**(Windows 가 프로그램 설정을 모아두는 시스템 저장소)의 `HKLM\SOFTWARE\Policies\Claude` 에 씁니다. 값은 전부 문자열(`REG_SZ`)입니다.

`HKLM` 은 컴퓨터 전체 설정, `HKCU` 는 사용자별 설정입니다. `HKLM` **에 값이 있으면** `HKCU` **는 통째로 무시됩니다** — 조직이 정한 설정을 사용자가 못 바꾸게 하는 구조입니다.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

먼저 값 두 개를 변수에 넣습니다. **손으로 채우는 것은 첫 줄 하나뿐입니다.**

```powershell
$BASE   = "<from operator - the https:// URL>"
$MODELS = '["claude-opus-5","claude-opus-4-8","claude-sonnet-5","claude-haiku-4-5-20251001"]'
$BASE
```

`$BASE` 는 절차 1-③ 의 `ANTHROPIC_BASE_URL` **과 같은 값**입니다. 이미 넣었는데 또 넣는 이유는, **절차 4 가 관리자 창**이고 절차 1 은 일반 창이기 때문입니다. `$env:` 로 넣은 값은 **창마다 따로**라 여기서는 비어 있습니다.

`$MODELS` 는 이 배포의 기본 목록이라 그대로 두시면 됩니다. 운영자가 다른 목록을 줬다면 그것으로 바꾸십시오 — 운영자는 `00-preflight-check.sh` 의 ACTIVE 목록에서 확인합니다.

⚠️ alias 는 **DB 에 등록된 문자열 그대로** 여야 합니다. 예를 들어 Haiku 는 `claude-haiku-4-5` 가 아니라 `claude-haiku-4-5-20251001` 입니다. 한 글자만 달라도 그 모델만 404 가 납니다.

마지막 줄이 `https://` 로 시작하는 주소를 찍는지 보고 넘어가십시오.

이제 아래는 **그대로** 붙여넣으면 됩니다.

```powershell
$K = "HKLM:\SOFTWARE\Policies\Claude"
New-Item -Path $K -Force | Out-Null
Set-ItemProperty $K inferenceProvider           "gateway"
Set-ItemProperty $K inferenceGatewayBaseUrl     $BASE
Set-ItemProperty $K inferenceGatewayAuthScheme  "bearer"
Set-ItemProperty $K inferenceCredentialHelper   "C:\ProgramData\llm-gateway\helper.cmd"
Set-ItemProperty $K inferenceCredentialHelperTtlSec 1800
Set-ItemProperty $K inferenceModels             $MODELS
```

확인합니다.

```powershell
Get-ItemProperty $K | Format-List inference*
```

여섯 개가 다 보이고 `inferenceGatewayBaseUrl` 에 **실제 주소**가 들어 있어야 합니다. `<from operator...>` 가 보이거나 값이 비어 있으면 `$BASE` 가 안 잡힌 것입니다 — 위로 돌아가 채우고 다시 돌리면 덮어써집니다.

> ✅ `inferenceModels` 는 **JSON 문자열**(`REG_SZ`)이 맞습니다 — 위 형태로 넣고 나서 앱이 실제로 그 목록의 alias 로 요청을 보낸 것을 게이트웨이 기록에서 확인했습니다(Windows 1.24012.9). `REG_MULTI_SZ` 는 필요 없습니다.



#### 설정 키


| 키                                 | 값                                                |
| --------------------------------- | ------------------------------------------------ |
| `inferenceProvider`               | `"gateway"`                                      |
| `inferenceGatewayBaseUrl`         | `https://<03 이 출력한 CloudFront 도메인>`              |
| `inferenceGatewayAuthScheme`      | `"bearer"`                                       |
| `inferenceCredentialHelper`       | helper **절대경로**                                  |
| `inferenceCredentialHelperTtlSec` | `1800`                                           |
| `inferenceModels`                 | `config.env` 의 `MODEL_ALIAS` + 기존 ACTIVE alias 들 |


⚠️ alias 는 **DB 에 등록된 문자열 그대로** 써야 합니다. 예를 들어 Haiku 는 `claude-haiku-4-5` 가 아니라 `claude-haiku-4-5-20251001` 입니다. 현재 목록은 `00-preflight-check.sh` 가 보여줍니다.

### 절차 5. 실행 및 검증

시작 메뉴에서 **Claude** 를 실행합니다.

#### ① 첫 화면

**claude.ai 로그인 화면이 뜨면 거기서 멈추십시오.** 개인 계정으로 들어가지 마시고 「5. 문제 판별」로 가십시오 — 관리형 설정을 못 읽었다는 뜻입니다.

**바로 쓸 수 있는 상태면 관리형 설정이 읽힌 것입니다.** 로그인을 요구하지 않는다는 것 자체가 증거입니다.

#### ② 모델 목록

모델 선택기를 열어 절차 4 의 `inferenceModels` 에 넣은 이름들이 보이는지 봅니다. 목록이 비어 있거나 다른 이름이면 그 값이 안 읽힌 것입니다.

#### ③ 짧은 대화

아무 모델이나 골라 `hi` 를 보냅니다. 응답이 오면 **직원 PC → CloudFront → 게이트웨이 → Bedrock** 이 전부 통한 것입니다.

⚠️ 게이트웨이 쪽 변경 직후라면 **캐시 5분**이 지나야 합니다. 그 전에는 신규 모델이 404 로 보입니다.

#### ④ 게이트웨이 쪽 확인 — 운영자

앱 화면만으로는 **어느 client 로 기록됐는지** 알 수 없습니다. `cowork` 로 분류되지 않으면 라우팅·예산이 Claude Code 와 섞입니다.

▶ 🟢 **실행 · 배포 EC2** — 운영자

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 04-verify.sh
```

C 섹션의 최근 행에서 봅니다.


| 열                 | 기대값                              |
| ----------------- | -------------------------------- |
| `client`          | `cowork`                         |
| `status`          | `SUCCESS`                        |
| `cost_usd`        | 0 이 아닐 것                         |
| `ws`              | 실시간 질문을 했다면 1 이상 (서버측 웹서치가 붙은 것) |


DB 조회라 임시 파드가 뜨는 데 1~2분 걸립니다. 화면이 멈춘 것처럼 보이는 게 정상입니다.

⚠️ **비용을 검산할 때 캐시 토큰을 빼먹지 마십시오.** `input_tokens`·`output_tokens` 만으로 계산하면 `cost_usd` 보다 훨씬 작게 나옵니다. Cowork 가 큰 시스템 프롬프트를 캐시로 보내기 때문이며, 단가 오류가 아닙니다.

> **벤더 문서의 `Help → Troubleshooting → Copy Managed Configuration Report` 는 이 빌드에 없습니다**(Windows 1.24012.9 실측). 좌측 하단 계정 메뉴에는 Settings·Language·View change log·Learn more·Sign out 만 있고, Settings 안에도 General·Privacy·Usage·Claude Code·Cowork 다섯 개뿐입니다. 그래서 위 ①~④ 로 갈음합니다 — ④ 는 리포트보다 강한 증거입니다. 리포트는 "앱이 설정을 어떻게 읽었나"를 보여주지만, ④ 는 **요청이 실제로 게이트웨이를 통과해 기록됐다**는 사실 자체입니다.

---



## 5. 문제 판별


| 증상                                 | 원인                                                                                     |
| ---------------------------------- | -------------------------------------------------------------------------------------- |
| 앱이 claude.ai 로그인 화면을 띄움            | 관리형 설정이 안 읽힘 (레지스트리 하이브/경로 확인)                                                         |
| 모델 목록이 비었거나 다른 이름              | `inferenceModels` 를 못 읽음 — JSON 형식·따옴표 확인 (절차 4)                                        |
| helper 는 VK 를 뱉는데 앱은 인증 실패         | helper 를 관리형이 아닌 레이어에 넣었거나, 출력이 개행으로 안 끝남                                              |
| 터미널에서는 helper 가 되는데 앱에서만 실패        | 앱이 보는 PATH 에 `api-key-helper` 가 없음 → 절대경로로                                             |
| VK 발급 타임아웃 (로그인은 성공)               | 클라이언트 공인 IP 가 `inbound-cidrs` 에 없음 → `05-allow-client-ip.sh`                           |
| `refresh failed: HTTP 400`         | refresh token 만료 → `gateway-cli login` 재실행                                             |
| `pip install` 이 `hatchling` 을 못 받음 | 사내망이 PyPI 를 막음 → 운영자에게 `.whl` 을 받아 `py -m pip install --user <받은경로>\gateway_cli-*.whl` |
| `py` 를 인식할 수 없다고 나옴                | Python 런처 미설치 → python.org 설치 파일을 다시 실행해 `py launcher` 를 체크                            |
| 특정 모델만 404                         | alias 오타, 또는 등록 후 5분 미경과                                                               |
| 전 요청 502                           | `01` 미적용 또는 CloudFront→ALB 경로 미개방(`03 --allow-cloudfront`)                             |


---



## 6. 조직 배포

테스트가 끝나면 관리형 설정을 MDM 으로 밀어넣습니다 — Windows 는 GPO 또는 Intune 으로 같은 레지스트리 값을 배포합니다. 설정 내용은 위와 동일하고, 전달 수단만 바뀝니다.


---



## 7. 선택 토글 — 앱 기능 켜고 끄기

여기까지 오면 게이트웨이 연결은 끝났습니다. 아래는 **연결과 무관한 앱 기능**들로, 조직이 켜고 끌 수 있습니다. 안 하셔도 됩니다.

값을 넣는 곳은 절차 4 와 같은 `HKLM\SOFTWARE\Policies\Claude` 이고, **바꾼 뒤에는 앱을 완전히 종료했다가 다시 켜야** 반영됩니다. 종료됐는지 확인하는 명령까지 아래 「Chat 탭 켜기」에 적어 뒀습니다 — 다른 토글도 절차는 같습니다.


용어 하나만 먼저 — **내장 도구**는 Claude 가 답을 만들면서 스스로 쓰는 기능들입니다. 웹에서 검색하기, 웹 페이지 열어보기 같은 것으로, 사용자가 따로 시키지 않아도 필요하면 씁니다.


| 키                      | 값을 넣으면 무엇이 바뀌나                       | 안 넣었을 때 (지금 상태)     |
| ---------------------- | ------------------------------------ | ------------------- |
| `chatTabEnabled`       | **Chat 탭**이 화면에 생깁니다                  | 탭이 안 보입니다           |
| `autoModeEnabled`      | **"Act without asking"** 가 승인 선택지에 생깁니다 | 선택지가 안 보입니다         |
| `disabledBuiltinTools` | 여기 적은 내장 도구를 앱에서 **뺍니다**              | 내장 도구를 전부 쓸 수 있습니다  |
| `builtinToolPolicy`    | 도구별로 **매번 물어보게** 만들 수 있습니다            | 대부분 안 묻고 바로 실행합니다   |
| `managedMcpServers`    | 조직이 정한 **MCP 서버**가 앱에 나타납니다           | 없습니다                |
| `disableBundledSkills` | 앱에 딸린 **Skills**(deep-research 등)를 끕니다 | 그대로 쓸 수 있습니다        |

⚠️ **기본값이 off 인 것은 앱에 옵션 자체가 안 뜹니다.** Chat 탭이 안 보이거나 "Act without asking" 이 선택지에 없는 것은 고장이 아니라 이 상태입니다.

### Chat 탭 켜기

여기가 **다른 토글에도 그대로 쓰는 절차**입니다 — 값 넣기 → 값 확인 → 앱 완전 종료 → 재시작 → 화면 확인.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
$K = "HKLM:\SOFTWARE\Policies\Claude"
Set-ItemProperty $K chatTabEnabled "true"
(Get-ItemProperty $K).chatTabEnabled
```

마지막 줄이 `true` 를 찍어야 합니다. 아무것도 안 찍히면 값이 안 들어간 것입니다.

이제 앱을 **완전히** 종료합니다. 창을 닫아도 백그라운드에 남아 있을 수 있는데, 그러면 설정을 다시 읽지 않습니다. 아래 한 줄이면 확실합니다 — 안 떠 있으면 아무 일도 일어나지 않습니다.

▶ 🔵 **실행 · 일반 PowerShell** — 직원 PC

```powershell
Get-Process -Name "*laude*" -ErrorAction SilentlyContinue | Stop-Process -Force
```

다시 실행하면 탭이 **Cowork · Code · Chat** 세 개가 됩니다.

안 늘어나면 ① 값이 `true` 로 찍히는지 다시 보고, ② 그래도 안 되면 값 형식 문제일 수 있으니 `"1"` 로 바꿔 같은 절차를 반복하십시오.

### "Act without asking" 켜기

Claude 가 위험하다고 판단한 작업만 승인을 묻고 나머지는 자율 실행합니다.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
$K = "HKLM:\SOFTWARE\Policies\Claude"
Set-ItemProperty $K autoModeEnabled "true"
```

⚠️ **모델이 Claude 4.6 이상**이어야 선택지가 활성화됩니다. 이 배포의 목록 중 `claude-opus-5`·`claude-opus-4-8`·`claude-sonnet-5` 는 충족하고, `claude-haiku-4-5-20251001` 은 미달이라 그 모델을 고르면 회색으로 보입니다.

### 내장 도구 빼기

앱에 딸린 웹 검색·웹 열기 같은 도구를 없앱니다. 회사가 외부 인터넷을 막아 둔 환경에서, 눌러도 실패하는 버튼을 아예 안 보이게 할 때 씁니다.

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
$K = "HKLM:\SOFTWARE\Policies\Claude"
Set-ItemProperty $K disabledBuiltinTools '["WebSearch","WebFetch"]'
```

⚠️ 이 둘은 **앱에 딸린 도구**이지 게이트웨이의 서버측 웹서치가 아닙니다. 게이트웨이 웹서치는 여기서 끄지 못하고, 운영자가 `routing_profiles.web_search_enabled` 로 끕니다.

### 값이 여러 개인 키의 형식

`disabledBuiltinTools`·`builtinToolPolicy`·`managedMcpServers` 는 값이 목록이나 표 형태입니다. 레지스트리에는 **JSON 을 통째로 하나의 문자열**로 넣습니다 — 절차 4 의 `inferenceModels` 와 같은 방식입니다.

```powershell
Set-ItemProperty $K builtinToolPolicy '{"Bash":"ask"}'
```

⚠️ 도구 이름은 **앱이 쓰는 이름 그대로** 여야 합니다. **틀린 이름은 오류 없이 무시됩니다.** 확인 방법은 하나뿐입니다 — 값을 넣고 앱을 재시작한 뒤 그 도구가 실제로 사라졌는지 보는 것입니다. 남아 있으면 철자가 틀린 것입니다.

### 확인과 되돌리기

▶ 🔴 **실행 · 관리자 PowerShell** — 직원 PC

```powershell
Get-ItemProperty "HKLM:\SOFTWARE\Policies\Claude"
```

되돌릴 때는 값을 `false` 로 바꾸지 말고 **키를 지우십시오.** 기본값으로 돌아갑니다.

```powershell
Remove-ItemProperty "HKLM:\SOFTWARE\Policies\Claude" chatTabEnabled
```

⚠️ 절차 4 에서 넣은 여섯 개(`inference*`)는 **지우면 안 됩니다.** 게이트웨이 연결이 끊어지고 앱이 claude.ai 로그인 화면으로 돌아갑니다.



## 8. 검증 기록

**2026-08-03 · Windows Server 2025 (EC2 `m5zn.metal`) · Cowork 1.24012.9.0**

절차 1~5 를 순서대로 실행해 앱 응답과 서버측 웹서치까지 확인했습니다. 게이트웨이 쪽 확인은 `04-verify.sh` 의 C 섹션(`usage.usage_logs`)으로 했습니다.


| 확인한 것        | 결과                                                                 |
| ------------- | ------------------------------------------------------------------ |
| `client` 분류   | 5행 모두 `cowork` — 앱이 보내는 `anthropic-client-platform: desktop_app` 을 게이트웨이가 읽습니다 |
| 모델            | `claude-opus-5` 와 `claude-haiku-4-5-20251001` 이 함께 기록 — 앱이 보조 작업에 작은 모델을 씁니다 |
| 서버측 웹서치       | 실시간 질문 행에 `web_search_count = 1`                                    |
| 비용 기록         | 전 행 `cost_usd > 0`, `status = SUCCESS`                              |
| CloudFront 경로 | `onboard-windows.ps1` 의 `gateway health: 200`                       |


⚠️ **비용 검산 시 캐시 토큰을 빼먹지 마십시오.** `input_tokens`·`output_tokens` 만으로 계산하면 실제 `cost_usd` 보다 훨씬 작게 나옵니다. Cowork 는 큰 시스템 프롬프트를 캐시로 보내서, `cache_creation_tokens`·`cache_read_tokens` 가 비용의 대부분을 차지하는 행이 나옵니다. 단가 오류로 오해하기 쉽습니다.

**이 검증에서 고친 것들** — 실제로 막혔던 지점입니다.

- ⓪ 표 한 칸에 붙어 있던 `winget` 명령 두 개가 한 줄로 읽혔습니다
- ⓪ 에 새 창 확인 단계가 없어, `py` 는 되는데 `git` 만 안 되는 상태로 넘어갔습니다
- 절차 3 에 설치 파일 받는 곳이 없었습니다
- 절차 4 의 자리표시자가 블록 한가운데 있어 그대로 레지스트리에 들어갔습니다
- 절차 2 에 helper 파일을 만드는 명령이 없고 내용만 있었습니다

