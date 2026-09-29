# Codex(CLI · 데스크톱) 클라이언트 설치 — macOS

> **상태: 미검증.**
> Cowork macOS 문서(`../cowork/cowork-client-install-macos.md`)와 같은 인증 구조
> (`gateway-cli` 로그인 → `api-key-helper` → VK)를 바탕으로 작성했습니다.
> Codex 의 `auth.command`(command-backed bearer token)는 공식 설정 reference 에
> 명시된 기능이나, 이 게이트웨이와의 종단 실기기 검증 전입니다. 실측 필요 지점에
> ⚠️(실측 전) 을 달았습니다.

**Codex CLI** 와 **Codex 데스크톱(ChatGPT 앱 내 Codex / Codex Desktop)** 을
게이트웨이에 붙이는 문서입니다. 둘은 **같은 `~/.codex/config.toml`** 을 읽으므로
설정은 한 번만 합니다. 게이트웨이 쪽 변경(`update-scripts/` + codex 라우팅
활성화)이 끝난 뒤에 하십시오.

---

## 1. 한눈에

직원 Mac 의 Codex 를 회사 게이트웨이에 연결합니다. 열쇠(VK)는 Codex 가
`auth.command` 로 helper 를 실행해 자동 발급·갱신하므로 직원이 키를 관리할 일은
없습니다 — Cowork 의 `inferenceCredentialHelper` 와 같은 구조입니다.

| 절차 | 무엇을 하나 | 창 | 끝난 것을 아는 법 |
| --- | --- | --- | --- |
| **1** | `gateway-cli` 설치 + 회사 계정 로그인 | 🔵 | `api-key-helper` 가 `vk-` 한 줄 출력 |
| **2** | credential helper 작성 | 🔵 → 🔴 | helper 직접 실행 시 `vk-` 한 줄 |
| **3** | Codex 설치 (CLI / 데스크톱) | — | `codex --version` 출력 |
| **4** | `~/.codex/config.toml` 작성 | 🔵 | `codex` 가 게이트웨이로 호출 |
| **5** | 실행 → 짧은 작업 → 게이트웨이 기록 확인 | — | `usage_logs` 에 `client=codex` |

**창 표시**: ▶ 🔵 **Terminal**(직원 본인 계정) · ▶ 🔴 같은 창에서 **`sudo`** 를 붙인 명령 · ▶ 🟢 **운영자**가 배포 EC2 에서.
⚠️ `sudo` 없이 하라는 확인 명령에 `sudo` 를 붙이면 root 기준으로 돌아 토큰을 못 찾습니다.

**운영자에게 미리 받을 것** — ① env 값 4개(`07-client-values.sh` 출력) ② 로그인 계정(이메일+임시 비밀번호) ③ 이 Mac 공인 IP 의 `inbound-cidrs` 등록(`05-allow-client-ip.sh`).

> ⚠️ **게이트웨이 전제** — codex 라우팅이 활성화돼 있어야 합니다:
> `model.routing_profiles` 의 `client='codex'` 행 enabled, `model.model_aliases` 의
> `codex-gpt`(또는 `codex-gpt-5.6-*`) ACTIVE, 그리고 해당 사용자/팀의
> `allowed_models` 에 그 alias 가 포함돼 있어야 합니다. 빠져 있으면
> `403 Model not allowed` 입니다.

---

## 2. 전제

**게이트웨이 쪽** — `update-scripts/README.md` 실행 순서 완료: `https://` base
URL(`03-create-cloudfront.sh`), codex 라우팅 프로필 + alias ACTIVE, **클라이언트
공인 IP 등록(`05-allow-client-ip.sh`)**. IP 가 빠지면 로그인(공개)은 되는데
**VK 발급(IP 제한)만 타임아웃**납니다.

**클라이언트 쪽** — `gateway-cli` 와 로그인 토큰뿐. 같은 Mac 에서 **Claude Code
또는 Cowork 를 이미 쓰면 로그인을 공유하므로 절차 1 생략.** ⚠️ `gateway-cli` 는
반드시 **fork** 에서 설치(upstream 은 벤더 버그 픽스 3건 부재).

