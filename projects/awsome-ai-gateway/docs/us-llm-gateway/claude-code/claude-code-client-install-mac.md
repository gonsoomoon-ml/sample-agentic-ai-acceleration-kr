# US LLM Gateway — Claude Code 설치 가이드 (macOS)

## 허용목록에 등록된 네트워크에서 시작한다 (예: 회사 VPN)

게이트웨이와 키 발급 서버(admin-api) **둘 다 IP 허용목록**으로 잠겨 있다. 운영자가 **허용목록에 넣어 둔 네트워크**(회사 VPN 출구 대역, 또는 등록된 개인 IP)에서 접속해야 한다. **VPN 만 켜면 된다.**

> 🔴 **VPN 을 쓴다면 운영자가 알려준 지역/프로필로 붙어야 한다.** VPN 은 목적지 리전마다 출구 IP 가 다를 수 있어, 다른 지역으로 붙으면 허용목록 밖이고 아래 확인에서 바로 걸린다. 어느 네트워크가 등록돼 있는지는 운영자에게 확인한다.

VPN 없이 진행하면 증상이 고약하다 — **로그인은 성공하는데**(Cognito 는 공개) 그다음 키 발급이 **조용히 타임아웃**난다. 인증 문제로 보이지만 네트워크 문제다. 그래서 여기서 먼저 확인한다.

▶ **확인** — VPN 을 켠 뒤 두 줄을 각각 실행한다

```bash
curl -s -o /dev/null -w 'gateway %{http_code}\n' --max-time 10 https://gateway-{{env}}.{{DOMAIN}}/health
```

```bash
curl -s -o /dev/null -w 'admin   %{http_code}\n' --max-time 10 https://admin-api-{{env}}.{{DOMAIN}}/health
```


| 결과        | 뜻                                          |
| --------- | ------------------------------------------ |
| `200` × 2 | 통과 — 2절로 간다                                |
| `000`     | **타임아웃 = 허용목록 밖.** VPN 이 꺼졌거나 운영자가 알려준 지역/프로필이 아니다 |
| 한쪽만 200   | 허용목록이 한쪽에만 적용된 상태 → 운영자에게 보고(아래 참고)        |




## 2. Claude Code 설치

▶ **실행**

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

```bash
source ~/.zshrc && claude --version
```

버전이 찍히면 성공이다.

> ℹ️ **native installer 를 쓴다.** Node.js 가 필요 없고, 관리자 권한도 필요 없으며(사용자 폴더에만 씀), 자동 업데이트가 된다.

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

---



## 3. gateway-cli 설치

Claude Code 를 게이트웨이에 붙이는 도구다. **반드시 아래 저장소(fork)에서** 설치한다.

▶ **실행**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

```bash
cd ~ && git clone -b us/deploy-fixes \
  https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
```

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
```

```bash
uv tool install --from ./gateway-cli gateway-cli
```

```bash
which gateway-cli api-key-helper
```

두 줄 다 나와야 한다.

---



## 4. 로그인 (OIDC)

값 4개는 운영자가 `07-client-values.sh --claude-code` 로 뽑아 준다 — `{{…}}` 자리를 그 값으로 바꿔 붙여넣는다.

▶ **실행** — 그대로 붙여넣는다

```bash
export OIDC_ISSUER_URL="https://cognito-idp.{{REGION}}.amazonaws.com/{{COGNITO_POOL_ID}}"
export OIDC_CLIENT_ID="{{OIDC_CLIENT_ID}}"
export ADMIN_API_URL="https://admin-api-{{env}}.{{DOMAIN}}"
export ANTHROPIC_BASE_URL="https://gateway-{{env}}.{{DOMAIN}}"
```

이어서 로그인한다. **같은 터미널 창**에서 실행해야 한다 — 위 `export` 는 그 창에서만 유효하다.

```bash
gateway-cli login --issuer-url "$OIDC_ISSUER_URL" \
  --client-id "$OIDC_CLIENT_ID" --redirect-port 8090
