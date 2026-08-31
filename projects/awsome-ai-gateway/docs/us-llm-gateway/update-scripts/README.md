# LLM Gateway 업데이트 스크립트 — Cowork 활성화 + 모델 추가

이미 운영 중인 LLM Gateway에 **Cowork(Claude Desktop 3P)를 연결**하고 **신규 모델을 등록**하는 도구 모음입니다. 「최신 업데이트」의 **`US-02`** 에 해당합니다 — [README.md](../README.md#2-최신-업데이트).

> 📒 **먼저 `status.sh` 를 돌리십시오.** 이 배포가 어느 업데이트까지 반영돼 있는지 한 화면으로 보여주고, 안 된 것만 알려줍니다. 구성을 변경하지 않습니다.
>
> ```bash
> bash status.sh
> ```
>
> ⚠️ **파일명의 숫자는 업데이트 세대가 아닙니다.** `00`~`09` 는 *한 배치 안의 실행 순서*이고, `US-NN` 은 *업데이트 세대*입니다 — 축이 달라 `US-03` 이 `09-update-admin-ui.sh` 인 식으로 어긋납니다. `status.sh` 에 숫자가 없는 것도 그래서입니다.

`deployment/scripts/install-eks.sh` 로 설치한 게이트웨이라면 `config.env` **에서 계정 ID 한 줄만** 고치면 됩니다. 나머지 값은 설치 기본값이거나 클러스터에서 자동으로 찾아냅니다.

---

## 무엇을 해결하나

Claude Code는 잘 되는데 Cowork만 안 되는 상태를 고칩니다. 원인은 셋입니다.

### ① Cowork 요청이 전부 실패합니다

게이트웨이는 클라이언트(Claude Code / Cowork)마다 **"이 요청을 어디로 보낼지"를 적어둔 설정 행**을 하나씩 갖고 있습니다. DB의 `model.routing_profiles` 테이블이고, Cowork용은 `client='cowork'` 행입니다.

설치할 때 자동으로 채워지는데, **Cowork 행에는 실습용 예시값이 들어갑니다.** 구체적으로 `account_role_arn` 컬럼이 존재하지 않는 AWS 계정 번호를 가리킵니다.

그래서 Cowork가 요청을 보내면 게이트웨이가 그 계정의 역할로 `sts:AssumeRole` 을 시도하다 실패합니다(보통 502로 보입니다). Claude Code 행(`client='claude-code'`)은 설치 과정에서 올바르게 채워지므로 영향이 없고, 그래서 **"Claude Code는 되는데 Cowork만 안 되는"** 모습이 됩니다.

### ② 모델을 새로 등록해도 Cowork에서는 못 씁니다

같은 Cowork 행의 `default_model` 컬럼이 **"모델 고정" 스위치**로 동작합니다. `backend='mantle'` 과 함께 값이 채워져 있으면, 게이트웨이는 사용자가 고른 모델을 **무시하고** 이 컬럼에 적힌 모델로 바꿔서 처리합니다.

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

`01-fix-cowork-routing.sh` 가 ①②를 (`routing_profiles` 의 `cowork` 행을 `claude-code` 행과 같은 모양으로 고칩니다), `03-create-cloudfront.sh` 가 ③을 해결합니다.

## 실행 위치와 준비물

**설치할 때 쓴 배포 작업용 EC2(Deployment EC2, 설치 가이드 §1-2)에서 실행합니다.** 랩톱이나 다른 머신에서는 동작하지 않습니다 — DB가 프라이빗 VPC 안에 있어 그 EC2를 거쳐야 하고, 클러스터 접근에 필요한 kubeconfig도 거기에 있습니다.

준비물은 따로 없습니다. `bootstrap-ec2.sh` 가 이미 `aws-cli` · `kubectl` · `helm` · `jq` · `psql` 을 설치해 두었고, 이 스크립트들이 쓰는 도구는 그게 전부입니다. 혹시 빠진 게 있으면 시작할 때 어느 도구인지 알려주고 중단합니다.

배포 EC2에서 설치 때 클론한 저장소를 갱신합니다. 이 브랜치는 upstream 위로 **리베이스**되므로 커밋 해시가 바뀝니다. 그래서 `git pull` 대신 **원격에 그대로 맞추는 방식**을 씁니다 — 갓 클론한 경우든 오래된 사본이든 동일하게 동작합니다.

먼저 수정된 파일이 있는지 봅니다.

```bash
cd ~/awsome-ai-gateway && git status -s
```

`values-*.yaml` 이 나오는 것이 정상입니다 — 설치 때 채운 **계정별 실값**입니다. 아래가 그 값을 보존하면서 갱신합니다.

```bash
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cd ~/awsome-ai-gateway && cp $V ~/values.bak
git fetch origin && git reset --hard origin/us/deploy-fixes
cp ~/values.bak $V
cd docs/us-llm-gateway/update-scripts
```

그 외 파일이 `git status` 에 나왔다면 같은 방식으로 복사해 두십시오.

⚠️ **지우고 재클론하지 마십시오.** `terraform.tfvars`·`.terraform/` 는 gitignore 대상이라 `git status` 에 안 보이면서 `rm -rf` 로는 사라지고 재클론으로 복구되지 않습니다. `reset --hard` 는 추적 파일만 되돌리므로 이들은 남습니다.

⚠️ `values-*.yaml` 을 잃으면 다음 `helm upgrade` 가 placeholder 로 나가고 **ALB IP 허용목록이 통째로 빠집니다**(`imageRegistry`·`aws.region`·`allowedStsRegions`·`masterPasswordRemoteKey`·ingress `inbound-cidrs`). 이 파일은 계정 값 때문에 커밋할 수 없어 배포 EC2 한 대에만 남습니다 — 안전한 곳에 사본을 두시길 권합니다.

설정 파일을 만들고 계정 ID만 채웁니다.

```bash
cp config.env.example config.env
vi config.env            # AWS_ACCOUNT_ID 만 채우면 됩니다
```

`config.env` 와 실행 중 생기는 `snapshots/` 는 `.gitignore` 대상이라 위 `reset --hard` 로도 **지워지지 않고**, 다음 갱신을 방해하지도 않습니다. 여러분의 계정 값이 저장소로 올라가지도 않습니다.

**여기까지가 준비입니다.** 실행은 다음 절의 순서를 그대로 따르십시오 — 맨 처음 `00-preflight-check.sh` 가 읽기 전용으로 설정이 어떻게 해석됐는지, 자동 탐지가 무엇을 찾았는지 보여줍니다. 값이 틀렸으면 거기서 멈추면 됩니다.

## 어느 스크립트가 무엇을 바꾸나


| 스크립트                        | 바꾸는 것                                                  | 위험도                        |
| --------------------------- | ------------------------------------------------------ | -------------------------- |
| `00-preflight-check.sh`     | **없음** — 상태 조회·판정·스냅샷                                  | 없음                         |
| `01-fix-cowork-routing.sh`  | `model.routing_profiles` 의 **행 1개**                    | 낮음. Claude Code 경로 무관      |
| `02-add-opus5-model.sh`     | `model_aliases` + `model_pricings` 에 **행 추가** (기존 미변경) | 낮음                         |
| `03-create-cloudfront.sh`   | **CloudFront 배포 생성** + gateway Ingress 어노테이션           | ⚠️ 데이터플레인 접근 통제가 바뀝니다 (아래) |
| `04-verify.sh`              | **없음** — 검증                                            | 없음                         |
| `05-allow-client-ip.sh`     | Ingress `inbound-cidrs` 어노테이션                          | 낮음                         |
| `06-persist-annotations.sh` | **helm values 파일** (`05-allow-client-ip.sh` 의 IP 허용목록을 영구화)               | 낮음. helm 을 돌리지 않음          |
| `07-client-values.sh`       | **없음** — 직원에게 줄 env 4줄 출력                              | 없음                         |
| `08-setup-notification-ses-irsa.sh` | **IAM 역할 + values 어노테이션** (`notification-worker` SES 용 IRSA) | 낮음. install-eks.sh 는 별도 실행     |
| `09-update-admin-ui.sh`     | admin-ui **이미지 빌드→ECR→롤아웃** + values 태그                | 낮음. 대시보드만. helm 을 돌리지 않음   |
| `https-env.sh` (source)     | **없음** — US-06 용 값 12개 export (도메인만 입력)                | 없음                         |
| `10-switch-https.sh`        | **helm values 파일** Ingress 블록 → 방식 B(https·인증서·host)     | 낮음. helm 을 돌리지 않음(install-eks.sh 가) |
| `11-route53-cname.sh`       | Route 53 hosted zone 에 **CNAME 3개**                       | 낮음. DNS 만                   |
| `99-rollback.sh`            | 위 변경 되돌리기                                              | —                          |
| `_lib.sh`                   | 공통 함수 (직접 실행하지 않음)                                     | —                          |
| `config.env`                | 설정값 (부작용 없음)                                           | —                          |




### 공통 규약

- **인자 없이 실행 = dry-run.** 현재값과 바꿀 내용만 출력하고 아무것도 건드리지 않습니다.
- `--apply` **/** `--create` **를 주어야 실제 변경**되며, 그때 `snapshots/` 에 원복 근거를 남깁니다.
- 변경 전 `yes` 입력을 받습니다. (⚠️ 한글 입력기면 `ㅛ` 가 들어가 취소됩니다 — 영문으로)
- 시작 시 **계정을 확인하고 불일치하면 즉시 중단**합니다. `config.env` 의 `AWS_ACCOUNT_ID` 가 그 기준입니다.

---



## 실행 순서

⚠️ **파일 번호는 실행 순서가 아니라 변경 ID 입니다.** `04-verify.sh` 는 번호와 달리 **맨 마지막**에 돌립니다 — `05-allow-client-ip.sh`·`06-persist-annotations.sh` 가 나중에 추가됐고 둘 다 검증보다 앞에 와야 하기 때문입니다. 기준은 아래 목록입니다.

```bash
bash 00-preflight-check.sh                 # 항상 먼저. 읽기 전용 (2~3분)

bash 01-fix-cowork-routing.sh              # 확인
bash 01-fix-cowork-routing.sh --apply

bash 02-add-opus5-model.sh                 # 확인 (단가는 config.env 에 기입돼 있음)
bash 02-add-opus5-model.sh --apply

bash 03-create-cloudfront.sh               # 설정 확인
bash 03-create-cloudfront.sh --create
bash 03-create-cloudfront.sh --allow-cloudfront
#   안 하면 502. 단 데이터플레인 접근 통제가
#   IP+VK -> VK 단독으로 바뀝니다 (「참고」 절)

bash 05-allow-client-ip.sh --add <Cowork 를 돌릴 PC 의 공인IP>/32 --apply

bash 06-persist-annotations.sh             # 확인
bash 06-persist-annotations.sh --apply     # 05 의 IP 허용목록을 values 에 반영

#   ↑ 여기서 5분 대기 (캐시), CloudFront 전파는 5~15분

bash 04-verify.sh --base-url https://<cf-domain> --vk <VK>
#                                                    ↑ 「VK 얻기」 참고

bash 07-client-values.sh                   # 직원에게 전달할 env 4줄
```

⏱ **DB 를 건드리는 스크립트는 조회 중에 화면이 멈춥니다 — 정상입니다.** DB 가 프라이빗 VPC 안이라 조회할 때마다 클러스터에 임시 psql 파드를 띄우는데, Fargate 가 파드 하나를 스케줄하는 데 **1~2분**을 씁니다. 그동안 아무 출력도 없으니 hang 으로 오해하기 쉽습니다. dry-run 은 조회가 더 적습니다.


| 스크립트                       | DB 조회 | 대략             |
| -------------------------- | ----- | -------------- |
| `00-preflight-check.sh`    | 3회    | 3~5분           |
| `01-fix-cowork-routing.sh` | 최대 3회 | 2~5분           |
| `02-add-opus5-model.sh`    | 최대 5회 | 3~8분           |
| `04-verify.sh`             | 2회    | 2~4분 + 종단 curl |
| `03` · `05` · `06` · `07`  | 없음    | 수 초            |


`Ctrl+C` 로 끊지 마십시오 — `--apply` 중이라면 스냅샷만 남고 변경이 반쯤 들어갈 수 있습니다.

게이트웨이 쪽은 여기까지입니다. 이어지는 클라이언트(Cowork) 설치는 `docs/us-llm-gateway/cowork/manual/cowork-client-install-windows.md`.

---



## 검증

`04-verify.sh` 가 세 층을 확인합니다.


| 층   | 확인              | 왜              |
| --- | --------------- | -------------- |
| A   | DB 상태           | 설정이 들어갔는가      |
| B   | 종단 호출           | 실제로 통하는가       |
| C   | `usage_logs` 비용 | 통했는데 제대로 기록되는가 |


A만 보고 끝내면 조용한 실패를 놓칩니다. C에서 `$0` 이 나오면 가격 행이 잘못 들어간 것입니다.

B는 `anthropic-client-platform: desktop_app` **헤더로 Cowork를 흉내** 냅니다(`client_identifier.py:38-45`). Cowork 앱을 설치하기 전에 서버측을 확정할 수 있습니다.

실패 시 판별표를 함께 출력합니다:


| 증상                         | 원인                                          |
| -------------------------- | ------------------------------------------- |
| 404 `not_found_error`      | alias 미등록 / INACTIVE / **캐시 미만료(5분 더 대기)**  |
| 400 "does not have access" | `team_allowed_models` 화이트리스트                |
| 502 `provider_error`       | **간헐적이면** 「문제가 생기면 · 간헐적인 502 / 504」        |
| 502 / AssumeRole 오류        | `01-fix-cowork-routing.sh` 미적용 또는 라우팅 캐시 미만료                      |
| 403                        | VK 만료                                       |
| 504 (정확히 60초)              | 오리진이 60초 안에 응답 못 함 → 같은 절                   |
| CloudFront 502 (매번)        | `03-create-cloudfront.sh --allow-cloudfront` 누락 |


⚠️ 검증 시 `max_tokens` 를 작게 잡지 마십시오. 최신 모델은 `thinking` 블록을 먼저 냅니다 — 작으면 그것만으로 예산이 소진되어 `text` 가 빈 채로 돌아오고, "빈 응답"으로 오진하기 쉽습니다. 64 이상을 권합니다.

---



## 롤백

```bash
bash 99-rollback.sh --list          # 되돌릴 수 있는 항목
bash 99-rollback.sh --routing       # 01 → 시드 상태로
bash 99-rollback.sh --model         # 02 → INACTIVE 로
bash 99-rollback.sh --cloudfront    # 03 → 절차 출력 (전파 대기 때문에 수동)
bash 05-allow-client-ip.sh --remove <IP>/32 --apply    # 05 되돌리기
bash 09-update-admin-ui.sh --rollback                  # 09 → 이전 이미지로
```

---



## `helm upgrade` 전에 어노테이션을 영구화하십시오

`helm upgrade` 는 values 로부터 Ingress 를 다시 만들고, AWS Load Balancer Controller 가 그 Ingress 로부터 SG 를 다시 만듭니다. **values 에 없는 규칙은 그때 사라집니다.** 클러스터에서만 `kubectl annotate` 로 넣은 값이 대표적입니다.

특히 위험한 것이 gateway Ingress 의 **`security-group-prefix-lists`** 입니다. CloudFront 가 오리진에 닿는 통로라, 이게 없어지면 **CloudFront 경유 요청이 전부 502** 가 되어 데이터플레인이 멈춥니다.

**차트는 이 값을 안전하게 담을 수 있습니다.** 어노테이션이 두 겹입니다.

| values 위치 | 적용 대상 |
|---|---|
| `ingress.annotations` | 세 Ingress **공통** |
| `ingress.gateway.annotations` | gateway **전용** |
| `ingress.adminUi.annotations` | admin-ui **전용** |
| `ingress.adminApi.annotations` | admin-api **전용** |

우선순위는 **전용 > 템플릿 기본값 > 공통** 입니다. prefix-list 처럼 data-plane 에만 필요한 규칙은 반드시 `ingress.gateway.annotations` 에 넣으십시오. 공통 맵에 넣으면 admin-api·admin-ui 까지 임의의 CloudFront 배포에 열려 IP 제한이 무력화됩니다.

`06-persist-annotations.sh` 가 두 키를 각각 맞는 자리에 씁니다 — CIDR 은 공통, prefix-list 는 gateway 전용. 전용 맵이 없는 옛 차트에서는 prefix-list 를 쓰지 않고 경고만 합니다(위험한 곳에 쓰느니 안 쓰는 편이 낫습니다).

**정상 순서**

```bash
bash 06-persist-annotations.sh              # dry-run — 무엇이 빠져 있는지
bash 06-persist-annotations.sh --apply      # 두 키를 values 에 영구화
./deployment/scripts/install-eks.sh <env>   # 이제 안전 (아래 ⚠️ 참고)
bash 04-verify.sh                           # 종단 확인
```

> ⚠️ **`helm upgrade` 를 직접 치지 마십시오.** values 파일에는 `<RDS_PROXY_ENDPOINT>` 같은 placeholder 가 남아 있고, 실값은 `install-eks.sh` 가 `terraform output` 에서 읽어 `--set` 으로 주입합니다. values 만 넘긴 업그레이드는 DB·Redis·OIDC 를 placeholder 로 덮어씁니다. 상세는 아래 「`helm upgrade` 는 `install-eks.sh` 로만」.

> `09-update-admin-ui.sh` 는 helm 대신 `kubectl set image` 를 씁니다. 차트를 아직 못 고친 설치(고객사 기존 배포 등)에서 이미지만 급히 바꿔야 할 때의 우회로입니다. **차트가 고쳐졌고 06 을 돌렸다면 `install-eks.sh` 가 정공법입니다** — helm 이 기억하는 상태와 클러스터가 갈라지지 않습니다.

원복 값은 문서나 기억이 아니라 **적용 시점에 실제 DB에서 읽어** `snapshots/` **에 남긴 SQL** 입니다.

모델은 **DELETE하지 않고 INACTIVE** 로 내립니다 — `model_aliases` 를 참조하는 FK가 여럿인데 `ON DELETE` 절이 없습니다.

롤백도 반영에 5분 걸립니다.

---



## 각 단계 보충

실행 순서의 특정 줄이 왜 필요한지에 대한 설명입니다.

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

가격 행이 없으면 `router_service.py:51-52` 가 조용히 0으로 대체해 **비용이 $0으로 기록**됩니다. 요청은 정상 성공하므로 그동안 예산이 통째로 우회됩니다. 그래서 `02-add-opus5-model.sh` 는 단가 없이 진행하지 않습니다.

`config.env` 에 Opus 5 값이 이미 들어 있으니 **그대로 두면 됩니다** (1K 토큰당 USD):


| input   | output  | cache 5m  | cache 1h | cache read |
| ------- | ------- | --------- | -------- | ---------- |
| `0.005` | `0.025` | `0.00625` | `0.010`  | `0.0005`   |


정가 $5·$25 per 1M + 캐시 표준 배수(×1.25 / ×2 / ×0.1). 다른 모델을 등록할 때만 고치십시오(`--input` 등 인자가 우선).

⚠️ Anthropic 1st-party 정가입니다. Bedrock 은 AWS 가 별도 책정하지만 Opus 계열은 일치해 왔고, 벤더 마이그레이션(`0004`·`0006`)도 Bedrock 대상 Opus 4.6·4.8 에 같은 세트를 씁니다. 비용 정확도가 중요하면 청구서와 대조하십시오.

### `05-allow-client-ip.sh` 가 필요한 이유

CloudFront를 세우면 추론은 어디서든 되지만, **VK를 받아오는 경로는 별개**입니다. `gateway-cli login` 과 `api-key-helper` 는 admin-api를 직접 치고 admin-api는 IP로 잠겨 있습니다.

넣을 IP 는 **Cowork(또는 Claude Code)를 실제로 돌릴 PC 의 공인 IP** 입니다 — 게이트웨이 서버나 배포 EC2 의 IP 가 아닙니다. 사용자가 여럿이면 각자의 IP(또는 사무실 대역)를 넣습니다. 목록에 없으면 **로그인은 성공하는데 VK 발급만 타임아웃**나서 원인을 찾기 어렵습니다.

기본 대상은 `admin-api` 하나입니다. 데이터플레인은 CloudFront 를 거치므로 `gateway` 까지 열 이유가 없습니다.

⚠️ 사내망은 목적지 리전마다 출구 IP가 다를 수 있습니다. `checkip.amazonaws.com` 결과를 믿지 말고 **대상 리전의 호스트로 SSH해서** 재십시오:

```bash
ssh -i <key> ubuntu@<대상 리전 EC2> 'echo $SSH_CLIENT'   # 작은따옴표 필수
```

---



### VK 얻기

`04-verify.sh` 는 실제로 요청을 보내므로 Virtual Key 가 필요합니다. 게이트웨이에 이미 온보딩된 머신(배포 EC2 포함)이라면 한 줄입니다.

```bash
api-key-helper 2>/dev/null | grep -m1 '^vk-'
```

`vk-` 로 시작하는 한 줄이 나오면 그게 VK 입니다. 아직 로그인한 적이 없다면 먼저 온보딩합니다(설치 가이드 §6-0 과 같은 절차).

```bash
export OIDC_ISSUER_URL="<운영자가 준 값>"
export OIDC_CLIENT_ID="<운영자가 준 값>"
export ADMIN_API_URL="<운영자가 준 값>"
cd ~/awsome-ai-gateway && bash scripts/onboard-macos-linux.sh
```

> `--setup-claude-code` 를 빼면 **로그인만** 하고 끝납니다. Claude Code 설정은 건드리지 않습니다.

⚠️ **로그인은 브라우저 PKCE 이고 콜백이** `localhost:8090` **입니다.** 헤드리스 서버에서 돌린다면 브라우저가 그 포트에 닿아야 하므로 터널을 먼저 여십시오. Cognito 콜백 화이트리스트는 `8090`·`8091`·`8092` 3개뿐이라 그중에서 골라야 합니다.

```bash
ssh -L 8090:localhost:8090 -i <key> ubuntu@<EC2 공인IP>
```

⚠️ **VK 발급은 IP 제한을 받습니다.** 로그인(Cognito)은 공개지만 발급(admin-api)은 `inbound-cidrs` 안에서만 됩니다. 그 머신의 공인 IP 가 목록에 없으면 **로그인은 성공하는데 발급이 타임아웃**납니다 — `05-allow-client-ip.sh` 로 추가하십시오.

---



## 문제가 생기면



### 간헐적인 502 / 504 — CloudFront 를 의심하지 마십시오

`03-create-cloudfront.sh` 를 제대로 돌렸는데도 요청이 **어떤 때는 되고 어떤 때는 안 되는** 증상이 있습니다. 이 프로젝트에서 실제로 겪었고, 원인을 CloudFront·신규 모델에서 한참 찾다가 아니라는 것을 확인했습니다.

원인은 게이트웨이 파드 안입니다. `bedrock-runtime` 으로의 keep-alive 연결이 botocore 커넥션 풀에 남는데, **NAT 의 idle timeout(350초)이 그 흐름을 조용히 버립니다**(Bedrock VPC 엔드포인트가 없으면 NAT 경유). 파드가 오래 떠 있을수록 죽은 연결이 쌓이고, 그걸 집은 요청이 두 형태로 실패합니다.

```
ReadTimeoutError       → 300초 매달림 → CloudFront 가 60초에 포기하고 504
ConnectionClosedError  → 즉시 502   {"type":"provider_error"}
```

새 연결을 집은 요청은 1~2초에 정상 응답합니다. 그래서 **재시도하면 되는 것처럼 보입니다.**

> ⚠️ Bedrock VPC 엔드포인트를 켜도(`ops/8-N-vpc-endpoint.md`) **이 증상은 그대로입니다.** 인터페이스 엔드포인트도 NLB 기반이라 idle timeout 이 동일한 350초입니다. 경로가 사설로 바뀔 뿐이니, 엔드포인트가 있다는 이유로 이 진단을 배제하지 마십시오.

**① 판별 — 로그에서 예외 종류를 봅니다**

```bash
kubectl logs deploy/llm-gateway-gateway-proxy -n llm-gateway --tail=500 |
  grep -o '\(ReadTimeout\|ConnectionClosed\)Error[^"]*' | tail -3
```

둘 중 하나가 보이면 이 문제입니다. (release·namespace 는 `config.env` 의 `HELM_RELEASE`·`K8S_NAMESPACE`.)

**② CloudFront 가 아님을 확인 — ALB 로 직접 쏩니다**

배포 EC2 의 IP 는 gateway ALB 허용목록에 있으므로 직접 호출할 수 있습니다. ALB 주소는 `00-preflight-check.sh` 가 `gateway ALB DNS` 로 출력합니다.

```bash
GW=http://<00 이 출력한 gateway ALB DNS>
VK=$(api-key-helper 2>/dev/null | grep -m1 '^vk-')
```

```bash
for i in 1 2 3; do
  curl -sS -o /dev/null -w "$i: %{http_code}  %{time_total}s\n" --max-time 90 \
    -X POST "$GW/v1/messages" \
    -H "Authorization: Bearer $VK" \
    -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    -d '{"model":"claude-opus-5","max_tokens":64,
         "messages":[{"role":"user","content":"hi"}]}'
done
```

3회 중 일부만 실패하면 이 문제입니다. **CloudFront 를 거치지 않았는데도 실패하기 때문입니다.** 전부 실패한다면 다른 원인이니 판별표를 보십시오.

**③ 임시 조치 — 파드 재시작**

풀이 비워집니다. Ingress 어노테이션·DB·CloudFront 는 **건드리지 않습니다**(그것들이 사라지는 건 `helm upgrade` 입니다).

```bash
kubectl rollout restart deploy/llm-gateway-gateway-proxy -n llm-gateway
```

```bash
kubectl rollout status deploy/llm-gateway-gateway-proxy -n llm-gateway --timeout=5m
```

기존 파드가 계속 서비스하므로 중단은 없습니다. 다만 **증상을 미루는 것일 뿐이라 유휴가 쌓이면 재발합니다.**

**근본 해결**(미적용): `gateway-proxy/src/app/providers/bedrock_adapter.py` 의 boto `Config` 에서 `read_timeout` 을 300 → 30 으로 낮추고 재시도를 붙이면, 300초 매달림이 빠른 재시도로 바뀌어 새 연결을 잡습니다. 이미지 재빌드 + `install-eks.sh` 가 필요합니다.

> `tcp_keepalive=True` 만으로는 부족합니다 — Linux 기본 `tcp_keepalive_time` 이 7200초라 350초 안에 keepalive 가 나가지 않습니다. **Bedrock VPC 엔드포인트도 해결책이 아닙니다** — 인터페이스 엔드포인트는 NLB 기반이고 NLB idle timeout 도 350초로 같습니다(보안·비용 이유로는 여전히 넣을 값어치가 있습니다).



### ⚠️ 보안그룹을 직접 고치지 마십시오

ALB는 **AWS Load Balancer Controller** 가 관리하고, SG 규칙을 Ingress 어노테이션에 맞춰 **계속 재조정**합니다. `aws ec2 authorize-security-group-ingress` 로 직접 넣은 규칙은 잠깐 살아 있다가 조용히 사라지고, 그때부터 502가 납니다.

이 프로젝트에서 실제로 겪었습니다 — 수동으로 넣은 IP가 사라지고 지웠던 IP가 되살아났습니다.

정본은 어노테이션입니다:

```
alb.ingress.kubernetes.io/inbound-cidrs               ← 허용 IP 목록
alb.ingress.kubernetes.io/security-group-prefix-lists ← CloudFront 등 관리형 대역
```

`03-create-cloudfront.sh` 와 `05-allow-client-ip.sh` 는 어노테이션을 바꾸고, **컨트롤러가 실제로 SG에 반영하는지 90초간 확인**한 뒤 결과를 알려줍니다.

**영속성 — 어노테이션은 두 곳에 있을 수 있습니다.**

```
클러스터의 Ingress    ← kubectl annotate.  03·05 가 여기에 씁니다
values 파일           ← helm 이 읽는 정본
      │
      └─ helm upgrade 는 values 로 클러스터를 덮어씁니다.
         values 에 없는 어노테이션은 그때 사라집니다.
```

| 어노테이션 | 넣는 스크립트 | values 에 기록 | `helm upgrade` 후 |
|---|---|---|---|
| `inbound-cidrs` | `05-allow-client-ip.sh` | `06-persist-annotations.sh` → 세 Ingress 가 같으면 `ingress.annotations`, 다르면 각자 `ingress.<이름>.annotations` | 유지 |
| `security-group-prefix-lists` | `03-create-cloudfront.sh` | `06-persist-annotations.sh` → `ingress.gateway.annotations` | 유지 |

`06` 은 **합집합을 쓰지 않습니다.** 합집합이면 admin-api 하나 때문에 추가한 IP 가 gateway 까지 열어버립니다. 공통분만 공유 맵에 넣고, 다른 Ingress 는 자기 목록을 전용 맵에 그대로 씁니다 — values 가 클러스터 현재 상태와 정확히 같아집니다. (전용 맵이 없는 옛 차트에서는 합집합밖에 표현할 수 없어, 그 사실과 넓어지는 범위를 출력하고 진행합니다.)

⚠️ **단, `06` 을 먼저 돌린 경우에만 유지됩니다.** 안 돌린 상태로 업그레이드하면 prefix-list 가 사라져 CloudFront 경유 요청이 전부 502 입니다. 그렇게 됐다면 아래로 되살립니다(배포는 그대로 두고 어노테이션만 다시 걸어 수 초).

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 03-create-cloudfront.sh --allow-cloudfront
```

**왜 prefix-list 를 공통 맵에 넣으면 안 되는가.** values 의 `ingress.annotations` 는 세 Ingress 에 **모두** 적용됩니다. 거기 넣으면 gateway 뿐 아니라 admin-api·admin-ui 에도 붙고, 그러면 누구든 자기 CloudFront 배포의 오리진을 그 ALB 로 지정해 IP 제한을 우회할 수 있습니다. gateway 는 그 상태를 승인하고 쓰는 것이지만(「참고 · `03 --allow-cloudfront` 의 보안 성격 변경」), 컨트롤 플레인까지 그럴 이유는 없습니다. 그래서 `06` 은 이 값을 **gateway 전용 맵**에만 씁니다.

> **옛 차트를 쓰는 설치라면** 이야기가 다릅니다. 전용 맵(`ingress.gateway.annotations`)이 없는 차트에서는 prefix-list 를 values 에 담을 방법이 아예 없어, `06` 이 쓰지 않고 경고만 합니다. 그 경우 **업그레이드할 때마다** 위 `03 --allow-cloudfront` 를 손으로 다시 걸어야 합니다. `06` 이 실행할 때마다 어느 쪽인지 알려줍니다.

### ⚠️ `helm upgrade` 는 `install-eks.sh` 로만

values 파일에는 `<RDS_PROXY_ENDPOINT>` 같은 **placeholder 가 남아 있습니다.** 실제 DB·Redis 엔드포인트, IRSA role ARN, Cognito issuer 는 `install-eks.sh` 가 terraform output 에서 읽어 `--set` 으로 주입하고, helm 이 그 값을 release 에 기록합니다.

따라서 values 파일만 넘긴 업그레이드는 **그 실값을 placeholder 로 덮어씁니다.**

```bash
helm upgrade llm-gateway ./charts/llm-gateway \
  -f values-eks-fargate-<env>.yaml        # ← DB·Redis·OIDC 가 전부 죽습니다
```

업그레이드는 항상 이 경로로만 하십시오. 같은 `--set` 을 다시 조립해 줍니다.

```bash
./deployment/scripts/install-eks.sh <env>
```

현재 release 가 실값을 들고 있는지 확인:

```bash
helm get values llm-gateway -n <namespace> | grep -E 'host|issuerUrl'
```

placeholder 가 아니라 실제 주소가 보여야 정상입니다.

### 이 기계에서 직접 커밋한 적이 있다면

저장소 갱신의 `reset --hard` 는 **원격에 없는 로컬 커밋을 지웁니다.** 이 기계에서 직접 커밋한 기억이 있다면 먼저 확인하십시오 — 제목을 대조해 원격에도 있는지 봅니다.

```bash
git fetch origin
git log --format=%s HEAD --not origin/us/deploy-fixes | sort > /tmp/a
git log --format=%s origin/us/deploy-fixes | sort > /tmp/b
comm -23 /tmp/a /tmp/b      # 빈 출력 = 전부 원격에 있음 = 버려도 됨
```

리베이스로 해시만 바뀐 커밋은 제목이 원격에도 있으므로 빈 출력이 나옵니다. 뭔가 출력되면 그 커밋은 이 기계에만 있는 작업이니 지우지 말고 따로 판단하십시오.

---



## 참고



### `03 --allow-cloudfront` 의 보안 성격 변경

CloudFront는 오리진에 공인 IP로 접근하므로 ALB가 그 대역을 받아줘야 합니다. 그 결과 데이터플레인의 접근 통제가 바뀝니다.


|                      | 변경 전    | 변경 후                 |
| -------------------- | ------- | -------------------- |
| gateway ALB 도달       | 허용된 IP만 | CloudFront 경유 시 누구나  |
| 실질 접근 통제             | IP + VK | **VK 단독**            |
| admin-api / admin-ui | IP 제한   | **IP 제한 유지 (변경 없음)** |




### 직접 확인·디버깅할 때 쓸 이름


| 이 문서의 표현             | 실제 이름                                                           |
| -------------------- | --------------------------------------------------------------- |
| 요청을 어디로 보낼지 적어둔 설정 행 | `model.routing_profiles` 테이블, `client` 컬럼이 키                    |
| 존재하지 않는 계정을 가리키는 값   | `routing_profiles.account_role_arn`                             |
| 그 계정에 접속 시도          | `sts:AssumeRole` (실패 시 502)                                     |
| 모델 고정 스위치            | `routing_profiles.default_model` (+ `backend='mantle'` 일 때만 발동) |
| 등록된 모델 목록            | `model.model_aliases` (`status='ACTIVE'` 인 것만 사용 가능)            |
| 모델 단가                | `model.model_pricings`                                          |
| 팀별 모델 허용목록           | `model.team_allowed_models` (행 0개 = 전체 허용)                      |


현재 상태는 `00-preflight-check.sh` 가 이 테이블들을 그대로 조회해 보여줍니다.

근거 — 코드에서 확인하려면

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

`claude-code` 행은 `backend='invoke'`, `default_model=NULL` 이라 이 규칙이 발동하지 않습니다. `01-fix-cowork-routing.sh` 는 Cowork 행을 이 모양으로 맞춥니다.

---

