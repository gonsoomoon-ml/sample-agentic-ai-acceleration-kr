# LLM Gateway 업데이트 스크립트 — Cowork 활성화 + 모델 추가

이미 운영 중인 LLM Gateway에 **Cowork(Claude Desktop 3P)를 연결**하고 **신규 모델을 등록**하는 도구 모음입니다.

`deployment/scripts/install-eks.sh` 로 설치한 게이트웨이라면 **`config.env` 에서 계정 ID 한 줄만** 고치면 됩니다. 나머지 값은 설치 기본값이거나 클러스터에서 자동으로 찾아냅니다.

---

## 무엇을 해결하나

Claude Code는 잘 되는데 Cowork만 안 되는 상태를 고칩니다. 원인은 셋입니다.

### ① Cowork 요청이 전부 실패합니다

게이트웨이는 클라이언트(Claude Code / Cowork)마다 **"이 요청을 어디로 보낼지"를 적어둔 설정 행**을 하나씩 갖고 있습니다. DB의 **`model.routing_profiles`** 테이블이고, Cowork용은 **`client='cowork'`** 행입니다.

설치할 때 자동으로 채워지는데, **Cowork 행에는 실습용 예시값이 들어갑니다.** 구체적으로 **`account_role_arn`** 컬럼이 존재하지 않는 AWS 계정 번호를 가리킵니다.

그래서 Cowork가 요청을 보내면 게이트웨이가 그 계정의 역할로 **`sts:AssumeRole`** 을 시도하다 실패합니다(보통 502로 보입니다). Claude Code 행(`client='claude-code'`)은 설치 과정에서 올바르게 채워지므로 영향이 없고, 그래서 **"Claude Code는 되는데 Cowork만 안 되는"** 모습이 됩니다.

### ② 모델을 새로 등록해도 Cowork에서는 못 씁니다

같은 Cowork 행의 **`default_model`** 컬럼이 **"모델 고정" 스위치**로 동작합니다. `backend='mantle'` 과 함께 값이 채워져 있으면, 게이트웨이는 사용자가 고른 모델을 **무시하고** 이 컬럼에 적힌 모델로 바꿔서 처리합니다.

```
Cowork 앱에서 "Claude Opus 5" 선택
        ↓
게이트웨이가 선택을 버림 → routing_profiles.default_model 값으로 교체
        ↓
그 값(cowork-opus)은 실제로 등록돼 있지 않음(INACTIVE) → 실패
```

이름이 `default_model`(기본값)이라 "사용자가 안 고르면 쓰는 값"으로 읽히지만, 실제로는 **덮어쓰기(override)** 입니다.

**모델 등록과 Cowork 활성화가 별개 작업이 아니라는 뜻**입니다. ①을 고치기 전에는 어떤 모델을 `model.model_aliases` 에 등록해도 Cowork에서 선택할 수 없습니다.

### ③ Cowork는 `https://` 주소를 요구합니다

Cowork는 게이트웨이 주소(`inferenceGatewayBaseUrl`)가 `https://` 로 시작해야 연결합니다. 게이트웨이 ALB가 HTTP만 열려 있고 ACM 인증서·퍼블릭 호스팅영역이 없다면, 도메인을 새로 사지 않고 https를 얻는 방법은 CloudFront를 앞에 세우는 것입니다.

---

`01` 이 ①②를 (`routing_profiles` 의 `cowork` 행을 `claude-code` 행과 같은 모양으로 고칩니다), `03` 이 ③을 해결합니다.

### 직접 확인·디버깅할 때 쓸 이름

| 이 문서의 표현 | 실제 이름 |
|---|---|
| 요청을 어디로 보낼지 적어둔 설정 행 | `model.routing_profiles` 테이블, `client` 컬럼이 키 |
| 존재하지 않는 계정을 가리키는 값 | `routing_profiles.account_role_arn` |
| 그 계정에 접속 시도 | `sts:AssumeRole` (실패 시 502) |
| 모델 고정 스위치 | `routing_profiles.default_model` (+ `backend='mantle'` 일 때만 발동) |
| 등록된 모델 목록 | `model.model_aliases` (`status='ACTIVE'` 인 것만 사용 가능) |
| 모델 단가 | `model.model_pricings` |
| 팀별 모델 허용목록 | `model.team_allowed_models` (행 0개 = 전체 허용) |

