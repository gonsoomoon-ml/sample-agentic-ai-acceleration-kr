# 8-M. 모델 추가와 교체

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-M**

> 📒 `US-02` 의 일부 — [README.md 「최신 업데이트」](../README.md#2-최신-업데이트). **Cowork 와 무관하며 Claude Code 만 쓰는 배포에도 해당**한다.

`02-add-opus5-model.sh` 는 이름과 달리 **범용**이다. `config.env` 의 `MODEL_ALIAS`·`MODEL_PROVIDER_ID` 를 바꾸면 어떤 모델이든 등록한다. 시드에는 **Opus 4.8 까지만** 들어 있으므로(마이그레이션 `0006`), 그 이후 모델은 전부 이 절차를 거친다.

---

## 실행

▶ **배포 EC2**

**0) 저장소를 최신으로 맞춘다** — 스크립트가 갱신됐을 수 있다.

```bash
cd ~/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak
git fetch origin && git reset --hard origin/us/deploy-fixes
cp ~/values.bak $V
```

⚠️ 이 브랜치는 리베이스되므로 `git pull` 은 통하지 않는다. `values-*.yaml` 백업·복구를 빠뜨리면 다음 `helm upgrade` 에서 **ALB 허용목록이 통째로 빠진다.** 원격 확인과 `cmp` 복구 검증을 포함한 전체 절차는 [README.md 「3. 적용하기」](../README.md#3-적용하기-배포-ec2-에서).

**1) `config.env` 를 준비한다** — 스크립트는 전부 이 파일을 읽는다. 없으면 다음 단계가 *"config.env not found"* 로 멈춘다.

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
[ -f config.env ] || cp config.env.example config.env
```

이 파일은 `.gitignore` 대상이라 **저장소를 갱신해도 지워지지 않는다.** 한 번 만들어 두면 그대로 남는다.

**고칠 것은 보통 `AWS_ACCOUNT_ID` 한 줄뿐이다.** 모델 기본값이 Opus 5.5 로 맞춰져 있다 —
별칭 `claude-opus-5-5` · 모델 ID `us.anthropic.claude-opus-5-5` · 단가 5종(`pricing.tsv` 와 같은 값).

▶ **실행** · 배포 EC2 — 먼저 지금 값을 본다

```bash
grep -E '^(AWS_ACCOUNT_ID|MODEL_)' config.env
```

`AWS_ACCOUNT_ID` 가 비어 있거나 모델 값이 위와 다를 때만 고친다. 맞으면 그대로 다음 단계로 간다.

```bash
vi config.env
```

예외는 둘뿐이다.

- **다른 모델을 등록한다면** — `MODEL_ALIAS` · `MODEL_PROVIDER_ID`(INFERENCE_PROFILE 전용이면
  `us.` 접두사 필수 ↓ⓒ) · `MODEL_DISPLAY_NAME`·`MODEL_DESCRIPTION` · 단가 5종 + `MODEL_PRICE_ASOF`(↓ⓑ)
- **Opus 5 를 `us.` 프로파일로 바꾼다면** — 마이그레이션 `0027` 이 넣은 별칭은 `global.` 이다.
  값은 `config.env.example` 주석 블록에 있고 `02` 가 REMAP 으로 UPDATE 한다.

**2) 사전 점검** — 읽기 전용. `team_allowed_models has 0 rows` 를 확인한다(행이 있으면 아래 ⓐ).

```bash
bash 00-preflight-check.sh
```


**3) dry-run → 적용**

```bash
bash 02-add-opus5-model.sh
```
```bash
bash 02-add-opus5-model.sh --apply
```

**4) 5분 기다린다** — `model:list` 캐시 TTL 이 300초다. 파드를 재시작해도 소용없다(캐시가 외부 ElastiCache).

**5) 검증**

```bash
bash 04-verify.sh
```

**안 쓰는 모델 내리기** — 새 모델을 넣었으면 그 자리를 대신하는 옛 모델은 선택 목록에서 치운다.
예를 들어 Opus 5.5 를 등록했으면 **Opus 4.8 은 더 둘 이유가 없다** — 같은 급인데 더 비싸고
(입력 $5 vs $4 / 1M), 최근 사용도 없다. 내리기 전에 그 모델을 최근에 쓴 사람이 있는지 관리
화면의 사용량에서 한 번 본다.

방법은 `INACTIVE` 로 바꾸는 것 하나다. **삭제는 안 된다** — `model_aliases` 를 참조하는 FK 가
여럿이고 `ON DELETE` 가 없어 실패한다. `INACTIVE` 로 바꿔도 과거 사용량·비용 기록은 그대로
남는다.

▶ **실행** · 배포 EC2 — ① 주소와 토큰을 준비한다 — 주소는 Ingress 에서 읽고, 토큰은 개발용
형식(`dev.<본문>.sig`)으로 만든다. 마지막 줄이 `HTTP 200` 이면 준비 끝이다.

```bash
H=$(kubectl -n llm-gateway get ingress llm-gateway-admin-api -o jsonpath='{.spec.rules[0].host}'); ADMIN_API="https://$H"; echo "$ADMIN_API"
```

```bash
P=$(printf '{"email":"admin@dev.local","role":"ADMIN"}' | base64 -w0 | tr '+/' '-_' | tr -d '='); ADMIN_JWT="dev.$P.sig"
```

```bash
curl -s -o /dev/null -w 'HTTP %{http_code}\n' "$ADMIN_API/admin/models" -H "Authorization: Bearer $ADMIN_JWT"
```

`401` 이 나오면 개발용 로그인이 꺼진 배포다(US-12 적용 후). 관리 화면에 Cognito 로 로그인한 뒤
브라우저 개발자도구에서 `admin_jwt` 쿠키 값을 `ADMIN_JWT` 에 넣는다.

▶ **실행** · 배포 EC2 — ② 지금 상태를 본다

```bash
curl -s "$ADMIN_API/admin/models" -H "Authorization: Bearer $ADMIN_JWT" | grep -o 'claude-opus-4-8[^}]*'
```

▶ **실행** · 배포 EC2 — ③ 내린다

```bash
curl -sX PATCH "$ADMIN_API/admin/models/claude-opus-4-8/status" \
  -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' \
  -d '{"active":false}'
