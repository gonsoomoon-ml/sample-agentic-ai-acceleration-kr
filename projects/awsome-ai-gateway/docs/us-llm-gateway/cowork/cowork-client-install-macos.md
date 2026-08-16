# Cowork(Claude Desktop 3P) 클라이언트 설치 — macOS

> **상태: 미검증.**
> Windows 검증본(2026-08-03, `cowork-client-install-windows.md` — 절차 구성·게이트웨이 좌표 동일)과
> Seoul 배포의 Mac 실측 패턴(`.mobileconfig`·credential helper, 2026-06)을 바탕으로 작성했습니다.
> macOS 실기기 종단 검증 전이며, 실측 필요 지점에 ⚠️(실측 전) 을 달았습니다.

**Cowork(Claude Desktop 3P)** 를 게이트웨이에 붙이는 문서입니다. 게이트웨이 쪽 변경(`update-scripts/`)이 끝난 뒤에 하십시오. **Windows PC 는** 같은 폴더의 `cowork-client-install-windows.md` **를 보십시오.**

---

## 1. 한눈에

직원 Mac 의 Cowork 를 회사 게이트웨이에 연결합니다. 열쇠(VK)는 helper 가 매번 자동 발급받으므로 직원이 키를 관리할 일은 없습니다.

| 절차    | 무엇을 하나                            | 창       | 끝난 것을 아는 법                       |
| ----- | --------------------------------- | ------- | -------------------------------- |
| **0** | Cowork 실행 가능 여부 점검                | —       | `ready for Cowork` 문장            |
| **1** | `gateway-cli` 설치 + 회사 계정 로그인      | 🔵      | `api-key-helper` 가 `vk-` 한 줄 출력  |
| **2** | credential helper 작성              | 🔵 → 🔴 | helper 직접 실행 시 `vk-` 한 줄         |
| **3** | Cowork 앱 설치 (offline `.dmg`)      | —       | `/Applications/Claude.app` 존재    |
| **4** | 관리형 설정 — `.mobileconfig` 생성·승인    | 🔵      | `defaults read` 가 여섯 값 출력        |
| **5** | 실행 → 대화 → 게이트웨이 기록 확인             | —       | `usage_logs` 에 `client=cowork`   |

**창 표시**: ▶ 🔵 **Terminal**(직원 본인 계정) · ▶ 🔴 같은 창에서 **`sudo`** 를 붙인 명령 · ▶ 🟢 **운영자**가 배포 EC2 에서.
⚠️ `sudo` 없이 하라는 확인 명령에 `sudo` 를 붙이면 root 기준으로 돌아 토큰을 못 찾습니다.

**운영자에게 미리 받을 것** — ① env 값 4개(`07-client-values.sh` 출력) ② 로그인 계정(이메일+임시 비밀번호) ③ 이 Mac 공인 IP 의 `inbound-cidrs` 등록(`05-allow-client-ip.sh`).

⚠️ **순서를 바꾸지 마십시오.** 설정(절차 4) 전에 앱을 켜면 claude.ai 로그인 화면에서 개인 계정으로 들어가 버릴 여지가 생깁니다. ⏱️ 앱 내려받기 제외 30분 안팎(Windows 실측 기준).

> 로그인 방식(Cognito)은 나중에 회사 통합 로그인으로 바뀔 수 있습니다 — 그때도 절차·명령은 동일하고 `OIDC_ISSUER_URL`·`OIDC_CLIENT_ID` 값만 달라집니다.

---

## 2. 전제

**게이트웨이 쪽** — `update-scripts/README.md` 실행 순서 완료: `https://` base URL(`03-create-cloudfront.sh`), Cowork 라우팅(`01-fix-cowork-routing.sh`), 모델 alias 목록(`00-preflight-check.sh` ACTIVE), **클라이언트 공인 IP 등록(`05-allow-client-ip.sh`)**. IP 가 빠지면 로그인(공개)은 되는데 **VK 발급(IP 제한)만 타임아웃**납니다.

**클라이언트 쪽** — `gateway-cli` 와 로그인 토큰뿐. 같은 Mac 에서 **Claude Code 를 이미 쓰면 로그인을 공유하므로 절차 1 생략.** ⚠️ `gateway-cli` 는 반드시 **fork** 에서 설치(upstream 은 벤더 버그 픽스 3건 부재).

