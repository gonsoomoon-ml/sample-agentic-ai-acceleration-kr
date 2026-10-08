# 8-A. Claude Code Auto mode 서버 분류기

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-A** · 업데이트 ID **US-17** · 등급 **권장**

Claude Code 의 Auto mode 는 도구를 실행하기 전에 "안전한가" 판정을 받는다. Claude Code 는 판정을 부탁하는 표시(`anthropic-beta` 헤더의 `dangerous-tool-use`)와 판단 재료(본문 `safeguards`)를 실어 보내고, 응답의 `safeguard_results` 로 판정을 받는다. 지금 게이트웨이는 beta 헤더를 전부 버려서 두 가지가 생긴다.

- 판정이 오지 않으니 Claude Code 가 PC 쪽 분류기로 바꾼다. 판정이 필요한 도구마다 별도 요청(약 4.7만 토큰)이 나가고, "classifier 요청 과금" 안내가 뜬다.
- Claude Code 2.1.289 이상은 대화 중간 메시지에 턴별 effort(`output_config`)를 붙인다. 그 beta 가 없으면 세션 첫 요청이 400 으로 한 번 실패한 뒤 다시 보낸다.

US-17 은 beta 중 두 개와 `safeguards` 만 Bedrock 으로 넘기고(나머지는 지금처럼 버린다 — Bedrock 은 모르는 beta 하나에도 요청 전체를 거절한다), 웹 검색 경로에서도 판정을 그대로 돌려준다.

```text
[지금] 게이트웨이가 beta·safeguards 를 버림

 사용자 프롬프트
   |
   v
 Claude Code -- ① 본 요청 (beta + safeguards) ----> 게이트웨이 --(버림)--> Bedrock
 Claude Code <-- ② 400 (턴별 effort 거부) --------- 게이트웨이 <---------- Bedrock
   |
   |  턴별 effort 를 끄고 다시 보냄 (세션당 한 번)
   v
 Claude Code -- ③ 다시 보냄 (턴별 effort 뺌) -----> 게이트웨이 --(버림)--> Bedrock
 Claude Code <-- ④ 응답: tool_use (판정 없음) ----- 게이트웨이 <---------- Bedrock
   |
   |  판정이 없으므로 로컬 분류기로 전환 + 과금 안내
   v
 Claude Code -- ⑤ 분류기 요청 (약 4.7만 토큰) ----> 게이트웨이 ----------> Bedrock
 Claude Code <-- ⑥ 판정: 허용 / 차단 -------------- 게이트웨이 <---------- Bedrock
   |
   v
 ⑦ 도구 실행 (예: curl) --> ⑧ 결과를 담아 다음 본 요청
```

```text
[수정 후] 게이트웨이가 beta 2개·safeguards 를 넘기고 판정을 돌려줌

 사용자 프롬프트
   |
   v
 Claude Code -- ① 본 요청 (beta + safeguards) ----> 게이트웨이 --(전달)--> Bedrock
 Claude Code <-- ② tool_use + safeguard_results --- 게이트웨이 <---------- Bedrock
   |
   |  Bedrock 의 판정(not_flagged / flagged)을 그대로 사용
   v
 ③ 도구 실행 (예: curl) --> ④ 결과를 담아 다음 본 요청
```

⑤⑥ 은 curl 처럼 판정이 필요한 명령일 때만 생긴다.

- 바뀌는 것: gateway-proxy 이미지 하나(`1.0.84-safeguards`). DB, 다른 서비스, values 의 다른 값은 그대로다.
- 넘기는 beta: `dangerous-tool-use-2026-09-03`(본문 `safeguards` 와 함께), `per-turn-control-2026-07-01`
- 끄기: 설정 `BEDROCK_FORWARD_BETAS` 를 빈 값으로 하면 재빌드 없이 이전 동작이 된다(7절).
- 시간: 약 20분(이미지 빌드 포함). gateway-proxy 만 롤링되어 추론은 끊기지 않는다.
- 직원 PC 는 바꿀 것이 없다.

▶ **실행** · 배포 EC2 — 위에서부터 그대로. 📋 = 기대 출력.

## 1. 저장소 최신화