**망** — 아래 호스트가 막혀 있으면 안 됩니다.

| 호스트 | 언제 쓰나 | 막혀 있으면 |
| --- | --- | --- |
| 게이트웨이 주소 (`https://` URL) | 추론 요청 전부 | 응답 없음 |
| `OIDC_ISSUER_URL` | 로그인, VK 갱신 | 로그인/VK 불가 |
| `registry.npmjs.org` 등 | CLI 설치 시 | 설치 불가 |
| `chatgpt.com` 등 | 데스크톱 앱 다운로드·업데이트 | 앱 설치 불가 |

**설정 위치 (결론만)** — provider·auth 설정은 **반드시 사용자 레벨
`~/.codex/config.toml`** 에 넣습니다. 프로젝트 안의 `.codex/config.toml` 에
`model_provider`·`model_providers` 를 써도 Codex 가 **무시**합니다(시작 경고만
출력). 이는 Codex 공식 동작입니다.

---

## 3. 절차

### 절차 1. `gateway-cli` 설치 + 로그인

> 그 Mac 에서 **Claude Code / Cowork 를 쓰고 있다면 이 절 생략** — 토큰
> (`~/.gateway-cli/`)을 공유합니다.

**⓪ 사전 요구사항** — ▶ 🔵 `git --version`, `uv --version` 둘 다 찍히면 ① 로.
`git` 부재 시 Command Line Tools 설치 창 승인. `uv` 부재 시:

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

`command not found` 면 `uv tool update-shell` 후 **새 터미널**에서 재확인.

**③ 운영자에게 받은 값 4개** — 운영자가 배포 EC2 에서(🟢) `bash
07-client-values.sh` 를 돌려 준 **"macOS / Linux"** 절의 `export` 4줄을
붙여넣습니다. ▶ 🔵

```bash
export OIDC_ISSUER_URL="<from operator>"
export OIDC_CLIENT_ID="<from operator>"
export ADMIN_API_URL="<from operator>"
export ANTHROPIC_BASE_URL="<from operator - starts with https://>"
```

> `ANTHROPIC_BASE_URL` 은 이름과 달리 **게이트웨이 base URL** 입니다 — Codex 도
> 같은 주소를 씁니다(절차 4 의 `base_url`).

**④ 로그인** — ⚠️ **③ 을 넣은 그 창에서** (export 값이 그 창에서만 유효). ▶ 🔵

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
bash scripts/onboard-macos-linux.sh
```

브라우저 로그인 화면에서 운영자 발급 계정으로 로그인 (첫 로그인 시 새 비밀번호
설정). 콜백 `localhost:8090` 이 점유돼 실패하면 — 등록 콜백은
`8090`·`8091`·`8092` **3개뿐** — `lsof -nP -iTCP:8090-8092 -sTCP:LISTEN` 으로
빈 포트를 골라:

```bash
gateway-cli login --issuer-url "$OIDC_ISSUER_URL" \
  --client-id "$OIDC_CLIENT_ID" --redirect-port 8091
```

**⑤ 확인** — ▶ 🔵

```bash
api-key-helper 2>/dev/null | grep -m1 '^vk-'
```

`vk-` 한 줄이면 완료. ⚠️ 로그인 성공 ≠ 완료 — VK 발급이 타임아웃나면 이 Mac 의
공인 IP 미등록입니다:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$ADMIN_API_URL/health"
curl -s https://checkip.amazonaws.com
```

첫 줄 `200` 이어야 하고, 아니면 둘째 줄의 IP 를 운영자에게 보내 등록을
요청하십시오.

### 절차 2. credential helper 작성

Codex 가 토큰이 필요할 때마다(`refresh_interval_ms` 주기 + 401 재시도 시)
실행해 VK 한 줄을 받는 스크립트입니다. **Cowork 절차 2 와 같은 파일**입니다 —
이미 `/usr/local/bin/llm-gateway-helper.sh` 가 있으면 이 절 생략.