**기기** — **macOS 14 (Sonoma) 이상**, Apple Silicon·Intel 지원. ⚠️ **VM 위 macOS 는 대개 실패**합니다(가상화 기능 미노출) — 절차 0 으로 먼저 거르십시오.

**망** — 아래 호스트가 막혀 있으면 안 됩니다.

| 호스트                     | 언제 쓰나                   | 막혀 있으면                       |
| ----------------------- | ----------------------- | ---------------------------- |
| 게이트웨이 주소 (`https://` URL) | 추론 요청 전부                | 응답 없음                        |
| `OIDC_ISSUER_URL`       | 로그인, VK 갱신              | 로그인 불가                       |
| `claude.ai`             | 점검 도구·설치 파일 다운로드        | 내려받기 불가 (설치 후 불필요)           |
| `downloads.claude.ai`   | **켤 때마다** 작업 환경 번들 다운로드 | 앱은 뜨는데 **Cowork 세션만 안 열림**   |
| `releases.claude.com`   | 자동 업데이트 확인              | 업데이트만 멈춤                     |

⚠️ 네 번째 줄이 가장 헷갈리는 실패입니다 — 폐쇄망이면 **offline 설치판**(절차 3, 번들 내장)을 쓰고 자동 업데이트를 끄십시오(토글 키는 Windows 문서 §7). 📖 [필요 호스트 전체 목록](https://claude.com/docs/third-party/claude-desktop/telemetry#required-egress-paths)

**설치 모드 (결론만)** — 앱은 **per-machine**(`/Applications`), 설정은 **관리형 필수**: `inferenceCredentialHelper` 는 [MDM-Only 키](https://claude.com/docs/third-party/claude-desktop/configuration#inferencecredentialhelper)라 Local(앱 UI `Apply locally`)에 넣으면 **조용히 무시**됩니다. 수동 설치한 `.mobileconfig` 도 관리형 레이어(`/Library/Managed Preferences`)로 들어가므로 **MDM 서버는 필요 없습니다**(Seoul 실측 2026-06). Bootstrap 서버는 helper 키를 실을 수 없어 불가.

---

## 3. 절차

### 절차 0. Cowork 실행 가능 여부 확인

**브라우저**로 아래를 열어 점검 도구를 받고 실행하십시오 (Apple Silicon·Intel 공용).

```
https://claude.ai/api/desktop/darwin/universal/cowork-readiness-check/latest/redirect
```

`This computer is ready for Cowork` 가 나오면 다음으로. Gatekeeper 경고가 뜨면 Finder 에서 **우클릭 → 열기** (⚠️ 실측 전). 불통과 원인은 대개 **macOS 14 미만**(업그레이드 필요) 또는 **VM 위 macOS**(불가)입니다.

### 절차 1. `gateway-cli` 설치 + 로그인

> 그 Mac 에서 **Claude Code 를 쓰고 있다면 이 절 생략** — 토큰(`~/.gateway-cli/`)을 공유합니다.

**⓪ 사전 요구사항** — ▶ 🔵 `git --version`, `uv --version` 둘 다 찍히면 ① 로. `git` 부재 시 Command Line Tools 설치 창 승인. `uv` 부재 시:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

끝나면 **새 터미널**에서 `uv --version` 확인.

**① 저장소** — ⚠️ 반드시 `gonsoomoon-ml` **fork**, 브랜치 `us/deploy-fixes`. ▶ 🔵

```bash
cd ~
git clone -b us/deploy-fixes \
  https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
```

**② 설치** — ▶ 🔵

```bash
uv tool install --from ./gateway-cli gateway-cli
gateway-cli version
```

`command not found` 면 `uv tool update-shell` 후 **새 터미널**에서 재확인 (⚠️ 실측 전).

**③ 운영자에게 받은 값 4개** — 운영자가 배포 EC2 에서(🟢) `bash 07-client-values.sh` 를 돌려 준 **"macOS / Linux"** 절의 `export` 4줄을 붙여넣습니다. ▶ 🔵

```bash
export OIDC_ISSUER_URL="<from operator>"
export OIDC_CLIENT_ID="<from operator>"
export ADMIN_API_URL="<from operator>"
export ANTHROPIC_BASE_URL="<from operator - starts with https://>"
```

⚠️ `ANTHROPIC_BASE_URL` 은 **`https://` 여야** 합니다(Cowork 가 평문 HTTP 거부 — CloudFront 주소).

**④ 로그인** — ⚠️ **③ 을 넣은 그 창에서** (export 값이 그 창에서만 유효). ▶ 🔵

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
bash scripts/onboard-macos-linux.sh
```

브라우저 로그인 화면에서 운영자 발급 계정으로 로그인 (첫 로그인 시 새 비밀번호 설정). 콜백 `localhost:8090` 이 점유돼 실패하면 — 등록 콜백은 `8090`·`8091`·`8092` **3개뿐** — `lsof -nP -iTCP:8090-8092 -sTCP:LISTEN` 으로 빈 포트를 골라:

```bash
gateway-cli login --issuer-url "$OIDC_ISSUER_URL" \
  --client-id "$OIDC_CLIENT_ID" --redirect-port 8091
```

**⑤ 확인** — ▶ 🔵

```bash
api-key-helper 2>/dev/null | grep -m1 '^vk-'
```

`vk-` 한 줄이면 완료. ⚠️ 로그인 성공 ≠ 완료 — VK 발급이 타임아웃나면 이 Mac 의 공인 IP 미등록입니다:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$ADMIN_API_URL/health"
curl -s https://checkip.amazonaws.com
```

첫 줄 `200` 이어야 하고, 아니면 둘째 줄의 IP 를 운영자에게 보내 등록을 요청하십시오.

### 절차 2. credential helper 작성

Cowork 가 요청마다 실행해 VK 한 줄을 받는 스크립트입니다. 위치는 `/usr/local/bin/llm-gateway-helper.sh` — 모든 사용자가 읽고 관리자만 고치는 자리이고, **경로에 사용자명이 없어** 조직 배포 때 프로파일 하나로 됩니다.

**① 값 확인** — ▶ 🔵 `echo "$OIDC_ISSUER_URL"` `echo "$OIDC_CLIENT_ID"` `echo "$ADMIN_API_URL"` 세 값이 찍혀야 합니다. 빈 줄이면 절차 1-③ 의 `export` 를 다시 붙여넣으십시오.

**② 파일 만들기** — ▶ 🔴 (`/usr/local/bin` 에 쓰므로 `sudo` 필요)

```bash
sudo mkdir -p /usr/local/bin
sudo tee /usr/local/bin/llm-gateway-helper.sh >/dev/null <<EOF
#!/bin/bash
set -euo pipefail
export OIDC_ISSUER_URL="$OIDC_ISSUER_URL"
export OIDC_CLIENT_ID="$OIDC_CLIENT_ID"
export ADMIN_API_URL="$ADMIN_API_URL"
export HOME="\${HOME:-/Users/\$(id -un)}"
H="\$HOME/.local/bin/api-key-helper"
[ -x "\$H" ] || H="\$(command -v api-key-helper || true)"
if [ -z "\$H" ]; then
  echo "api-key-helper not found" >&2
  exit 1
fi
"\$H" 2>/dev/null | grep -m1 '^vk-'
EOF
sudo chmod +x /usr/local/bin/llm-gateway-helper.sh
```

`\` 없는 값 세 개는 **지금 창의 값이 파일에 박히고**, `\$` 붙은 것은 실행 시점에 평가됩니다. `HOME` 명시(비로그인 셸 대비)와 `grep -m1 '^vk-'`(첫 `vk-` 한 줄 + **끝 개행 보장**)는 Seoul 실측에서 나온 필수 줄입니다.

**③ 확인** — ▶ 🔵 (⚠️ **`sudo` 붙이지 말 것** — root 기준이 되어 토큰을 못 찾습니다)

```bash
/usr/local/bin/llm-gateway-helper.sh
```

`vk-` 한 줄이면 완료.

### 절차 3. Cowork 설치

**설정보다 앱을 먼저 켜지 마십시오** — 설치만 하고 실행은 절차 5 에서.

**① 설치 파일** — **브라우저**로 받으십시오(명령줄은 403 가능성 — ⚠️ 실측 전). ⚠️ 반드시 **offline 판**: `claude.com/download` 의 표준판은 세션마다 `downloads.claude.ai` 에 의존해 제한망에서 **Cowork 세션만 안 열립니다.** 404 가 나면 신판 전환 구간이니 잠시 뒤 재시도 ([명시된 동작](https://claude.com/docs/third-party/claude-desktop/installation#offline-installation)).

Apple Silicon (「칩」= `Apple M…`):

```
https://claude.ai/api/desktop/darwin/arm64/offline/latest/redirect
```

Intel:

```
https://claude.ai/api/desktop/darwin/x64/offline/latest/redirect
```

**② 설치** — `.dmg` 를 열어 **`Claude.app` 을 `Applications` 로 드래그** (per-machine). 확인: ▶ 🔵 `ls -d /Applications/Claude.app` 이 한 줄 찍히면 완료. MDM 이 앱 설치를 막는 Mac 이면 IT 담당자 문의.

### 절차 4. 관리형 설정 (`.mobileconfig`)

프로파일을 설치하면 값이 `/Library/Managed Preferences/<사용자>/com.anthropic.claudefordesktop.plist` 에 쓰입니다(Windows 의 `HKLM\SOFTWARE\Policies\Claude` 대응). ⚠️ **목록 값은 Windows(JSON 문자열)와 달리 네이티브 plist 배열** — 형식을 섞으면 그 키만 조용히 무시됩니다.

**① 값 채우기** — ▶ 🔵 아래를 실행해 `https://` 주소가 찍히는지 확인합니다(빈 줄이면 절차 1-③ 재실행).

```bash
BASE="$ANTHROPIC_BASE_URL"; echo "$BASE"
```

모델 목록은 아래 `<string>` 4줄이 기본 — 운영자가 준 ACTIVE 목록이 다르면 교체. ⚠️ alias 는 **DB 등록 문자열 그대로** (예: `claude-haiku-4-5-20251001` — 한 글자만 달라도 그 모델만 404).

**② 프로파일 생성** — ▶ 🔵 그대로 붙여넣으십시오 (`$BASE` 만 치환됨).

```bash
cat > ~/Downloads/us-llm-gateway-cowork.mobileconfig <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadDisplayName</key>
      <string>US LLM Gateway - Cowork Inference</string>
      <key>PayloadIdentifier</key>
      <string>com.anthropic.claudefordesktop.us-llm-gateway</string>
      <key>PayloadType</key>
      <string>com.anthropic.claudefordesktop</string>
      <key>PayloadUUID</key>
      <string>c5aedb63-b1d3-4d7c-940c-713c7cb47e4d</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
      <key>inferenceProvider</key>
      <string>gateway</string>
      <key>inferenceGatewayBaseUrl</key>
      <string>$BASE</string>
      <key>inferenceGatewayAuthScheme</key>
      <string>bearer</string>
      <key>inferenceCredentialHelper</key>
      <string>/usr/local/bin/llm-gateway-helper.sh</string>
      <key>inferenceCredentialHelperTtlSec</key>
      <integer>1800</integer>
      <key>inferenceModels</key>
      <array>
        <string>claude-opus-5</string>
        <string>claude-opus-4-8</string>
        <string>claude-sonnet-5</string>
        <string>claude-haiku-4-5-20251001</string>
      </array>
    </dict>
  </array>
  <key>PayloadDisplayName</key>
  <string>US LLM Gateway Cowork</string>
  <key>PayloadIdentifier</key>
  <string>com.anthropic.claudefordesktop.us-llm-gateway.profile</string>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>bcee0872-d3bc-477c-a63e-3334114d5a04</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
</dict>
</plist>
EOF
plutil -lint ~/Downloads/us-llm-gateway-cowork.mobileconfig
```

마지막 줄이 `... OK` 여야 합니다(오류 = 대개 복사 잘림 → 재붙여넣기). `PayloadIdentifier`·`PayloadUUID` 가 고정이라 **다시 돌려 재설치하면 기존 프로파일이 교체**됩니다 — 모델·주소 변경 시 블록을 고쳐 반복하면 됩니다.

**③ 프로파일 설치** — ▶ 🔵 `open ~/Downloads/us-llm-gateway-cowork.mobileconfig` → System Settings 에서 **설치** 승인 (「개인정보 보호 및 보안 → 프로파일」 또는 「일반 → 기기 관리」 — ⚠️ 실측 전).

**④ 확인** — ▶ 🔵

```bash
defaults read \
  "/Library/Managed Preferences/$(whoami)/com.anthropic.claudefordesktop"
```

여섯 키(`inferenceProvider` ~ `inferenceModels`)가 보이고 `inferenceGatewayBaseUrl` 에 실제 주소가 있어야 합니다. "does not exist" 면 프로파일 미승인 → ③ 재시도. 키별 원문 정의는 [Configuration reference](https://claude.com/docs/third-party/claude-desktop/configuration).

### 절차 5. 실행 및 검증

`/Applications/Claude.app` 실행 후 —

1. **첫 화면**: claude.ai **로그인 화면이 뜨면 멈추고** §4 로 (관리형 설정을 못 읽은 것). 로그인 없이 바로 쓸 수 있어야 정상.
2. **모델 목록**: `inferenceModels` 에 넣은 이름들이 보여야 함.
3. **짧은 대화**: `hi` 응답이 오면 Mac→CloudFront→게이트웨이→Bedrock 통과. ⚠️ 게이트웨이 변경 직후엔 **캐시 5분** 대기(그 전엔 신규 모델 404).
4. **게이트웨이 쪽**: 운영자가(🟢) `bash 04-verify.sh` → C 섹션 최근 행 `client=cowork`, `status=SUCCESS`. 상세 기준은 Windows 문서 절차 5-④ 와 동일.

---

## 4. 문제 판별

| 증상                                 | 원인                                                                        |
| ---------------------------------- | ------------------------------------------------------------------------- |
| 앱이 claude.ai 로그인 화면을 띄움            | 프로파일 미설치/미승인 — 절차 4-④ 의 `defaults read` 로 판별                              |
| 모델 목록이 비었거나 다른 이름                  | `inferenceModels` 형식 — plist **배열**이어야 함, JSON 문자열이면 무시됨 (절차 4)            |
| helper 는 VK 를 뱉는데 앱은 인증 실패         | 값이 관리형 레이어에 없음(Local 에 넣음), 또는 출력이 개행으로 안 끝남                               |
| 터미널에서는 helper 가 되는데 앱에서만 실패        | helper 절대경로 오타, 또는 실행권한 없음 → `sudo chmod +x`                               |
| VK 발급 타임아웃 (로그인은 성공)               | 이 Mac 의 공인 IP 가 `inbound-cidrs` 에 없음 → `05-allow-client-ip.sh`             |
| `refresh failed: HTTP 400`         | refresh token 만료 → `gateway-cli login` 재실행                                |
| `uv: command not found`            | PATH 미반영 → 새 터미널, 그래도 안 되면 설치 스크립트 재실행 (절차 1-⓪)                           |
| PyPI 를 못 받음 (사내망)                  | 운영자에게 wheel 파일을 받아 `uv tool install <받은경로>/gateway_cli-*.whl`             |
| 특정 모델만 404                         | alias 오타, 또는 등록 후 5분 미경과                                                  |
| 전 요청 502                           | `01` 미적용 또는 CloudFront→ALB 경로 미개방(`03 --allow-cloudfront`)                |
| 앱은 켜지고 대화도 되는데 **Cowork 세션만** 시작 안 됨 | `downloads.claude.ai` 가 막힘 → offline 설치판으로 다시 설치 (§2 「망」)                  |
| 위와 같은데 방화벽은 열려 있음                  | 백신·EDR 이 Cowork 에이전트를 막음 → 아래                                              |
| 원인이 안 보일 때                         | 앱 로그 — `~/Library/Logs/Claude-3p/main.log`                                 |

**EDR·백신** — Santa·CrowdStrike 등이 `~/Library/Application Support/Claude-3p/claude-code/<버전>/claude.app/Contents/MacOS/claude` 를 차단하면 방화벽 문제와 같은 증상(세션만 불가)입니다. ⚠️ 경로가 아니라 **서명자로 허용**하십시오(경로엔 버전이 들어 있어 업데이트마다 재차단) — Team ID `Q6L2SF6YDW` (Anthropic PBC), Signing ID `com.anthropic.claude-code`. 📖 [Endpoint security software](https://claude.com/docs/third-party/claude-desktop/installation#endpoint-security-software)

---

## 5. 검증 기록

**아직 실기기 검증 전입니다.** 절차 구성·좌표는 Windows 검증(2026-08-03)과, `.mobileconfig`·helper 패턴은 Seoul Mac 실측(2026-06)과 같습니다. macOS 실기기로 절차 0~5 를 완주할 때 채울 항목:

**게이트웨이 쪽** (`04-verify.sh` C 섹션 — Windows §8 과 같은 기준):

- [ ] `client` 분류가 5행 모두 `cowork`
- [ ] 모델 alias 가 `inferenceModels` 에 넣은 이름으로 기록
- [ ] 실시간 질문 행에 `web_search_count ≥ 1`
- [ ] 전 행 `cost_usd > 0`, `status = SUCCESS`
- [ ] 로그인 스크립트의 `gateway health: 200` (CloudFront 경로)

**macOS 고유** (본문 ⚠️ 마커 대응):

- [ ] 설치판·점검 도구의 `curl -L` 수신 가능 여부 (Windows 는 403)
- [ ] readiness check 앱의 Gatekeeper 경고 여부 (절차 0)
- [ ] `uv tool install` 후 PATH 자동 등록 여부 (절차 1-②)
- [ ] System Settings 프로파일 승인의 정확한 메뉴 경로 (절차 4-③)
- [ ] `Help → Troubleshooting → Copy Managed Configuration Report` 메뉴 존재 여부

검증 후: 머리말 배지를 **"종단 검증 완료 — <날짜>, <기기·macOS·앱 버전>"** 으로 바꾸고 ⚠️ 마커를 실측 결과로 치환합니다.

---

## 6. 조직 배포·토글·참고

**조직 배포** — 테스트에 쓴 **같은 `.mobileconfig`** 를 Jamf·Intune 등 Apple MDM 으로 배포하고, helper 파일도 MDM 스크립트/패키지로 `/usr/local/bin/llm-gateway-helper.sh` 에 같은 내용으로 밀어넣습니다(경로가 사용자명과 무관해 전 기기 공통). 프로파일 저작은 **관리형 설정이 없는 Mac** 에서 앱의 **Developer → Configure Third-Party Inference… → Export** 로도 가능하고, 그 창의 **Egress Requirements** 가 방화벽 허용 호스트 `.txt` 도 내보냅니다.

**선택 토글** (`chatTabEnabled`·`autoModeEnabled`·`disabledBuiltinTools` 등) — 키 이름·값은 OS 무관 동일, **정본은 Windows 문서 §7**. macOS 차이 세 가지: ① 절차 4-② 블록의 내부 `<dict>` 에 키를 추가해 프로파일 **재생성·재설치**(같은 식별자라 교체됨) ② 목록 값은 **네이티브 배열** ③ 반영은 앱 **Cmd+Q 후 재실행**. 되돌리기는 값 `false` 가 아니라 **키를 빼고 재설치**. ⚠️ `inference*` 여섯 키는 빼면 게이트웨이 연결이 끊어집니다.

**공식 문서** (값이 어긋나면 아래가 정본):

- [Installation and setup](https://claude.com/docs/third-party/claude-desktop/installation) — 기기 요구사항·offline 설치판·EDR·자동 업데이트
- [Configuration reference](https://claude.com/docs/third-party/claude-desktop/configuration) — 설정 키 전체
- [Deploy with MDM](https://claude.com/docs/third-party/claude-desktop/mdm) — 관리형 레이어·조직 배포
- [Telemetry and egress](https://claude.com/docs/third-party/claude-desktop/telemetry#required-egress-paths) — 방화벽 호스트
- [macOS 배포 (support)](https://support.claude.com/en/articles/12611117-deploy-claude-desktop-for-macos) — Jamf·Intune 절차

**설치 파일 고정 주소** (항상 최신판 — 배포 자동화용): 점검 도구 `…/darwin/universal/cowork-readiness-check/latest/redirect` · offline 설치판 `…/darwin/arm64/offline/latest/redirect`(Apple Silicon) / `…/darwin/x64/offline/latest/redirect`(Intel) — 전체 URL 은 절차 0·3. ⚠️ `claude.com/download` 는 표준판이라 배포에 쓰지 마십시오(절차 3).