```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

📋 `values restored OK`. `git remote -v` 의 origin 은 `gonsoomoon-ml/…` 이어야 한다.

## 2. 지금 상태 확인 (패치 전)

확인 스크립트는 Claude Code 와 같은 모양의 요청 3개(턴별 effort · 판정 비스트리밍 · 판정 스트리밍)를 게이트웨이에 보내 결과를 판정한다. 모델이 도구를 부르더라도 실행하지 않는다. 비용은 짧은 요청 몇 건이다.

키(VK)는 직원 PC 와 같은 방법으로 관리자 PC 에서 받는다: `api-key-helper 2>/dev/null | grep -m1 '^vk-'`. 배포 EC2 에는 브라우저 로그인이 없어 직접 받기 어렵다. 게이트웨이 주소는 직원 PC 의 `ANTHROPIC_BASE_URL` 값이다.

```bash
cd ~/awsome-ai-gateway/deployment/scripts
read -rs GATEWAY_KEY && export GATEWAY_KEY
python3 check-safeguards-passthrough.py https://<게이트웨이 주소>
```

둘째 줄에서 `vk-…` 를 붙여 넣고 Enter 를 누른다(화면에 안 보인다). 키를 `ANTHROPIC_AUTH_TOKEN` 으로 export 하지 않는다 — 같은 셸에서 Claude Code 를 띄우면 그 값을 먼저 써서, 키가 만료된 뒤 401 이 난다.

📋

```text
  FAIL   1) 턴별 effort       400 per-turn-control 이 Bedrock 까지 가지 않음
  FAIL   2) 판정 (비스트리밍) 판정 없음 (safeguards 또는 판정이 중간에 사라짐)
  FAIL   3) 판정 (스트리밍)   판정 없음 (safeguards 또는 판정이 중간에 사라짐)
결과: 패치 미적용 (이전 동작)
```

## 3. 이미지 태그 올리기

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 13-bump-image-tags.sh dev
```

📋 `<- change` 는 **gateway-proxy 한 줄**(→ `1.0.84-safeguards`)만 나와야 한다. 왼쪽(current) 값은 설치마다 다르다. 다른 서비스에도 `<- change` 가 붙으면 멈춘다 — 이 EC2 가 다른 업데이트를 덜 적용한 상태라 [8-U](8-U-update.md) 또는 [8-D](8-D-upstream-sync.md) 대상이다.

```bash
bash 13-bump-image-tags.sh dev --apply
```

📋 helm 렌더 확인 뒤 values 가 바뀌고 백업 경로가 나온다.

## 4. gateway-proxy 이미지 빌드

```bash
cd ~/awsome-ai-gateway
./deployment/scripts/rebuild-image.sh gateway-proxy dev
```

📋 `tag : 1.0.84-safeguards` 로 빌드해 ECR 에 올린다. region 오류가 나면 `export AWS_DEFAULT_REGION=us-west-2` 뒤 다시 실행한다.

## 5. 배포

```bash
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```

📋 helm REVISION 이 하나 오르고, gateway-proxy 파드만 새로 뜬다.

## 6. 확인 (패치 후)

```bash
kubectl -n llm-gateway get deploy gateway-proxy \
  -o jsonpath='{.spec.template.spec.containers[0].image}'; echo
```

📋 `…/gateway-proxy:1.0.84-safeguards`

```bash
cd ~/awsome-ai-gateway/deployment/scripts
read -rs GATEWAY_KEY && export GATEWAY_KEY
python3 check-safeguards-passthrough.py https://<게이트웨이 주소>
```

📋

```text
  PASS   1) 턴별 effort       200
  PASS   2) 판정 (비스트리밍) toolu_… -> not_flagged
  PASS   3) 판정 (스트리밍)   toolu_… -> not_flagged
결과: 패치 적용됨
```

`SKIP` 은 모델이 도구를 부르지 않은 경우다. 한 번 더 실행한다. 끝나면 `unset GATEWAY_KEY`.

### Claude Code 로 확인 (관리자 PC)

확인 스크립트는 게이트웨이만 본다. 실제 Claude Code 가 서버 판정을 쓰는지는 게이트웨이에 연결된 관리자 PC 에서 아래 세 단계로 본다. **배포 뒤 새로 연 세션**이어야 한다 — 배포 전에 연 세션은 끝날 때까지 PC 쪽 분류기를 쓴다.

**① 서버 판정이 켜져 있는지** — 새 터미널에서 `claude` 를 열고 `/status` 를 친다.

📋 `Auto mode server: Enabled`. 확인했으면 `/exit`.