```

응답의 `status` 가 `INACTIVE` 면 끝이다. 되살리려면 같은 호출에 `{"active":true}` 를 보낸다.
클라이언트 목록에 반영되는 것은 `model:list` 캐시 5분 뒤다. Cowork 는 PC 마다 모델 목록에서도
빼야 선택기에서 사라진다(「등록 뒤」의 클라이언트 문단과 같은 명령).

---

## 등록 뒤

**관리자 토큰** — 이 절의 `curl` 들이 쓴다. 주소는 배포마다 달라 문서에 적어 두지 않고 클러스터
에서 뽑는다. 각 단계는 `echo` 로 값을 보고 넘어간다.

▶ **실행** · 배포 EC2 — ① admin-api 주소를 Ingress 에서 읽는다

```bash
H=$(kubectl -n llm-gateway get ingress llm-gateway-admin-api -o jsonpath='{.spec.rules[0].host}'); echo "$H"
```

뒤 명령들이 쓰는 `$ADMIN_API` 로 조립한다.

```bash
ADMIN_API="https://$H"; echo "$ADMIN_API"
```

▶ **실행** · 배포 EC2 — ② 로그인 방식을 확인한다(개발용 로그인이 켜져 있나)

```bash
kubectl -n llm-gateway get deploy llm-gateway-admin-api -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="DEV_LOGIN_ENABLED")].value}{"\n"}'
```

▶ **실행** · 배포 EC2 — ③ `true` 면 토큰을 만든다 — 관리자 신원을 JSON 으로 적어 base64url 로
인코딩한다. 서명이 없는 개발용 형식이라 dev 에서만 통하고, US-12 로 끄면 동작하지 않는다.

```bash
P=$(printf '{"email":"admin@dev.local","role":"ADMIN"}' | base64 -w0 | tr '+/' '-_' | tr -d '='); echo "$P"
```

`dev.<본문>.sig` 가 admin-api 가 받는 개발용 토큰 형식이다.

```bash
ADMIN_JWT="dev.$P.sig"; echo "${ADMIN_JWT:0:24}..."
```

②가 `false` 거나 비어 있으면 위 두 줄 대신, 관리 화면에 Cognito 로 로그인한 뒤 브라우저
개발자도구에서 `admin_jwt` 쿠키 값을 복사해 `ADMIN_JWT` 에 넣는다.

▶ **실행** · 배포 EC2 — ④ 토큰이 통하는지 본다 — 모델 목록을 한 번 불러 응답 코드만 확인한다

```bash
curl -s -o /dev/null -w 'HTTP %{http_code}\n' "$ADMIN_API/admin/models" -H "Authorization: Bearer $ADMIN_JWT"
```

`HTTP 200` 이면 이 절의 나머지 `curl` 이 전부 동작한다. `401` 이면 토큰 문제, 응답이 없으면
admin-api 접근 문제(허용 목록·VPN)다.

**클라이언트에서 보이게 하기** — 서버에 등록해도 여기까지 해야 사용자가 고를 수 있다.

- **Claude Code** — 게이트웨이가 내려주는 모델 목록을 따른다. `model:list` 캐시 5분이 지나면
  보인다. 설치할 때 `--available-models` 로 **모델 목록을 PC 에 고정**한 곳만 `gateway-cli setup`
  을 다시 돌려야 한다.
- **Cowork** — PC 마다 모델 목록(`inferenceModels`)을 다시 써야 한다. 레지스트리를 직접 고치지
  말고 설치기 CLI 를 쓴다. `--model` 이 기본 모델이고, 그 값은 목록 안에 있어야 한다.

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell (정책이 HKLM 이면 관리자 권한이 필요하다)

```powershell
$m = "claude-sonnet-5,claude-opus-5-5,claude-haiku-4-5-20251001"
gateway-cli-cowork setup --model claude-sonnet-5 --available-models $m
```

▶ **실행** · 사용자 PC — 🔵 일반 PowerShell (앱을 띄워야 하므로 사용자 세션에서)

```powershell
gateway-cli-cowork relaunch
```

확인은 `gateway-cli-cowork verify` 와 Cowork 의 모델 선택기다. GPO 로 관리하는 조직은 같은
`inferenceModels` 값(JSON 배열 문자열)을 정책으로 배포하고 앱을 재시작한다.

**폴백 체인** — 장애(5xx)나 예산 초과 때 다른 모델로 내려가게 하려면 `budget.downgrade_policies`
에 행을 넣는다. gateway-proxy 는 기동 시 **활성 행 전부**(스코프 무관)를 읽어 `from → to` 전역
체인을 만든다. 같은 `from` 이 여러 개면 먼저 읽힌 행이 이긴다.

▶ **배포 EC2** — 관리자 JWT 로 호출한다. `<scope>` 는 `TEAM`·`USER` 등, `<scope_id>` 는 그 UUID.

```bash
curl -sX PUT "$ADMIN_API/admin/budgets/<scope>/<scope_id>/downgrade" \
  -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' \
  -d '{"enabled":true,"rules":[{"from_model_alias":"claude-opus-5-5","to_model_alias":"claude-sonnet-5","threshold_pct":100}]}'