현재 상태는 `00-preflight-check.sh` 가 이 테이블들을 그대로 조회해 보여줍니다.

<details>
<summary>근거 — 코드에서 확인하려면</summary>

`gateway-proxy/src/app/routers/messages.py:118`

```python
# 주석: "a 'mantle' profile WITH a default_model forces that model
#        (cowork → cowork-opus), ignoring the requested alias."
if profile is not None and profile.backend == "mantle" and profile.default_model:
    cfg = await router_service.resolve_mantle_model(redis, db, profile.default_model)
```

`model.routing_profiles` 의 `cowork` 행이 `backend='mantle'` 이고 `default_model` 이 채워져 있으면, 요청 본문의 모델명을 버리고 `default_model` 로 강제합니다. 이름과 달리 기본값이 아니라 **override** 입니다.

시드가 넣는 값(마이그레이션 `0009_add_mantle_routing_data.py`):

```
client=cowork  backend=mantle  default_model=cowork-opus
account_role_arn=arn:aws:iam::<존재하지 않는 계정>:role/...
```

`claude-code` 행은 `backend='invoke'`, `default_model=NULL` 이라 이 규칙이 발동하지 않습니다. `01` 은 Cowork 행을 이 모양으로 맞춥니다.

</details>

---

## 실행 위치와 준비물

**설치할 때 쓴 배포 작업용 EC2(Deployment EC2, 설치 가이드 §1-2)에서 실행합니다.** 랩톱이나 다른 머신에서는 동작하지 않습니다 — DB가 프라이빗 VPC 안에 있어 그 EC2를 거쳐야 하고, 클러스터 접근에 필요한 kubeconfig도 거기에 있습니다.

준비물은 따로 없습니다. `bootstrap-ec2.sh` 가 이미 `aws-cli` · `kubectl` · `helm` · `jq` · `psql` 을 설치해 두었고, 이 스크립트들이 쓰는 도구는 그게 전부입니다. 혹시 빠진 게 있으면 시작할 때 어느 도구인지 알려주고 중단합니다.

배포 EC2에서 설치 때 클론한 저장소를 최신으로 당긴 뒤 이 디렉터리로 이동합니다.

```bash
cd ~/awsome-ai-gateway && git pull
cd projects/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
```

> 클론 위치가 다르면 그 경로에서 `git pull` 하십시오. 브랜치는 설치 때와 같은 `us/deploy-fixes` 입니다.

설정 파일을 만들고 계정 ID만 채웁니다.

```bash
cp config.env.example config.env
vi config.env            # AWS_ACCOUNT_ID 만 채우면 됩니다
bash 00-preflight-check.sh
```

`config.env` 와 실행 중 생기는 `snapshots/` 는 `.gitignore` 에 있어 다음 `git pull` 을 방해하지 않습니다. 여러분의 계정 값이 저장소로 올라가지도 않습니다.

`00` 은 읽기 전용입니다. 설정이 어떻게 해석됐는지, 자동 탐지가 무엇을 찾았는지 먼저 보여주므로 **여기서 값이 맞는지 확인**하고 진행하십시오.

> 배포 EC2는 인스턴스 역할로 AWS에 접근하므로 `AWS_PROFILE` 을 따로 설정할 필요가 없습니다. 다른 자격증명이 잡혀 있으면 계정 가드가 걸러냅니다.

---

## 어느 스크립트가 무엇을 바꾸나

| 스크립트 | 바꾸는 것 | 위험도 |
|---|---|---|
| `00-preflight-check.sh` | **없음** — 상태 조회·판정·스냅샷 | 없음 |
| `01-fix-cowork-routing.sh` | `model.routing_profiles` 의 **행 1개** | 낮음. Claude Code 경로 무관 |
| `02-add-opus5-model.sh` | `model_aliases` + `model_pricings` 에 **행 추가** (기존 미변경) | 낮음 |
| `03-create-cloudfront.sh` | **CloudFront 배포 생성** + gateway Ingress 어노테이션 | ⚠️ 데이터플레인 접근 통제가 바뀝니다 (아래) |
| `04-verify.sh` | **없음** — 검증 | 없음 |
| `05-allow-client-ip.sh` | Ingress `inbound-cidrs` 어노테이션 | 낮음 |
| `99-rollback.sh` | 위 변경 되돌리기 | — |
| `_lib.sh` | 공통 함수 (직접 실행하지 않음) | — |
| `config.env` | 설정값 (부작용 없음) | — |