**① 값 확인** — ▶ 🔵 `echo "$OIDC_ISSUER_URL"` `echo "$OIDC_CLIENT_ID"`
`echo "$ADMIN_API_URL"` 세 값이 찍혀야 합니다. 빈 줄이면 절차 1-③ 의 `export`
를 다시 붙여넣으십시오.

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

`\` 없는 값 세 개는 **지금 창의 값이 파일에 박히고**, `\$` 붙은 것은 실행
시점에 평가됩니다. Codex 는 이 명령의 **stdout 첫 `vk-` 줄**을 bearer 토큰으로
씁니다(공백 trim, 빈 출력은 오류 처리).

**③ 확인** — ▶ 🔵 (⚠️ **`sudo` 붙이지 말 것** — root 기준이 되어 토큰을 못
찾습니다)

```bash
/usr/local/bin/llm-gateway-helper.sh
```

`vk-` 한 줄이면 완료.

### 절차 3. Codex 설치

둘 중 필요한 쪽, 또는 둘 다 설치합니다 — **`~/.codex/config.toml` 을 공유**하므로
절차 4 설정은 한 번이면 됩니다.

**① Codex CLI** — ▶ 🔵 둘 중 하나:

```bash
npm install -g @openai/codex        # Node.js 필요
# 또는
brew install codex                  # Homebrew
```

확인: ▶ 🔵 `codex --version` 이 찍히면 완료.

**② Codex 데스크톱** — ChatGPT macOS 앱(또는 Codex 데스크톱 앱)을 설치합니다.
⚠️ **실측 전**: 데스크톱 앱의 Codex 워크스페이스가 custom provider 사용 시에도
ChatGPT 로그인을 요구하는지는 실기기 확인이 필요합니다 — 로그인 화면이 뜨면
§4 의 판별표를 보십시오.

### 절차 4. `~/.codex/config.toml` 작성

**① 값 확인** — ▶ 🔵

```bash
BASE="$ANTHROPIC_BASE_URL"; echo "$BASE"
```

`https://` 주소가 찍혀야 합니다(빈 줄이면 절차 1-③ 재실행).

**② 설정 파일 생성** — ▶ 🔵 그대로 붙여넣으십시오 (`$BASE` 만 치환됨).
기존 `~/.codex/config.toml` 이 있으면 덮어쓰지 말고 아래 블록을 병합하십시오.

```bash
mkdir -p ~/.codex
cat > ~/.codex/config.toml <<EOF
model = "gpt-5.5"
model_provider = "gateway"

[model_providers.gateway]
name = "US LLM Gateway (Mantle GPT-5.5)"
base_url = "$BASE/v1"
wire_api = "responses"
requires_openai_auth = false

[model_providers.gateway.auth]
command = "/usr/local/bin/llm-gateway-helper.sh"
timeout_ms = 10000
refresh_interval_ms = 300000
EOF
cat ~/.codex/config.toml
```

키 설명:

- **`wire_api = "responses"`** — 필수. 게이트웨이의 codex 경로는 OpenAI
  Responses API(`/v1/responses`)만 받습니다.
- **`auth.command`** — Codex 가 토큰이 필요할 때마다 helper 를 실행해 VK 를
  받습니다. **`env_key` 와 동시 사용 불가.**
- **`refresh_interval_ms = 300000`** — 5분마다 선제 갱신(VK 는 1시간 유효).
  `0` 이면 401 재시도 때만 갱신합니다.