```

행을 넣은 뒤 **gateway-proxy 를 재시작**해야 체인이 적재된다. 확인은 기동 로그의
`fallback_chain_loaded entries=N` 한 줄이다.

```bash
kubectl -n llm-gateway rollout restart deploy/llm-gateway-gateway-proxy
```

⚠️ web search 가 켜진 앱(`routing_profiles.web_search_enabled`)은 이 fork 에서 폴백 루프를
우회하는 결함이 있어, 그 조건에서는 체인이 걸리지 않는다. 검증은 웹서치를 끈 앱으로 한다.

**단가 검산** — 하루 뒤 Cost Explorer 에서 실제 청구 단가와 대조한다. 어긋나면 `config.env` 를
고쳐 `08-set-model-pricing.sh` 로 갱신한다.

---

## 알아둘 것

**ⓐ 🔴** `team_allowed_models` **에 행이 하나라도 있으면 새 모델이 400 을 뱉는다.** 행이 있는 순간 whitelist 모드로 뒤집혀 등록만으로는 못 쓴다. 해당 팀 행을 함께 넣어야 한다 — `bash 02-add-opus5-model.sh --team-id <uuid>`. `00-preflight-check.sh` 가 이걸 검사해 알려준다.

**ⓑ 단가를 빼먹으면 조용히 망가진다.** 가격 행이 없으면 `router_service.py:51-52` 가 0 으로 대체한다. **요청은 성공하고 비용만** `$0` **으로 쌓이며 예산이 우회된다** — 에러가 없어 발견이 늦다. 상세: [update-scripts/README.md 「왜 단가가 필수인가」](../update-scripts/README.md#왜-단가가-필수인가).

**단가 배수는 모델마다 다를 수 있다.** 기본은 5분 쓰기 ×1.25 · 1시간 쓰기 ×2 · 읽기 ×0.1 이지만
**Opus 5.5 는 읽기가 ×0.05** 다(Fable 5.1 은 ×0.025). 공식 가격표 각주를 확인하고 넣는다. 그리고
`us.` 같은 지리 엔드포인트는 글로벌 정가의 **1.1배**로 청구되므로 정가를 그대로 넣으면 10% 적게
기록된다.

단가는 **수동**이다. AWS Pricing API(`AmazonBedrock`)는 Claude 3 까지만 싣고 신모델은 공개 가격 페이지에도 없다. 그래서 값이 조용히 낡는다 — `MODEL_PRICE_ASOF` 가 그것을 드러내는 유일한 장치이니 반드시 갱신한다. 등록 뒤 단가를 청구와 맞추는 절차 = [8-R](8-R-pricing.md).

**ⓒ 모델 ID 는 리전에서 확인하고 넣는다.** Opus 5 처럼 `INFERENCE_PROFILE` 전용 모델은 `us.` 접두사가 필수다.

```bash
aws bedrock list-inference-profiles --region us-west-2 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId,'opus')]"
```

**ⓓ 계정에서 그 모델이 켜져 있어야 한다.** 안 켜져 있으면 403 — [install-guide.md §1-3](../install-guide.md#1-3-bedrock-모델-액세스-us-west-2--먼저-확인-대개-불필요).

**ⓔ IAM 은 Claude 계열이면 대개 손댈 필요가 없다.** `terraform.tfvars` 의 `bedrock_model_arns` 가 `inference-profile: us.anthropic.*` 와 `foundation-model: anthropic.claude-*` 를 와일드카드로 잡는다. **비-Claude 모델을 넣을 때만** ARN 을 추가하고 `terraform apply` 한다.

**ⓕ 클라이언트에서 보이게 하기** → 위 「등록 뒤」 절을 따른다.