- `Disabled` 면 관리형 설정이나 환경 변수에 `CLAUDE_CODE_AUTO_MODE_SERVER=0` 이 있다. 서버 판정을 시도하지 않는 상태라 아래 확인이 거짓으로 통과하므로, 그 값이 없는 PC 에서 한다.
- 모델은 Sonnet 5.5 또는 Opus 5.5 다(Auto mode 는 Haiku 4.5 를 지원하지 않는다).

**② 판정이 필요한 명령을 Auto mode 로 한 번 실행**

```bash
P="Run: curl -s -o /dev/null -w '%{http_code}' https://example.com"
claude -p "$P" --permission-mode auto --debug-file /tmp/cc.log
```

📋 답에 `200` 이 들어 있다(확인 창 없이 실행됨). `curl` 은 네트워크 명령이라 판정 대상이다. `ls` 같은 읽기 명령은 판정 없이 실행돼 확인이 되지 않는다.

**③ 로그 확인**

```bash
grep -E "server-classifier|classifier_request_started" /tmp/cc.log
```

📋 **아무것도 나오지 않으면 통과**다. 패치 전에는 아래 같은 줄이 나온다.

```text
[server-classifier] the platform gave no classification ... (server_no_result) ...
[server-classifier] Bash: no server verdict for this call (server_no_result) ...
[Stall] classifier_request_started reqId=... tool=Bash ...
```

- `server_no_result` = 응답에 판정이 없어 PC 쪽 분류기로 바꿨다는 뜻이다.
- `classifier_request_started` = 판정용 요청(약 4.7만 토큰)을 따로 보냈다는 뜻이다. 과금 안내가 뜨는 원인이 이것이다.

로그에는 프롬프트와 경로가 남으므로 확인 뒤 지운다: `rm /tmp/cc.log`

**Windows (PowerShell)** — ②③ 을 이렇게 한다.

```powershell
$P = "Run: curl -s -o /dev/null -w '%{http_code}' https://example.com"
claude -p $P --permission-mode auto --debug-file $env:TEMP\cc.log
sls $env:TEMP\cc.log "server-classifier|classifier_request_started"
```

**과금 안내는 판정 근거로 쓰지 않는다** — 대화형 세션에서는 "classifier 요청 과금" 안내가 안 떠야 맞다. 하지만 한 번 확인한 안내는 24시간 동안 원래 안 뜨고, `-p` 실행에서는 아예 안 뜬다.

## 7. 되돌리기

**끄기 스위치 (재빌드 없이 이전 동작)** — values 의 `gatewayProxy:` → `env:` 아래에 한 줄을 넣고 배포한다.

```yaml
    BEDROCK_FORWARD_BETAS: ""
```

```bash
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```

📋 2절의 확인 스크립트가 다시 `패치 미적용` 이 된다. 다시 켜려면 그 줄을 지우고 배포한다.

**이미지까지 되돌리기** — [8-U](8-U-update.md) 4단계와 같다.

```bash
helm -n llm-gateway history llm-gateway
helm -n llm-gateway rollback llm-gateway <직전 REVISION>
```

## 8. 참고

- **넘기는 beta 가 두 개인 이유**: Claude Code 가 보내는 11개를 Bedrock 에 직접 시험했다. `prompt-caching-scope` 는 Bedrock 이 모르는 이름이라 섞이면 요청 전체가 400 이다. 나머지는 넘기지 않아도 동작이 같거나 이번 기능과 무관하다.
- **Mantle 경로는 대상이 아니다**: beta 를 넘기지 않는다. 그 경로의 Claude Code 는 이전처럼 PC 쪽 분류기를 쓴다.
- **새 beta 가 생기면**: Claude Code 를 올린 뒤 2절 확인 스크립트를 다시 돌린다. 게이트웨이 로그의 `upstream_compat.beta_dropped` 에 처음 보는 이름이 나오면, Bedrock 에 직접 시험한 뒤 `BEDROCK_FORWARD_BETAS` 에 넣을지 정한다. 그 기능이 웹 검색·사용량 집계와 얽히면 코드 변경이 필요하다.
- **보안**: `safeguards` 의 판단 재료에는 작업 경로, git 상태, 사용자 이름이 들어 있고, 이제 Bedrock 까지(호출 로그를 켰다면 그 로그에도) 간다. 프롬프트와 같은 경계다.
- **비용**: 응답 사용량(usage)에 판정 몫 토큰은 보이지 않았다(실측). 따로 나가던 분류기 요청이 사라지는 만큼 줄어든다.
- **prod**: dev 확인 뒤 이 문서에 prod 절을 덧붙인다.
