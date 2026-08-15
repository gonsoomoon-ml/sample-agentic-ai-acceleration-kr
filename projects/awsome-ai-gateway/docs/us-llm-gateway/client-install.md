# US LLM Gateway — 직원 PC 클라이언트 설치 (§6-1 ~ §6-3)

> **누가 보나**: **직원**(또는 직원 PC 를 세팅해 주는 IT 담당자). 운영자는 게이트웨이 설치를 마친 뒤 **이 문서만** 직원에게 전달하면 된다 — 1,000줄이 넘는 인프라 런북은 필요 없다.
>
> **시작 전 운영자에게서 받을 것**
> - **env 4줄** — `OIDC_ISSUER_URL` · `OIDC_CLIENT_ID` · `ADMIN_API_URL` · `ANTHROPIC_BASE_URL`
> - **Cognito 계정** — 이메일 + 임시 비밀번호 (발급은 [operations.md §8-Y](ops/8-Y-onboarding.md))
> - **그 PC 의 공인 IP 가 `inbound-cidrs` 에 등록**되어 있을 것 — 빠져 있으면 **로그인은 되는데** 키발급·추론이 타임아웃난다(로그인 = Cognito 공개, 키발급·추론 = IP 제한)
>
> **절 번호 규칙**: `§1`~`§6-0` 은 [install-guide.md](install-guide.md) 의 절 번호다. 이 문서는 그 흐름의 **§6-1 ~ §6-3** 을 독자(직원)가 달라 떼어낸 것이라 번호를 그대로 유지한다.

---

### 6-1. Claude Code 설치 (직원 PC — macOS · Windows)