### 공통 규약

- **인자 없이 실행 = dry-run.** 현재값과 바꿀 내용만 출력하고 아무것도 건드리지 않습니다.
- **`--apply` / `--create` 를 주어야 실제 변경**되며, 그때 `snapshots/` 에 원복 근거를 남깁니다.
- 변경 전 `yes` 입력을 받습니다. (⚠️ 한글 입력기면 `ㅛ` 가 들어가 취소됩니다 — 영문으로)
- 시작 시 **계정을 확인하고 불일치하면 즉시 중단**합니다. `config.env` 의 `AWS_ACCOUNT_ID` 가 그 기준입니다.

---

## 실행 순서

```bash
bash 00-preflight-check.sh                 # 항상 먼저. 읽기 전용

bash 01-fix-cowork-routing.sh              # 확인
bash 01-fix-cowork-routing.sh --apply

bash 02-add-opus5-model.sh                 # 확인 (단가는 config.env 에 기입돼 있음)
bash 02-add-opus5-model.sh --apply

bash 03-create-cloudfront.sh               # 설정 확인
bash 03-create-cloudfront.sh --create
bash 03-create-cloudfront.sh --allow-cloudfront   # 안 하면 502

bash 05-allow-client-ip.sh --add <클라이언트IP>/32 --apply   # VK 발급 경로

#   ↑ 여기서 5분 대기 (캐시), CloudFront 전파는 5~15분

bash 04-verify.sh --base-url https://<cf-domain> --vk <VK>
```

---

## 알아야 할 것

### 왜 5분을 기다려야 하나

관련 캐시가 전부 TTL 300초입니다.

```
auth_service.py:20            VK_CACHE_TTL         = 300
routing_profile_loader.py:16  ROUTING_CACHE_TTL    = 300
router_service.py:25          MODEL_CACHE_TTL      = 300
router_service.py:26          MODEL_LIST_CACHE_TTL = 300
```

⚠️ **파드 재시작으로 앞당길 수 없습니다.** 캐시는 외부 ElastiCache라 `rollout restart` 는 아무 효과가 없습니다.

그 5분 동안 Claude Code는 `GET /v1/models/{id}` 를 404로 받고 **아예 호출을 시도하지 않습니다** — "등록이 안 됐다"고 오해하기 쉬운 지점입니다.

관련 없어 보이지만 함께 기억할 것: `budget_service.py:18` 의 `BUDGET_CONFIG_TTL = None`(무기한). 예산을 SQL로 바꾸면 **기다려도 반영되지 않습니다.**

### 왜 단가가 필수인가

가격 행이 없으면 `router_service.py:51-52` 가 예외 없이 0으로 대체하고 `cost_recorder.py:24-39` 가 그대로 곱해 **비용을 $0으로 기록**합니다. 요청은 정상 성공하므로 눈치채기 어렵고, 그동안 예산이 통째로 우회됩니다.

그래서 `02` 는 단가 없이 진행하지 않습니다. **`config.env` 에는 Opus 5 값이 이미 채워져 있으므로 그대로 두면 됩니다** — 다른 모델을 등록하거나 값을 바꿔야 할 때만 수정하십시오(`--input` 등 인자로 주면 그쪽이 우선).

| 항목 | 1K 토큰당 USD | 근거 |
|---|---|---|
| input | `0.005000` | 공표 정가 $5 / 1M |
| output | `0.025000` | 공표 정가 $25 / 1M |
| cache write 5m | `0.006250` | input × 1.25 |
| cache write 1h | `0.010000` | input × 2 |
| cache read | `0.000500` | input × 0.1 |

