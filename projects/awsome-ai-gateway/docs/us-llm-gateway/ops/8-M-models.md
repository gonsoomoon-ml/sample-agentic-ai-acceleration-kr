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

무엇을 채울지는 경우에 따라 다르다.


| 상황                        | `vi config.env` 로 고칠 값                                                                                   |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **update-scripts 를 처음 쓴다** | `AWS_ACCOUNT_ID` **한 줄**. 나머지는 설치 기본값이라 그대로 둔다                                            |
| **Opus 5.5 를 등록한다**    | 별칭·모델 ID·단가 5종 **전부** — 값은 `config.env.example` 아래쪽 주석 블록에 있다(그대로 붙여넣는다). 같은 값이 `pricing.tsv` 에도 들어 있다 |
| **Opus 5 를 등록한다**        | 보통 **필요 없다** — 마이그레이션 `0027` 이 별칭을 이미 넣는다. 프로파일 ID 가 `global.` 이라 `us.` 지리 프로파일로 바꿀 때만 이 절차를 쓴다(`02` 가 REMAP 으로 UPDATE 한다). 단가는 US-11 이 맞춘다 |
| **다른 모델을 등록한다**       | `MODEL_ALIAS`(클라이언트가 요청할 이름) · `MODEL_PROVIDER_ID`(Bedrock 모델 ID, `INFERENCE_PROFILE` 전용이면 `us.` 접두사 필수 ↓ⓒ) · `MODEL_DISPLAY_NAME`·`MODEL_DESCRIPTION`(admin-ui 표시용) · 단가 5종 + `MODEL_PRICE_ASOF`(↓ⓑ) |


```bash
vi config.env
```

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

**되돌리기** — `INACTIVE` 로 바꾼다. `model_aliases` 를 참조하는 FK 가 여럿이고 `ON DELETE` 가 없어 **삭제는 실패한다.**

```bash
bash 99-rollback.sh --model
```

⚠️ `99-rollback.sh --model` 은 `config.env` 의 `MODEL_ALIAS` 를 대상으로 삼는다. 방금 새 모델을
등록한 상태에서 **옛 모델을 내리려고 그대로 돌리면 방금 넣은 모델이 내려간다.** 내릴 별칭으로
바꾼 뒤 실행한다.

---

## 등록 뒤

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

**ⓕ 클라이언트에서 보이게 하기**

- **Cowork** — `inferenceModels` 에 이름을 넣어야 모델 선택기에 나타난다([client-install.md](../client-install.md)). 넣지 않으면 등록해도 안 보인다.
- **Claude Code** — 게이트웨이가 내려주는 모델 목록을 따른다. 5분 캐시가 만료된 뒤 반영된다.