- **`model`** — 두 가지 선택지가 있습니다.
  - `model = "gpt-5.5"` (실제 OpenAI 모델명, **권장 기본값**): Codex 내장
    메타데이터(context window 등) 테이블에 있어 경고 없이 동작합니다.
    게이트웨이 alias 와 일치하지 않으므로 **routing_profile 의
    `default_model`(기본 `codex-gpt`)로 라우팅**되고, usage_logs·대시보드도
    그 alias 로 기록됩니다. (`gateway-clients/codex-box/entrypoint.sh` 실측)
  - `model = "<게이트웨이 alias>"` (예: `codex-gpt-5.6-terra`): 게이트웨이가
    ACTIVE `BEDROCK_MANTLE_OPENAI` alias 와 일치하는 `model` 값을 **실제
    모델 선택으로 인식**해 해당 모델로 라우팅합니다(GPT-5.6 별칭과 함께
    추가된 per-request model selection). 단, alias 는 Codex 메타데이터
    테이블에 없어 "Model metadata not found" 경고 + fallback 이 발생합니다.
    등록 alias: `codex-gpt`(GPT-5.5) · `codex-gpt-5.6-sol` /
    `codex-gpt-5.6-terra` / `codex-gpt-5.6-luna`(GPT-5.6). ⚠️ alias 를 쓰려면
    그 alias 가 ACTIVE 이고 키 scope(`allowed_models`)에 포함돼 있어야
    합니다 — scope 밖 alias 를 요청해도 다른 모델로 우회되지 않습니다.

⚠️ **실측 전**: 데스크톱 앱이 `config.toml` 검증을 CLI 보다 엄격하게 하는
사례가 보고된 적 있습니다(`amazon-bedrock` provider 의 `auth`/`base_url` 거부 —
앱 버전 26.721 에서 해결). custom provider `auth.command` 도 같은 제한이
있으면 **CLI 는 되는데 데스크톱만 거부**하는 증상이 나올 수 있습니다 — 그때는
앱 업데이트 후 재시도하거나 CLI 사용으로 우회하십시오.

### 절차 5. 실행 및 검증

1. **CLI**: ▶ 🔵 `codex "hi"` (또는 `codex exec "hi"`) — 응답이 오면
   Mac→CloudFront→게이트웨이→Mantle 통과. ⚠️ 게이트웨이 변경 직후엔
   **라우팅 캐시 5분** 대기(그 전엔 404 가능).
2. **데스크톱**: 앱에서 Codex 스레드를 열어 짧은 요청 — 같은 경로를 탑니다.
   provider/auth 를 바꿨으면 앱 완전 종료(Cmd+Q) 후 재실행.
3. **게이트웨이 쪽**: 운영자가(🟢) `bash 04-verify.sh` → C 섹션 최근 행
   `client=codex`, `status=SUCCESS`, model 은 `codex-gpt`(또는 라우팅 프로필의
   default alias)로 기록돼야 합니다. Codex 가 보내는 `originator` 헤더
   (`codex_cli_rs` / `codex_exec`, 판정은 `startswith("codex")`)로
   `client=codex` 를 식별합니다 — ⚠️ **데스크톱 앱의 originator 값은 실측
   필요**(codex 접두어가 아니면 `client=other` 로 잡힐 수 있음).

---

## 4. 문제 판별

| 증상 | 원인 |
| --- | --- |
| `401` / 인증 실패 | helper 가 `vk-` 를 못 뱉음 → 절차 2-③ 직접 실행으로 판별 |
| helper 는 되는데 Codex 만 인증 실패 | `auth.command` 경로 오타·실행권한 없음 → `sudo chmod +x` |
| `403 Model not allowed` | 사용자/팀 `allowed_models` 에 `codex-gpt` 미포함 → admin UI 에서 추가 |
| `404` / 모델 없음 | `codex-gpt` alias INACTIVE, 또는 라우팅 캐시 5분 미경과 |
| "Model metadata not found" 경고 | `model` 에 게이트웨이 alias 를 넣음 — 의도한 모델 선택이면 무시 가능, 아니면 `gpt-5.5` 로 교체 (절차 4) |
| config 를 썼는데 적용이 안 됨 | 프로젝트 `.codex/config.toml` 에 넣음 → 반드시 `~/.codex/config.toml` (절차 2) |
| 데스크톱만 거부 (CLI 는 정상) | 앱의 config 검증 — 앱 업데이트 후 재시도, 또는 CLI 우회 (절차 4 ⚠️) |
| VK 발급 타임아웃 (로그인은 성공) | 이 Mac 공인 IP 가 `inbound-cidrs` 에 없음 → `05-allow-client-ip.sh` |
| `refresh failed: HTTP 400` | refresh token 만료 → `gateway-cli login` 재실행 |
| `uv: command not found` | PATH 미반영 → 새 터미널, 그래도 안 되면 설치 스크립트 재실행 (절차 1-⓪) |
| 특정 시점부터 전 요청 인증 실패 | refresh token 만료로 helper 가 빈 출력 → `gateway-cli login` 재실행 |
| 전 요청 502 | CloudFront→ALB 경로 미개방(`03 --allow-cloudfront`) |
| `codex exec` 는 되는데 대화형만 이상 | originator 가 `codex_exec` vs `codex_cli_rs` — 둘 다 `codex` 접두어라 정상 |