⚠️ 위 값은 **Anthropic 1st-party API 정가**입니다. Amazon Bedrock 요금은 AWS가 별도로 책정합니다. 실제로 Opus 계열은 두 값이 일치해 왔고 — 이 코드베이스의 벤더 마이그레이션도 Bedrock 대상으로 Opus 4.6·4.7·4.8 에 **정확히 같은 세트**를 심어 두었습니다(`0003_rename_cache_5m.py`, `0004_add_opus_4_6.py`, `0006_add_opus_4_8.py`) — 비용 정확도가 중요하다면 실제 청구서와 한 번 대조하십시오.

**자동 조회는 불가능합니다.** AWS Pricing API(`AmazonBedrock`)는 Claude 3까지만 담고 있고, 신규 모델은 공개 요금 페이지에도 올라오지 않는 경우가 있습니다. 즉 이 값은 **조용히 낡습니다** — 확인한 날짜를 `MODEL_PRICE_ASOF` 에 적어두는 것이 유일한 감지 수단입니다. 기존 모델들의 현재 값은 `00-preflight-check.sh` 가 함께 보여줍니다.

### ⚠️ 보안그룹을 직접 고치지 마십시오

ALB는 **AWS Load Balancer Controller** 가 관리하고, SG 규칙을 Ingress 어노테이션에 맞춰 **계속 재조정**합니다. `aws ec2 authorize-security-group-ingress` 로 직접 넣은 규칙은 잠깐 살아 있다가 조용히 사라지고, 그때부터 502가 납니다.

이 프로젝트에서 실제로 겪었습니다 — 수동으로 넣은 IP가 사라지고 지웠던 IP가 되살아났습니다.

정본은 어노테이션입니다:

```
alb.ingress.kubernetes.io/inbound-cidrs               ← 허용 IP 목록
alb.ingress.kubernetes.io/security-group-prefix-lists ← CloudFront 등 관리형 대역
```

`03` 과 `05` 는 어노테이션을 바꾸고, **컨트롤러가 실제로 SG에 반영하는지 90초간 확인**한 뒤 결과를 알려줍니다.

**영속성**: 이 Ingress들은 helm release 소유입니다. `kubectl annotate` 로 넣은 값은 **다음 `helm upgrade` 때 사라집니다.** 영구 적용하려면 values의 `ingress.annotations` 를 함께 갱신해야 하며, 스크립트가 실행 시 해당 스니펫을 출력합니다.

### `03 --allow-cloudfront` 의 보안 성격 변경

CloudFront는 오리진에 공인 IP로 접근하므로 ALB가 그 대역을 받아줘야 합니다. 그 결과 데이터플레인의 접근 통제가 바뀝니다.

| | 변경 전 | 변경 후 |
|---|---|---|
| gateway ALB 도달 | 허용된 IP만 | CloudFront 경유 시 누구나 |
| 실질 접근 통제 | IP + VK | **VK 단독** |
| admin-api / admin-ui | IP 제한 | **IP 제한 유지 (변경 없음)** |

데이터플레인을 공개하고 VK 인증에 일임하는 것은 이미 운영 중인 배포들이 쓰는 모델이지만, **IP로 잠근 설계였다면 그 성격이 바뀌는 변경**이므로 승인 후 진행하십시오.

**하드닝 후보**: CloudFront 배포에 WAF IP set 부착, 또는 커스텀 오리진 헤더 + `alb.ingress.kubernetes.io/conditions.*` 로 오리진에서 검사.

### `team_allowed_models` 함정

행이 **0개면 전체 허용**, **1개라도 있으면 화이트리스트** 입니다. 후자면 신규 모델은 `400 invalid_request_error` 가 됩니다 (403이 아닌 것은 Claude Code가 `/login` 을 띄우지 않게 하려는 의도 — `fallback_loop.py:171-196`).

`00` 이 이걸 판정합니다. 화이트리스트면 `02 --team-id <uuid>` 로 팀 행도 함께 넣으십시오.

⚠️ `user_allowed_models` 는 **0행이 전체 허용이 아니라 "팀으로 위임"** 입니다(`auth_service.py:109-121`). 행이 있는 사용자는 팀 설정을 무시하므로 별도로 넣어야 합니다.

### `05` 가 필요한 이유