```

브라우저가 열리면 **아래 계정**으로 로그인한다.


| 항목      | 값                    |
| ------- | -------------------- |
| 아이디     | `{{EMAIL}}` (운영자가 별도 전달) |
| 임시 비밀번호 | `{{TEMP_PASSWORD}}` (운영자가 별도 전달)     |


들어가자마자 **새 비밀번호 설정** 화면이 뜬다 — 정상이다. 12자 이상, 대문자·소문자·숫자를 포함한다.

> 🔴 **임시 비밀번호는 발급 후 7일까지만 유효하다.** 새 비밀번호를 정하고 나면 위 임시 비밀번호는 무효가 되니 **이 문서에서 지운다.** 7일이 지나 `NotAuthorizedException` 이 나면 운영자에게 재발급을 요청한다.

> ⚠️ **등록된 콜백 포트는 8090 · 8091 · 8092 세 개뿐이다.** 다른 포트를 쓰면 Cognito 가 *"An error was encountered with the requested page"* 를 띄운다(인증 실패처럼 안 보여서 헷갈린다). 8090 이 이미 쓰이고 있으면(Cursor 등) 빈 포트를 골라 쓴다:
>
> ```bash
> lsof -nP -iTCP:8090-8092 -sTCP:LISTEN
> ```

**확인** — VK(가상 키) 한 줄이 나오면 여기까지 정상이다.

```bash
api-key-helper 2>/dev/null | grep '^vk-'
```

아무것도 안 나오면 **VPN 이 끊겼는지**부터 본다(1절의 확인 두 줄을 다시 실행).

---



## 5. 게이트웨이 연결

▶ **실행** (sudo 암호를 물어본다 — 시스템 경로에 쓴다)

```bash
gateway-cli setup --gateway-url "$ANTHROPIC_BASE_URL" \
  --admin-api-url "$ADMIN_API_URL"
```

`/Library/Application Support/ClaudeCode/managed-settings.d/50-gateway.json` 이 만들어진다. 이 파일은 Claude Code 설정 계층의 **최상위**라 이 Mac 의 모든 `claude` 실행에 적용된다.

**원복**: `gateway-cli disable`

> ℹ️ `apiKeyHelper` 는 절대경로가 아니라 **이름**(`"api-key-helper"`)으로 기록되므로 Claude Code 가 **PATH 에서 찾아야** 한다. 터미널에서 `claude` 를 띄우면 문제없다. GUI 런처로 띄워 못 찾는다면 절대경로로 다시 지정한다:
>
> ```bash
> gateway-cli setup --gateway-url "$ANTHROPIC_BASE_URL" \
>   --admin-api-url "$ADMIN_API_URL" \
>   --api-key-helper "$HOME/.local/bin/api-key-helper"
> ```

---



## 6. 검증

▶ **실행**

```bash
claude
```

그다음 아무 질문이나 던져 답이 오면 끝이다.

> 🔴 `Login method: Claude Max account` **가 그대로 남아 있으면 붙지 않은 것이다.** 3절의 upstream 설치 문제일 가능성이 가장 높다.

---



## 부록 — 이 게이트웨이를 쓴다는 것

- 요청은 개인 Claude 계정이 아니라 **회사 게이트웨이**를 거친다. 팀별 **예산·rate limit** 이 적용되고 **사용량이 기록**된다.
- 인증은 **가상 키(VK)** 로 하고, `api-key-helper` 가 만료 전에 자동으로 갱신한다. 손으로 키를 관리할 일은 없다.
- OIDC refresh token 은 **7일**이다. 일주일 이상 안 쓰면 4절 로그인을 다시 한다.
- 쓸 수 있는 모델 목록은 운영자가 정한다. `/status` 나 모델 선택기에 없는 모델은 호출해도 404 다.

### 대시보드 (admin-ui)

내 사용량·남은 예산을 화면으로 볼 수 있다. 로그인은 **4절과 같은 Cognito 계정**이다.

```
https://admin-{{env}}.{{DOMAIN}}
```

- **VPN 필요** — 게이트웨이와 같은 IP 허용목록으로 잠겨 있다(1절).
- 모델·예산·팀 설정 같은 **관리 메뉴는 `ClaudeAdmin` 그룹에만** 보인다. 일반 사용자에게는 자기 사용량만 보인다.