---

## 5. 검증 기록

**아직 실기기 검증 전입니다.** 인증 구조는 Cowork 와 동일하고, `auth.command`
계약·`wire_api="responses"`·`gpt-5.5` 모델명은 공식 문서와 `codex-box` 실측
주석 근거입니다. macOS 실기기로 절차 1~5 를 완주할 때 채울 항목:

**게이트웨이 쪽** (`04-verify.sh` C 섹션 — Cowork §5 와 같은 기준):

- [ ] `client` 분류가 전 행 `codex` (⚠️ 데스크톱 앱 originator 실측 포함)
- [ ] 모델이 의도한 alias 로 기록 — `model = "gpt-5.5"` 면 default_model(`codex-gpt`), alias 명시 시 그 alias
- [ ] 전 행 `cost_usd > 0`, `status = SUCCESS`
- [ ] 로그인 스크립트의 `gateway health: 200` (CloudFront 경로)

**Codex 고유** (본문 ⚠️ 마커 대응):

- [ ] `auth.command` 가 CLI·데스크톱 양쪽에서 토큰을 정상 발급
- [ ] `refresh_interval_ms` 주기 갱신 및 401 재시도 갱신 동작
- [ ] 데스크톱 앱의 custom provider `auth.command` 수용 여부 (앱 버전 기록)
- [ ] 데스크톱 앱의 Codex 워크스페이스가 ChatGPT 로그인을 요구하는지
- [ ] `codex` / `codex exec` 양쪽의 `client=codex` 식별

검증 후: 머리말 배지를 **"종단 검증 완료 — <날짜>, <기기·macOS·앱/CLI 버전>"**
으로 바꾸고 ⚠️ 마커를 실측 결과로 치환합니다.

---

## 6. 조직 배포·참고

**조직 배포** — helper 파일(`/usr/local/bin/llm-gateway-helper.sh`)은 경로가
사용자명과 무관해 MDM 스크립트/패키지로 그대로 밀어넣을 수 있습니다.
`~/.codex/config.toml` 은 사용자 홈 아래 파일이라 MDM 프로파일로는 못 넣고,
설정 스크립트로 배포합니다(사용자 컨텍스트에서 실행 — root 로 쓰면
`/var/root/.codex` 에 들어갑니다).

**대안: 격리 컨테이너** — 호스트의 `~/.codex`·셸 설정을 전혀 안 건드리는 방식이
필요하면 `gateway-clients/` 의 codex-box(`./gw.sh codex`)를 씁니다. 단, 이
방식은 `GATEWAY_VK` 를 env 로 주입하는 **수동 갱신** 방식이라(1시간 유효,
`./gw.sh vk` 재실행) 상시 사용은 본 문서의 `auth.command` 방식이 낫습니다.

**공식 문서** (값이 어긋나면 아래가 정본):

- [Codex Configuration Reference](https://developers.openai.com/codex/config-reference) — `model_providers`, `auth.command`, `refresh_interval_ms`
- [Codex Advanced Configuration](https://developers.openai.com/codex/config-advanced) — command-backed provider auth 상세