CloudFront를 세우면 추론은 어디서든 되지만, **VK를 받아오는 경로는 별개**입니다. `gateway-cli login` 과 `api-key-helper` 는 admin-api를 직접 치고 admin-api는 IP로 잠겨 있습니다. 클라이언트 PC의 공인 IP가 목록에 없으면 VK 발급이 아예 안 됩니다.

⚠️ 사내망은 목적지 리전마다 출구 IP가 다를 수 있습니다. `checkip.amazonaws.com` 결과를 믿지 말고 **대상 리전의 호스트로 SSH해서** 재십시오:

```bash
ssh -i <key> ubuntu@<대상 리전 EC2> 'echo $SSH_CLIENT'   # 작은따옴표 필수
```

---

## 검증

`04-verify.sh` 가 세 층을 확인합니다.

| 층 | 확인 | 왜 |
|---|---|---|
| A | DB 상태 | 설정이 들어갔는가 |
| B | 종단 호출 | 실제로 통하는가 |
| C | `usage_logs` 비용 | 통했는데 제대로 기록되는가 |

A만 보고 끝내면 조용한 실패를 놓칩니다. C에서 `$0` 이 나오면 가격 행이 잘못 들어간 것입니다.

B는 **`anthropic-client-platform: desktop_app` 헤더로 Cowork를 흉내** 냅니다(`client_identifier.py:38-45`). Cowork 앱을 설치하기 전에 서버측을 확정할 수 있습니다.

실패 시 판별표를 함께 출력합니다:

| 증상 | 원인 |
|---|---|
| 404 `not_found_error` | alias 미등록 / INACTIVE / **캐시 미만료(5분 더 대기)** |
| 400 "does not have access" | `team_allowed_models` 화이트리스트 |
| 502 / AssumeRole 오류 | `01` 미적용 또는 라우팅 캐시 미만료 |
| 403 | VK 만료 |
| CloudFront 502/504 | `03 --allow-cloudfront` 누락 |

⚠️ 검증 시 `max_tokens` 를 작게 잡지 마십시오. 최신 모델은 `thinking` 블록을 먼저 냅니다 — 작으면 그것만으로 예산이 소진되어 `text` 가 빈 채로 돌아오고, "빈 응답"으로 오진하기 쉽습니다. 64 이상을 권합니다.

---

## 롤백

```bash
bash 99-rollback.sh --list          # 되돌릴 수 있는 항목
bash 99-rollback.sh --routing       # 01 → 시드 상태로
bash 99-rollback.sh --model         # 02 → INACTIVE 로
bash 99-rollback.sh --cloudfront    # 03 → 절차 출력 (전파 대기 때문에 수동)
bash 05-allow-client-ip.sh --remove <IP>/32 --apply    # 05 되돌리기
```

원복 값은 문서나 기억이 아니라 **적용 시점에 실제 DB에서 읽어 `snapshots/` 에 남긴 SQL** 입니다.

모델은 **DELETE하지 않고 INACTIVE** 로 내립니다 — `model_aliases` 를 참조하는 FK가 여럿인데 `ON DELETE` 절이 없습니다.

롤백도 반영에 5분 걸립니다.

---

## 이 변경이 끝난 뒤 (범위 밖)

클라이언트(Windows/macOS) 설치는 별도 작업입니다.

`uv` → `gateway-cli` 설치 → `gateway-cli login`(OIDC) → credential helper 작성 → 관리형 설정(Windows는 `HKLM\SOFTWARE\Policies\Claude`, macOS는 `.mobileconfig`) → Claude Desktop 실행 → **Help → Troubleshooting → Copy Managed Configuration Report** 로 확인.

Cowork 설정에 들어갈 값:

```
inferenceProvider          = "gateway"
inferenceGatewayBaseUrl    = "https://<03 이 출력한 CloudFront 도메인>"
inferenceGatewayAuthScheme = "bearer"
inferenceCredentialHelper  = "<helper 절대경로>"
inferenceModels            = <config.env 의 MODEL_ALIAS 및 기존 ACTIVE alias 들>
```

⚠️ `gateway-cli` 는 **fork에서 설치**하십시오. upstream에는 벤더 버그 픽스가 빠져 있어, 설치는 성공한 것처럼 보이는데 인증이 조용히 개인 계정으로 새는 등의 문제가 있습니다.