> 💡 관리자는 [install-guide.md §6-0](install-guide.md#6-0-linux-배포-ec2--관리자가-먼저-익힌다) 을 먼저 해보길 권한다 — 배포 EC2 에는 Claude Code 가 이미 깔려 있어 이 설치 절을 건너뛰고 `login`·`setup` 만 익힐 수 있다.

§6-2·§6-3(직원 PC 로그인·setup)은 **Claude Code 가 이미 깔려 있다고 가정**한다. 직원 PC(macOS·Windows·Linux)엔 §2-2 부트스트랩이 없으니 **여기서 바이너리부터 깐다.**


| OS                   | 설치 명령 (native installer)                                                 |
| -------------------- | ------------------------------------------------------------------------ |
| macOS / Linux / WSL  | `curl -fsSL https://claude.ai/install.sh \| bash`                          |
| Windows (PowerShell) | `irm https://claude.ai/install.ps1 \| iex`                                 |


**native installer** 를 쓴다 — Node.js 불필요, **관리자 권한 불필요**(사용자 폴더에만 씀), 백그라운드 자동 업데이트. `npm install -g @anthropic-ai/claude-code` 는 Node 22+ 가 필요하고 **자동 업데이트가 안 되므로** 직원 PC 엔 권하지 않는다(공식 문서도 native 를 1순위로 안내).

> 🔴 **PATH 자동 등록을 믿지 말 것.** Windows 실측(2026-07-17, v2.1.212): 설치는 성공했는데 출력에 `Native installation exists but C:\Users\<user>\.local\bin is not in your PATH` 가 뜨고 GUI 로 등록하라고 안내했다 — 그대로 두면 다음 절이 전부 `claude: 명령을 찾을 수 없음` 으로 막힌다. 등록 후 **터미널을 새로 열어야** 반영된다.
> ▶ **실행** · 직원 PC (Windows)
>
> ```powershell
> $p="$env:USERPROFILE\.local\bin"
> $u=[Environment]::GetEnvironmentVariable("PATH","User")
> [Environment]::SetEnvironmentVariable("PATH","$p;$u","User")
> ```
>
> 확인: 새 창에서 `claude --version`.

> ⚠️ 설치 직후 `apiKeyHelper failed` 트레이스백이 보일 수 있다 — 설치 프로그램이 managed-settings 의 헬퍼를 한 번 시험 삼아 부르기 때문이다. `urllib3ㆍcreate_connection` 에서 났다면 **인증이 아니라 네트워크** 문제다(그 PC IP 가 `inbound-cidrs` 밖). 아래 각 절의 확인 절차로 잡는다.



### 6-2. macOS — 실측 검증 (2026-07-17)

**§6-1 설치를 마친 Mac** 에서 아래 ①~③ 을 실행한다. 흐름은 §6-0(배포 EC2)과 같지만 **저장소를 직접 clone** 하는 점이 다르다(EC2 는 부트스트랩이 이미 받아둠). §6-0 의 함정 중 **8090 포트·**`--setup-claude-code` **누락·OIDC 주입(fork)** 은 macOS 에도 그대로 적용되고, EC2 전용인 `CLAUDE_CODE_USE_BEDROCK`·headless 는 직원 Mac 엔 보통 없다. 그 위에 **macOS 에서만 다른 점**을 실행 블록 아래에 덧붙인다.

▶ **실행** · 직원 PC (macOS)

```bash
# ① uv 설치 — uv 가 자기 Python 3.11+ 를 받아 쓴다 (시스템·conda Python 안 건드림)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# ② 운영자가 준 4줄 붙여넣기 (§6 출력 그대로 — 아래는 예시값)
export OIDC_ISSUER_URL="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEfGhI"
export OIDC_CLIENT_ID="7h2k9p4m1n8q3r5s6t0v2w4x6y"
export ADMIN_API_URL="http://k8s-llmgatew-llmgatew-a1b2c3d4e5-1234567.us-west-2.elb.amazonaws.com"
export ANTHROPIC_BASE_URL="http://k8s-llmgatew-llmgatew-f6g7h8i9j0-7654321.us-west-2.elb.amazonaws.com"

# ③ 저장소 clone (직원 Mac 엔 없다 — §1-4 = fork) 후 온보딩
cd ~
git clone -b us/deploy-fixes \
  https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
cd sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
bash scripts/onboard-macos-linux.sh --setup-claude-code
```

> ℹ️ **③ 도중 브라우저에 Cognito 로그인 창이 뜬다** — `gateway-cli login` 단계에서 기본 브라우저가 자동으로 열린다. **이메일 + 비밀번호**(운영자가 발급한 그 직원의 Cognito 계정 — 발급은 [operations.md §8-Y](ops/8-Y-onboarding.md))를 입력한다. **첫 로그인이면** 임시 비밀번호로 들어간 뒤 곧바로 **새 비밀번호 설정**을 요구한다(관리자 생성 계정 기본 상태). 로그인에 성공하면 브라우저에 완료 표시가 뜨고 터미널이 이어서 진행된다.

**검증**: `claude` → `/status` 에서 base URL = gateway ALB · `Auth token` = `apiKeyHelper`. 그다음 `hi`(추론 §4-5) → 실시간 값 질문(웹서치 §5-4).

아래는 **macOS 에서만 다른 점**이다.

> 🔴 **gateway-cli 를 upstream 에서 설치하지 말 것 — macOS 에서 조용히 게이트웨이를 우회한다.**
> Claude Code 는 OS 마다 다른 곳에서 managed settings 를 읽는데(macOS = `/Library/Application Support/ClaudeCode/managed-settings.d/`), upstream 의 gateway-cli 는 `win32` 만 분기하고 macOS 를 **Linux 경로(**`/etc/claude-code/`**)로 보낸다**(`managed.py:_managed_dir`).
> **실패가 조용하다** — `setup` 은 `Gateway enabled: /etc/claude-code/...` 로 **성공을 찍고** 파일도 정상 생성되는데, Claude Code 는 **그 파일을 아예 안 읽는다**. 사용자는 **이전 인증(개인 Max 구독 등)으로 계속** 쓰고, 예산·rate limit·비용기록이 전부 우회되는데 **에러가 어디에도 없다** → 관리자는 "붙였다"고 믿는다.
> **fork 픽스** `5e05ffd` 가 darwin·WSL 분기를 넣었다. ③의 온보딩 스크립트는 **클론한 저장소의** `./gateway-cli` 에서 설치하므로(§1-4 = fork) 그대로 따르면 픽스본이 깔린다. ⚠️ `pip install "git+https://github.com/aws-samples/..."` **처럼 upstream 을 직접 가리키는 설치는 macOS 에서 버그본을 깐다** — 3 OS 모두 **클론한 저장소에서** 설치할 것.
>
> **진단**: `claude` → `/status` → `Setting sources` 에 `Enterprise managed settings` 가 없으면 이 문제다(`Login method: Claude Max account` 가 그대로 남아 있는 것도 같은 신호). 이미 잘못 깔았다면 파일만 옮겨도 즉시 살아난다:
> ▶ **실행** · 직원 PC (macOS) — 잘못 깔렸을 때 응급조치
>
> ```bash
> sudo mkdir -p "/Library/Application Support/ClaudeCode/managed-settings.d"
> sudo cp /etc/claude-code/managed-settings.d/50-gateway.json \
>   "/Library/Application Support/ClaudeCode/managed-settings.d/"
> ```
>
> ⚠️ 이 경우 `gateway-cli disable` 은 `/etc/` **쪽만 지우므로** 옮긴 파일은 손으로 지워야 한다.

> ℹ️ `setup` 은 macOS 도 **sudo 가 필요**하다(시스템 경로에 씀). `apiKeyHelper` 는 **절대경로가 아니라 이름**(`"api-key-helper"`)으로 기록되므로 Claude Code 가 **PATH 에서 찾아야** 한다 — uv 가 `~/.local/bin` 에 깔고 터미널에서 띄우면 문제없다(실측). GUI 런처로 띄우면 PATH 가 최소라 못 찾을 수 있고, 그때는 `setup --api-key-helper <절대경로>` 로 다시 실행한다.

> ℹ️ **셸에** `ANTHROPIC_BASE_URL` **만 export 된 상태를 조심할 것.** ②의 export 는 그 자체로 Claude Code 의 주소를 바꾼다 — managed settings 없이도. 그래서 **주소는 게이트웨이, 인증은 개인 계정**인 반쪽 상태가 만들어지고 **401** 이 난다(실측). 정상 상태의 `/status` 는 `Auth token: apiKeyHelper` 다.



### 6-3. Windows (PowerShell) — 실측 검증 (2026-07-17)

**§6-1 설치를 마친 Windows** 에서 아래 ⓪~④ 를 **관리자 PowerShell** 에서 실행한다. macOS·Linux 와 달리 Windows 는 gateway-cli 를 **pip 로** 깔고 **PATH 를 직접 등록**해야 해 단계가 많다. 🔴 **시작 전 창이 관리자 권한인지 확인**한다(제목 표시줄 `관리자:`) — 아니면 마지막 `setup` 에서 `WinError 5` 로 죽는다(상세·복구는 아래 🔴).

▶ **실행** · 직원 PC (Windows) — 관리자 PowerShell

```powershell
# ⓪ 사전 요구사항 — 없을 때만 설치 (winget 은 Windows 11 / 최신 Win10 에 기본 포함)
python --version     # 3.11+ 가 찍히면 건너뜀
git --version        # ①-a 의 저장소 clone 에 필요

winget install --id Python.Python.3.12 -e --scope machine   # py 런처 + PATH 자동 등록
winget install --id Git.Git -e
#    → 설치 후 PATH 반영을 위해 PowerShell 창을 닫고 관리자 권한으로 다시 연다

# ①-a 저장소 — ④의 스크립트에도 필요하고, gateway-cli 도 여기서 깐다(=fork, 픽스 포함)
cd ~
git clone -b us/deploy-fixes `
  https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
cd ~\sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway

# ①-b gateway-cli 설치 — Python 3.11+ 필수
#     (Windows 스크립트는 설치를 안 하고 '확인만' 한다 — 없으면 exit 1)
py -m pip install --user .\gateway-cli
#     또는 운영자 배포 .whl:  py -m pip install --user $HOME\Downloads\gateway_cli-*.whl

# ② PATH 등록 — pip --user 의 Scripts 폴더는 기본 PATH 에 없어 gateway-cli 를 못 찾는다
$s = py -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))"
$env:PATH = "$s;$env:PATH"                                       # 이번 세션
[Environment]::SetEnvironmentVariable("PATH", "$s;" +
  [Environment]::GetEnvironmentVariable("PATH","User"), "User")  # 영구(새 셸부터)
gateway-cli version              # 버전이 찍히면 설치 OK

# ③ 운영자가 준 4줄 (§6 출력값을 PowerShell 문법으로 — 아래는 예시값)
$env:OIDC_ISSUER_URL="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEfGhI"
$env:OIDC_CLIENT_ID="7h2k9p4m1n8q3r5s6t0v2w4x6y"
$env:ADMIN_API_URL="http://k8s-llmgatew-llmgatew-a1b2c3d4e5-1234567.us-west-2.elb.amazonaws.com"
$env:ANTHROPIC_BASE_URL="http://k8s-llmgatew-llmgatew-f6g7h8i9j0-7654321.us-west-2.elb.amazonaws.com"

# ④ 온보딩 (①-a 에서 이미 저장소 폴더에 있다)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force   # .ps1 실행 차단 해제(이 세션만)
.\scripts\onboard-windows.ps1 -SetupClaudeCode
```

> ℹ️ **④ 도중 브라우저에 Cognito 로그인 창이 뜬다** — `gateway-cli login` 단계에서 기본 브라우저가 자동으로 열린다. **이메일 + 비밀번호**(운영자가 발급한 그 직원의 Cognito 계정 — 발급은 [operations.md §8-Y](ops/8-Y-onboarding.md))를 입력한다. **첫 로그인이면** 임시 비밀번호로 들어간 뒤 곧바로 **새 비밀번호 설정**을 요구한다(관리자 생성 계정 기본 상태). 로그인에 성공하면 브라우저에 완료 표시가 뜨고 터미널이 이어서 진행된다.

**검증**: 새 셸에서 `claude` → `/status` 에서 `Anthropic base URL` = gateway ALB · `Auth token` = `apiKeyHelper` 여야 한다(§6-0 검증과 동일). 그다음 `hi`(추론 §4-5) → 실시간 값 질문(웹서치 §5-4).

**아래 🔴/ℹ️ 는 배경·함정이다** — 검증이 통과했으면 넘어가도 된다. 막히면 해당 항목에서 원인을 찾는다.

> 🔴 **④를 시작하기 전에 창이 관리자 권한인지 반드시 확인한다** — 제목 표시줄에 `관리자:`. 스크립트가 **권한을 미리 확인하지 않아서**, 안 되어 있으면 **브라우저 로그인까지 다 시킨 뒤 마지막** `setup` **에서** `Failed to write managed settings: WinError 5: access is denied` 로 죽는다(실측). Linux 는 `sudo` 로 자동 승격하지만(`managed.py:_write_unix`) **Windows 는 그냥 쓰고 실패한다**(`_write_windows`: *"requires running as admin on Windows"*).
> 이미 로그인까지 했다면 **다시 안 해도 된다** — 토큰이 `%USERPROFILE%\.gateway-cli\` 에 있으므로, 관리자 창을 새로 열고 URL 2개만 다시 넣은 뒤 `gateway-cli setup ...` 만 돌리면 된다.

> ℹ️ **그 PC 의 공인 IP 가** `inbound-cidrs` **안에 있어야 한다**(§3-6). 사내망이 아닌 회선(집·지사)이면 십중팔구 빠져 있다 — 실측에서도 그랬다. 확인:
> ▶ **실행** · 직원 PC (Windows)
>
> ```powershell
> $G="http://<admin-api ALB>"
> (iwr "$G/health" -TimeoutSec 10).StatusCode      # 200 이어야 함
> (irm https://checkip.amazonaws.com).Trim()       # 막혔으면 이 IP 를 열어달라고 운영자에게
> ```
>
> 로그인이 됐다고 안심하면 안 된다 — **Cognito 는 공개**라 ALB 와 무관하게 성공한다. `apiKeyHelper` 가 `urllib3ㆍcreate_connection` 에서 죽으면 이 문제다.

> **PowerShell 은 따로 설치할 필요 없다** — Windows 10/11 에 **Windows PowerShell 5.1** 이 기본 내장이고 이 스크립트는 5.1 에서 동작한다(시작 메뉴에 "PowerShell"). PowerShell 7 은 선택 사항이다(`winget install --id Microsoft.PowerShell -e`). **Python 3.11+ 와 git 은 기본 내장이 아니라서 ⓪ 에서 설치**한다.
> **winget 이 없으면**(구형 Win10) Microsoft Store 에서 **앱 설치 관리자(App Installer)** 를 먼저 깔거나, python.org · git-scm.com 에서 직접 설치한다. Python 은 **python.org/winget 판을 쓸 것** — Microsoft Store 판은 경로 리다이렉션 때문에 `py` 런처와 `--user` 설치가 꼬일 수 있다.
>
> **PowerShell 을 "관리자 권한으로 실행"** 하되 **직원 본인 계정으로 UAC 승격**한다. `gateway-cli setup` 은 `C:\Program Files\...` 에 써야 해서 승격이 필요하고, `gateway-cli login` 은 토큰을 `%USERPROFILE%\.gateway-cli\` 에 쓰므로 **다른 계정으로 실행하면 안 된다**(직원 프로필에 토큰이 안 생겨 Claude Code 가 인증하지 못한다). UAC 승격은 계정이 그대로라 둘 다 만족한다.

> ⚠️ macOS 와 마찬가지로 `-SetupClaudeCode` **를 빼면 로그인만** 한다(스크립트 49줄 분기 `if ($SetupClaudeCode)`). 원복 = `gateway-cli disable`.

> ℹ️ **③의** `$env:` **는 이 창에서만 살지만 따로 영구 등록할 필요는 없다** — `setup` 이 `OIDC_ISSUER_URL`·`OIDC_CLIENT_ID` 를 **managed-settings 에 함께 심는다**(fork 픽스 `7773582`). 그래서 ①-b 를 **클론한 저장소(fork)에서** 설치하는 것이 중요하다 — upstream 의 gateway-cli 로 깔면 그 둘이 안 심겨서, 창을 닫는 순간(=아래 "새 셸에서 `claude` 실행") 헬퍼가 **STS 로 폴백**해 직원에게 `SSO session expired. Run 'aws sso login'` 이 뜬다(`api_key_helper/main.py:357-366`).
> 확인: `type "C:\Program Files\ClaudeCode\managed-settings.d\50-gateway.json"` 에 `OIDC_ISSUER_URL`·`OIDC_CLIENT_ID` 가 보이면 정상.

> ℹ️ **로그인 콜백 포트 8090 — 대개 이 블록은 안 돌린다.** ④ 스크립트가 `gateway-cli login` 을 **포트 8090 으로 고정** 호출한다(`--redirect-port` 안 넘김, 기본 8090 `login.py:65`). 8090 이 비어 있으면 **④ 한 줄이면 끝나고, 아래 블록은 실행하지 않는다.** 예외 하나 — 8090 이 이미 다른 도구에 점유돼 로그인이 실패하는 경우에**만**, ④ 대신 손으로 포트를 바꿔 실행한다. Cognito 콜백 화이트리스트가 `localhost:8090|8091|8092` **3개뿐**이라(§3-2 기본값) `8091`/`8092` 중 빈 것을 쓴다.
> ▶ **실행 (선택)** · 직원 PC (Windows) — **8090 이 이미 점유됐을 때만** (④ 온보딩 스크립트 대신)
>
> ```powershell
> gateway-cli login --issuer-url $env:OIDC_ISSUER_URL `
>   --client-id $env:OIDC_CLIENT_ID --redirect-port 8091
> gateway-cli setup --gateway-url $env:ANTHROPIC_BASE_URL `
>   --admin-api-url $env:ADMIN_API_URL
> ```

- 관리설정 기록 위치 = `C:\Program Files\ClaudeCode\managed-settings.d\50-gateway.json`(관리자 권한 필요). **macOS·Linux/WSL 은 둘 다** `/etc/claude-code/managed-settings.d/50-gateway.json` — gateway-cli 는 `sys.platform == "win32"` 만 분기하고 나머지 플랫폼은 전부 `/etc/` 로 보낸다(`gateway-cli/src/cli/managed.py:28-31`, macOS 전용 분기 없음). 그래서 macOS 도 `setup` 에 **sudo 가 필요**하다(`setup.py:46`).
- 대량 배포: managed-settings 파일 + gateway-cli 패키지를 MDM/GPO로 푸시. 그 파일에 `OIDC_*` 가 들어 있으므로(fork 픽스) 직원 env 는 건드릴 필요 없다 — 다만 **파일을 OS별 정확한 경로로** 푸시할 것(§6-2 의 macOS 경로 주의).

> ⚠️ Windows 직원 PC에 **Python 3.11+ 필요**(현재 awsome 클라이언트는 frozen exe 미제공). 폐쇄망/무-Python 요건이 생기면 그때 별도 검토.
