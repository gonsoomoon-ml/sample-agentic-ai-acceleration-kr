# §8-P. prod 스택 설치 — 고객사 최종형(https + admin internal) (US-08)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-P** · 업데이트 ID **US-08** · 등급 **선택**(prod 로 갈 때)
> · 소요: 도메인 준비 ~30분(위임·ACM) + 설치 ~2h(apply 30분 포함)
>
> **한 줄**: `environment=prod` 로 [install-guide.md](../install-guide.md) §1~§6 을 다시 돌되, **prod 에서만 터지는 함정**과
> "처음부터 https(US-06) · admin internal(US-07)" 로 세우는 **삽입점**을 적는다. 검증 계정 실측(2026-08-28~29).

## 0. 결론 · 전제

- **결론**: prod 는 dev 의 스위치가 아니라 **나란히 서는 별개 스택**이다. `environments/llm-gateway-prod/` ·
`install-eks.sh prod` · `/llm-gateway/prod/*` · `values-eks-fargate-prod.yaml` 로 §1~§6 을 다시 돈다.
단 prod 템플릿은 dev 의 rename 이 아니라 **낡은 별도 계보**라, 2 절의 must-fix 없이는 설치가 서지
않는다(elasticache output · 이미지 태그 · tfvars 변수 선언 — 현재 저장소에 반영됨).
- **이 문서의 구성**: prod 정본 그대로(Aurora 2× db.r7g.large · Valkey 3 shard × 3 노드 · HPA 3~30) ·
**처음부터** https(US-06) + admin ALB 2개 internal(US-07) + gateway public · Claude Code + Cowork(**Windows
설치기**, HKLM) + 서버측 web search.
- **전제 5개**: (0) **prod 는 dev 와 다른 AWS 계정**에 올린다(계정 = 환경 경계) (a) 도메인 — 부모 `awsome-ai-gw.click`(dev 계정 zone)의 서브도메인 `prod.awsome-ai-gw.click`
을 prod 계정 hosted zone 으로 **NS 위임**(2-0) (b) internal admin 에 닿을 경로 — 고객사는 S2S VPN, 테스트는 2-9 의 Client VPN 스탠드인.
VPN 이 아직 없으면 2-6 ④·2-9 를 생략(admin 도 dev 처럼 IP 허용목록 public)하고 개통 후 [8-I](8-I-admin-internal.md) 로 전환
(c) 배포 EC2(§1-2)  (d) **Cowork 용 Windows 테스트 머신**(2-10, metal) — dev 용과 별개.
- **비용**: dev 의 수 배(월 수천 달러대, 개략). 검증이 끝나면 4 로 teardown.



## 1. dev 와 무엇이 다른가

전체 구성은 [architecture.md 「전체 그림」](../architecture.md#전체-그림) 참고 — 이 문서가 세우는 형태가
그 문서의 「고객사 최종 아키텍처」(계정 분리 + https + admin ALB internal + VPN) 다.

```text
prod = separate AWS account: https (US-06) + admin ALB internal (US-07) + VPN
clients: Claude Code / Cowork on Mac or Windows - inference: Geo profile (us.anthropic.*)

                                ┌─ AWS Cloud: prod account, <region> ──────────────────────────┐
┌─ Parent DNS zone ──────────┐  │ ┌─ Region: <region> ───────────────────────────────────────┐ │
│ (dev or shared account)    │  │ │ ┌─ Route 53 prod.<domain> + ACM *.prod.<domain> ───────┐ │ │
│ NS delegation ->           ├──┼─┼▶│ CNAME x3 -> ALBs (gateway/admin/admin-api)           │ │ │
└────────────────────────────┘  │ │ └──────────────────────────────────────────────────────┘ │ │
                                │ │                                                          │ │
┌─ Employee PC (Mac/Win) ────┐  │ │ ┌─ Amazon Cognito (prod user pool) ────────────────────┐ │ │
│ Claude Code (gateway-cli)  ├──┼─┼▶│ Hosted UI, OIDC issuer/token                         │ │ │
│ (1) OIDC login             │  │ │ └──────────────────────────────────────────────────────┘ │ │
│                            │  │ │                                                          │ │
│                            │  │ │ ┌─ VPC <vpc_cidr> ─────────────────────────────────────┐ │ │
│                            │  │ │ │ ┌─ ALB gateway ──────┐      ┌─ EKS Fargate ────────┐ │ │ │
│ (3) LLM call, Bearer VK    ├──┼─┼─┼▶│ public https       ├─────▶│ gateway-proxy x3     │ │ │ │
│                            │  │ │ │ │ IP allowlist       │      │ admin-api x3 (VK)    │ │ │ │
│ VPN: S2S (customer) or     │  │ │ │ └────────────────────┘      │ admin-ui x2          │ │ │ │
│      Client VPN (test)     │  │ │ │ ┌─ private subnet ───┐      │ workers, scheduler   │ │ │ │
│ (V) tunnel to VPC          ├──┼─┼─┼▶│ VPN entry: VGW/TGW │      │ HPA, PDB             │ │ │ │
│                            │  │ │ │ │ or Client VPN ENI  │      │                      │ │ │ │
│ (2) get VK   [via VPN]     │  │ │ │ │ (SNAT -> VPC IP)   ├─(2)─▶│                      │ │ │ │
│ (A) admin UI [via VPN]     │  │ │ │ │ ALB admin-api INT  ├─(A)─▶│                      │ │ │ │
└────────────────────────────┘  │ │ │ │ ALB admin-ui  INT  │      └─┬─────┬─────────────┬┘ │ │ │
                                │ │ │ └────────────────────┘        │     │             │  │ │ │
                                │ │ │           ┌───────────────────┘     │             │  │ │ │
                                │ │ │           ▼                         ▼             │  │ │ │
                                │ │ │ ┌─ Aurora PostgreSQL ┐    ┌─ Valkey (Redis) ─┐    │  │ │ │
                                │ │ │ │ r7g x2, RDS Proxy  │    │ r7g 3 shard x 3  │    │  │ │ │
                                │ │ │ │ del. protection    │    │ cluster mode     │    │  │ │ │
                                │ │ │ │                    │    │                  │    │  │ │ │
                                │ │ │ └────────────────────┘    └──────────────────┘    │  │ │ │
                                │ │ │                                                   │  │ │ │
                                │ │ │                           (NAT x2 / VPC endpoint) │  │ │ │
                                │ │ └───────────────────────────────────────────────────┼──┘ │ │
                                │ │                                                     │    │ │
                                │ │                                                     │    │ │
                                │ │                                                     │    │ │
                                │ │                                                     │    │ │
                                │ └─────────────────────────────────────────────────────┼────┘ │
                                │                                                       ▼      │
                                │   ┌─ Egress ─────────────────────────────────────────────┐   │
                                │   │ Bedrock <region>: Geo profile us.anthropic.*         │   │
                                │   │ AgentCore Web Search: us-east-1 (managed connector)  │   │
                                │   └──────────────────────────────────────────────────────┘   │
                                └──────────────────────────────────────────────────────────────┘

(1) OIDC 로그인(Cognito)   (2) VK 발급 = admin-api(internal ALB, VPN 경유)
(3) 추론 = gateway ALB(공개, IP allowlist)   (A) admin UI = internal ALB(VPN 경유)
(V) VPN: 고객사 = S2S(VGW/TGW) / 검증 = Client VPN(ENI, SNAT -> VPC IP)
    admin ALB SG 허용 = VPC CIDR + VPN 클라이언트 CIDR.  부모 DNS zone -> NS 위임 -> prod zone
```

**사이징은** `environment = "prod"` **한 줄로 terraform 모듈이 자동으로 바꾼다** — tfvars 에 쓸 것이 없다(바꾸고 싶을 때만
`aurora_prod_instance_class`·`elasticache_prod_node_type`·`elasticache_prod_replicas_per_node_group`·`elasticache_prod_enable_custom_param_group`).


| 리소스                             | dev                | prod (자동)                                                                         |
| ------------------------------- | ------------------ | --------------------------------------------------------------------------------- |
| Aurora PostgreSQL               | Serverless v2 ×1   | **db.r7g.large ×2**(writer+reader) · backup 14일 · PI 62일 · **삭제 보호** · 최종 스냅샷     |
| ElastiCache Valkey              | cache.t4g.small ×1 | **cache.r7g.large · 3 shard × (1+2) = 9 노드** · cluster mode · 커스텀 파라미터그룹 · 스냅샷 7일 |
| NAT Gateway                     | 1개                 | **AZ 당 1개 = 2개**(EIP 2)                                                           |
| RDS Proxy · 로그 보존               | on · 7일            | on · 30일                                                                          |
| gateway-proxy 등 파드(helm values) | replica 1 · HPA 없음 | replica 3 · HPA 3~30 · PDB · 풀 사이즈 ↑                                              |


**dev 와 분리되는 것**: **계정부터 다르다**(prod 전용 계정 — 이 문서의 전제). 그 안에서도 tfstate key(`prod/`) · VPC(기본 `10.40/16` — 회사망과 겹치면 tfvars 로 변경, 2-2) · EKS `llm-gateway-prod` ·
Aurora · Valkey · Cognito 풀/도메인 · IRSA 역할 · Secrets `/llm-gateway/prod/*` · helm release(클러스터가 다르니 이름 같아도 무관).

## 2. 절차 — install-guide §1~§6 을 prod 로

> 아래 2-0 ~ 2-10 은 [install-guide.md](../install-guide.md) 의 설치 순서를 그대로 따른다. 각 제목 끝의
> **(install-guide §n-n)** 은 그 단계가 **무엇을 왜 하는지 읽는 곳**이다.
>
> 🔴 **실행 명령은 반드시 이 문서의 ▶ 실행 블록을 쓴다.** install-guide 의 명령 블록은 **dev 경로·dev 값**(`llm-gateway-dev`,
> `values-eks-fargate-dev.yaml`, `install-eks.sh dev`)이라 그대로 복사하면 dev 스택을 만들거나 건드린다.



### 2-0. 도메인 · ACM 인증서 준비 (절차는 [8-H](8-H-alb-https.md) §0 — 등록 또는 NS 위임)

> **한 줄**: 고객이 도메인이 AWS Router53 을 사용하지 않으면, 고객의 도메인 시스템에서 작업을 하시면 됩니다. 이럴 경우에 아래 내용은 단지 참고 하시면 됩니다.
>
> prod 가 쓸 이름 공간(`$DOMAIN`)의 **권위 zone 을 prod 계정에** 두고 와일드카드 ACM 을 ALB 리전에
> 만든다. 명령은 [8-H §0](8-H-alb-https.md#0-준비--도메인-등록--acm-인증서-저장소클러스터와-무관-먼저-해-둔다)
> 그대로 — 출처에 따라 (a) 신규 등록 = 0-1→0-2→0-3→0-4, (b) 회사 도메인의 서브도메인 위임 = 0-2-보충→0-3→0-4.
> 회사 zone 을 그대로 쓰는 (c) 는 CNAME 을 매번 부모 쪽이 넣어야 해 스크립트가 못 쓴다 → 비추천.
> 저장소·클러스터와 무관하니 terraform 전에 끝내 둔다(`ISSUED` 대기 5~30분).

**prod 에서만 더 지킬 것 2개**

1. **부모 zone 위치** — (b)일 때 부모는 중립 DNS/공유 계정이 정석. dev 계정이 부모면 prod DNS 가 dev zone 의
  NS 1건에 의존한다(테스트에선 허용, 고객사엔 부적합).
2. **호스트명** — 도메인이 환경을 담지 않으면(`corp-gw.click`) 스크립트 기본 `gateway-prod.$DOMAIN` 그대로.
  서브도메인이 환경을 담으면(`prod.corp.com`) 관례대로 `gateway.$DOMAIN`·`admin.$DOMAIN`·`admin-api.$DOMAIN`
   — 2-6 의 `10-switch-https.sh` 에 `--gateway-host/--admin-ui-host/--admin-api-host` 로 지정(기본값이면
   `gateway-prod.prod.…` 로 환경이 두 번). `11-route53-cname.sh`·`07-client-values.sh` 는 Ingress host 를 읽어 자동 추종.

📋 **이 배포(검증 계정) = (b)**: 부모 `awsome-ai-gw.click`(dev 계정 zone `<부모 zone ID>`) →
`DOMAIN=prod.awsome-ai-gw.click` · `CERT_REGION=us-west-2` · prod 계정에 zone 생성 → dev 계정에 NS 1건 UPSERT →
`*.prod.awsome-ai-gw.click` ACM → 호스트 `gateway.prod.…`·`admin.prod.…`·`admin-api.prod.…`.

**완료 기준**: `ZONE_ID` 확인((b)는 `dig +short NS "$DOMAIN"` 4개) + ACM `Certificate.Status` = `ISSUED`.
**메모**: `DOMAIN` · `CERT_ARN`(2-6 의 `https-env.sh` 가 `config.env` 에 저장).

### 2-1. 계정 준비 — 배포 EC2 · 도구 · tfstate 버킷 (install-guide §1 · §2 · §3-1)

> **한 줄**: 계정 단위 준비물이라 **prod 계정에서 install-guide §1 · §2 · §3-1 을 그대로** 한다(명령에 dev/prod 구분이 없다).


| 절    | 내용                                                      | prod 계정에서 |
| ---- | ------------------------------------------------------- | --------- |
| §1-2 | 배포 EC2 + `llm-gateway-deployer` 역할(AdministratorAccess) | 생성        |
| §1-3 | Bedrock 모델 액세스(계정 단위)                                   | 확인·신청     |
| §2   | EC2 도구 설치 · 새 셸                                         | 실행        |
| §3-1 | tfstate 버킷 · lock 표(`llm-gateway-tfstate-<acct>`)       | 생성        |


**완료 기준**: 배포 EC2 에서 `aws sts get-caller-identity` 가 prod 계정 · `terraform`/`helm`/`kubectl`/`docker`
있음 · tfstate 버킷(`llm-gateway-tfstate-<acct>`) 존재.

### 2-2. terraform.tfvars 만들기 — dev 것을 복사해 prod 로 (install-guide §3-2)

> **한 줄**: 전제 = **dev 가 이미 있다**(이 문서 전체의 전제). dev 의 `terraform.tfvars` 는 §3-3 apply 로 검증된
> **배포 고유값**(리전·ARN·역할·그룹)이므로 prod 디렉터리로 **복사한 뒤 아래 표의 값만 고친다**. 환경 차이
> (Aurora db.r7g.large ×2 · Valkey cache.r7g.large 3×3 · HA · 삭제 보호)는 `environment = "prod"` 하나로 prod
> `variables.tf` 가 켜므로 tfvars 에 사이징 줄은 없다.

**① 복사** — 기본 전제는 **dev 와 prod 가 다른 계정**. dev 배포 EC2 의 `environments/llm-gateway-dev/terraform.tfvars`
를 prod 배포 EC2 의 `environments/llm-gateway-prod/terraform.tfvars` 로 옮긴다. EC2 끼리 직접은 안 닿으니 **작업자 랩톱을
경유**한다:

▶ **실행** · 작업자 랩톱

```bash
D=awsome-ai-gateway/deployment/terraform/environments
scp -i <dev 키> ubuntu@<dev EC2 IP>:$D/llm-gateway-dev/terraform.tfvars /tmp/terraform.tfvars
scp -i <prod 키> /tmp/terraform.tfvars ubuntu@<prod EC2 IP>:$D/llm-gateway-prod/terraform.tfvars
```

**② 고칠 값** — dev 파일에서 바뀌는 줄은 아래 4개뿐이고, `environment` 를 빼면 전부 **계정 ID(12자리)만** dev 것에서
prod 것으로 바뀐다(예: dev `111111111111` → prod `999999999999`). 그 외 줄은 전부 그대로.


| 값                                            | dev 파일에 있는 것                                                                     | prod 에서 바꾼 것                                                                     |
| -------------------------------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `environment`                                | `"dev"`                                                                          | `"prod"`                                                                         |
| `eks_access_entries.developer.principal_arn` | `arn:aws:iam::111111111111:role/llm-gateway-deployer`                            | `arn:aws:iam::999999999999:role/llm-gateway-deployer`                            |
| Bedrock `inference-profile` ARN(계정 ID 든 1줄)  | `arn:aws:bedrock:us-west-2:111111111111:inference-profile/us.anthropic.claude-*` | `arn:aws:bedrock:us-west-2:999999999999:inference-profile/us.anthropic.claude-*` |
| `cognito_domain_suffix`                      | `"us-auth-111111111111"`                                                         | `"us-auth-999999999999"`                                                         |


**꼭 그대로 가져와야 하는 줄**: `eks_cluster_version = "1.34"` + `eks_addon_versions`(§3-2 의 example 에 들어 있어 dev 에 있음).
env `variables.tf` 기본값이 `1.30` 이라 이 줄이 빠지면 prod 클러스터가 1.30 으로 태어난다.

> 🔴 **네트워크 CIDR — apply 전에 반드시 결정한다 (apply 후엔 VPC 재생성 없이는 못 바꾼다)**
>
> prod VPC 는 기본 `10.40.0.0/16`(dev 는 `10.30.0.0/16`), 서브넷 4종도 `10.40.x` 기본값이다. prod 의 admin ALB 는
> **S2S VPN 을 타고 회사망에서 들어오므로, 이 대역이 회사망(온프렘·다른 VPC·다른 VPN 대역)과 한 칸이라도 겹치면 라우팅이
> 깨져 admin-ui·VK 발급이 통째로 불통**이 된다. 네트워크 담당자에게 **prod 용 /16(또는 /20 이상) 대역을 배정받아** 아래를
> tfvars 에 **명시**한다 — 기본값을 그대로 쓰는 것은 "회사망과 안 겹친다" 를 확인한 뒤에만.
>
> ```hcl
> vpc_cidr                 = "10.40.0.0/16"
> private_subnet_cidrs     = ["10.40.1.0/24",   "10.40.2.0/24"]      # azs 수만큼
> public_subnet_cidrs      = ["10.40.101.0/24", "10.40.102.0/24"]
> database_subnet_cidrs    = ["10.40.201.0/24", "10.40.202.0/24"]
> elasticache_subnet_cidrs = ["10.40.211.0/24", "10.40.212.0/24"]
> ```
>
> `azs` 도 같은 자리에서 조정. 2-9 의 Client VPN 클라이언트 대역(예: `10.99.0.0/22`)도 이 VPC 와 겹치지 않게 잡는다.

**③ 명령** — 파일을 가져다 둔 뒤:

▶ **실행** · prod 배포 EC2

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
sed -i 's/^environment = "dev"/environment = "prod"/' terraform.tfvars
sed -i 's/<dev 계정 ID>/<prod 계정 ID>/g' terraform.tfvars     # 표의 계정 ID 3곳 일괄 치환
```

예 — dev 계정 `111111111111`, prod 계정 `999999999999` 일 때:

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
sed -i 's/^environment = "dev"/environment = "prod"/' terraform.tfvars
sed -i 's/111111111111/999999999999/g' terraform.tfvars
grep -c 111111111111 terraform.tfvars    # 0 이어야 (dev 계정 ID 잔재 없음)
grep -c 999999999999 terraform.tfvars    # 3 이어야 (suffix · inference-profile ARN · principal_arn)
```

**④ 확인**

```bash
grep -n '^environment\|enable_chat\|^aws_region\|^azs\|cognito_domain_suffix\|principal_arn' terraform.tfvars
```

기대: `environment = "prod"` 만 다르고 나머지(`enable_chat_*`·`aws_region`·`azs`·…)는 dev 와 동일 ·  
`principal_arn = "arn:aws:iam::<prod acct>:role/llm-gateway-deployer"` · `cognito_domain_suffix` 채워짐.

**완료 기준**: **VPC CIDR 결정·기입** + ④ 기대값 + §3-2 의 검증 3개(`principal_arn` 이 `eks_access_entries.developer` 안 · `…:role/…` 형태 ·
Seoul 잔재 없음).

### 2-3. 인프라 생성 — terraform apply + elasticache output 확인 (install-guide §3-3)

> **한 줄**: 무엇을 왜 하는지는 [§3-3](../install-guide.md#3-3-terraform-apply-인프라--약-30분) — **명령은 아래 블록**(prod 경로).
> init 의 버킷·표는 prod 계정에서 §3-1 로 만든 것(state key 는 `prod/`).
>
> ⚠️ install-guide §3-3 블록을 그대로 복사하면 `llm-gateway-dev` 경로라 dev 스택 plan 이 나온다(실제로 겪음).
> plan 뒤 `pwd` 가 `…/llm-gateway-prod` 이고 `grep -c 'llm-gateway-dev' /tmp/plan.txt` = 0 인지 확인한다.

**① 먼저 tmux 로 들어간다** — apply 가 30분이라 SSH 가 끊겨도 작업이 살아 있게(빠져나오기 `Ctrl+b` `d`, 복귀 `tmux attach -t deploy`):

▶ **실행** · 배포 EC2

```bash
tmux attach -t deploy 2>/dev/null || tmux new -s deploy
```

**② tmux 안에서** init → plan:

▶ **실행** · 배포 EC2 — tmux 안

```bash
export AWS_DEFAULT_REGION=us-west-2
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
terraform init \
  -backend-config="bucket=llm-gateway-tfstate-$ACCOUNT" \
  -backend-config="dynamodb_table=llm-gateway-tflock" \
  -backend-config="region=us-west-2"
terraform plan -no-color > /tmp/plan.txt 2>&1; wc -l < /tmp/plan.txt
```

init 의 `"dynamodb_table" is deprecated` 경고와 `.terraform.lock.hcl` 변경 안내는 무시한다(lock 변경은 README §3 의 reset 이 되돌림).
줄 수는 수천 줄이 정상. **어느 디렉터리의 plan 인지**부터 확인:

```bash
pwd                                                        # …/llm-gateway-prod 여야
grep -m1 '^Plan:' /tmp/plan.txt                            # N to add, 0 to change, 0 to destroy
echo "dev 이름: $(grep -c 'llm-gateway-dev' /tmp/plan.txt) ← 0"
```

**plan 판정** — §3-3 ③ 의 5개 + **prod 4개**를 한 번에:

```bash
P=/tmp/plan.txt
printf '  %-16s %s\n'          "요약"         "$(grep '^Plan:' $P)"
printf '  %-16s %s ← 1 이상\n' "access entry" "$(grep -c 'aws_eks_access_entry' $P)"
printf '  %-16s %s ← 0\n'      "서울 잔재"    "$(grep -c 'ap-northeast-2' $P)"
printf '  %-16s %s ← 0\n'      "미선언 변수"  "$(grep -ci 'undeclared' $P)"
printf '  %-16s %s ← 0\n'      "파괴/교체"    "$(grep -ciE 'will be destroyed|forces replacement' $P)"
printf '  %-16s %s ← = 3\n'    "redis shard"   "$(grep -o 'num_node_groups *= *[0-9]*' $P | head -1)"
printf '  %-16s %s ← = 2\n'    "redis replica" "$(grep -o 'replicas_per_node_group *= *[0-9]*' $P | head -1)"
printf '  %-16s %s ← 2\n'      "aurora inst"   "$(grep -c 'instance_class *= *"db.r7g.large"' $P)"
printf '  %-16s %s ← 1 이상\n' "del.protect"   "$(grep -c 'deletion_protection *= *true' $P)"
```

→ prod 회로(3 shard × replica 2 · Aurora 2 인스턴스 · 삭제 보호)가 plan 에 보여야 `environment=prod` 가 먹은 것이다.

**계정 확인(§3-3 ④)** — Cognito 도메인은 `llm-gateway-prod-<suffix>` 로 조회. EIP 쿼터(기본 5)는 prod NAT 2개 여유 확인:

```bash
SUFFIX=$(echo 'var.cognito_domain_suffix' | terraform console 2>/dev/null | tr -d '"' | tail -1)   # tfvars 값을 terraform 이 읽음(경고문 제거)
echo "Cognito 도메인: $(aws cognito-idp describe-user-pool-domain --region us-west-2 \
  --domain "llm-gateway-prod-$SUFFIX" --query 'DomainDescription.Domain' --output text 2>/dev/null)  ← None 또는 빈 값(미점유)"
echo "VPC: $(aws ec2 describe-vpcs --region us-west-2 --query 'length(Vpcs)' --output text)개 사용 / 쿼터 $(aws service-quotas get-service-quota \
  --service-code vpc --quota-code L-F678F1CE --region us-west-2 --query Quota.Value --output text)"
echo "EIP: $(aws ec2 describe-addresses --region us-west-2 --query 'length(Addresses)' --output text)개 사용 / 쿼터 $(aws service-quotas get-service-quota \
  --service-code ec2 --quota-code L-0263D0A3 --region us-west-2 --query Quota.Value --output text)  ← prod NAT 2개 여유"
```

**apply** (tmux 안, **30~45분** — prod 는 Aurora provisioned 2대·Valkey 9노드라 dev 보다 길다):

```bash
terraform apply         # 마지막에 yes
```

> 🧯 `release aws-load-balancer-controller … context deadline exceeded` 로 끝나면 시간초과(치명적 아님) — `terraform apply` **재실행**(대개 2회차 통과).

`Apply complete!` 후 **elasticache output 검증**:

```bash
terraform output elasticache_endpoint                # clustercfg.… 가 나와야 한다(cluster mode)
terraform output elasticache_configuration_endpoint  # 위와 같은 값
terraform output cluster_name                        # llm-gateway-prod
```

`null`/빈 값이면 outputs.tf 픽스가 안 들어온 것 — 이 상태로 2-7 로 가면 `install-eks.sh` 가 `REDIS_HOST` 누락으로 멈춘다.

### 2-4. Secrets Manager 시크릿 — `/llm-gateway/prod/*` (install-guide §3-4)

> **한 줄**: 무엇을 왜 하는지는 [§3-4](../install-guide.md#3-4-시크릿--손으로-만드는-건-2개-app-redis) — 차트가 읽는
> `/llm-gateway/prod/{app,db,redis}` 중 `db` **는 2-3 terraform 이 이미 만들었고**, 손으로 만드는 건 `app`(새 랜덤값)과
> `redis`(terraform 이 `/redis/auth_token` 에 둔 값을 차트가 읽는 `/redis` 로 복사) 둘뿐이다. 경로의 `prod` 가 tfvars 의
> `environment` 와 같아야 external-secrets IAM 이 허용한다.
>
> 🔴 `/llm-gateway/prod/db` 는 **만들지도 덮어쓰지도 말 것** — `put-secret-value` 로 손대면 `master_password` 가 사라져
> 2-7 migration 이 깨진다. 확인만 한다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
# (1) app — 새 랜덤 3종
aws secretsmanager create-secret --name /llm-gateway/prod/app \
  --secret-string "{
    \"virtual_key_encryption_key\": \"$(openssl rand -hex 32)\",
    \"nextauth_secret\": \"$(openssl rand -hex 32)\",
    \"jwt_jwks_cache_key\": \"$(openssl rand -hex 32)\"
  }"
# (2) redis — terraform 의 /redis/auth_token(raw 문자열) → /redis 의 {"password": …}
REDIS_ARN=$(terraform output -raw elasticache_auth_token_secret_arn)
REDIS_PW=$(aws secretsmanager get-secret-value --secret-id "$REDIS_ARN" --query SecretString --output text)
aws secretsmanager create-secret --name /llm-gateway/prod/redis \
  --secret-string "{\"password\":\"$REDIS_PW\"}"
# (3) db — 확인만
aws secretsmanager get-secret-value --secret-id /llm-gateway/prod/db \
  --query SecretString --output text | jq -r 'keys|@csv'      # "master_password","password"
```

**확인**(값 노출 없이 키·길이만):

```bash
for s in app db redis; do printf '%-6s ' "$s"
  aws secretsmanager get-secret-value --secret-id /llm-gateway/prod/$s \
    --query SecretString --output text | jq -c 'to_entries|map({key,len:(.value|length)})'
done
```

**완료 기준**: 확인 블록 3줄 모두 `len>0`, `db` 에 `master_password`·`password` 두 키.

### 2-5. 컨테이너 이미지 빌드 → ECR (install-guide §3-5)

> **한 줄**: 무엇을 왜 하는지는 [§3-5](../install-guide.md#3-5-이미지-빌드--ecr). 빌드 목록은 **prod values 를 렌더**해 뽑는다 —
> 2-2 의 픽스로 6 서비스 태그가 values 에 명시돼 있어 `Chart.appVersion` 폴백이 없다. 저장소·태그가 없는 빈 계정이면
> 저장소 6개 생성 + 이미지 6개 빌드·push(10~15분, tmux 권장).

▶ **실행** · 배포 EC2 — tmux 안

```bash
cd ~/awsome-ai-gateway
export AWS_DEFAULT_REGION=us-west-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR_BASE="$ACCOUNT.dkr.ecr.us-west-2.amazonaws.com/llm-gateway"

# (1) 저장소 생성 + 로그인
for svc in gateway-proxy admin-api admin-ui notification-worker cost-recorder-worker migration; do
  aws ecr create-repository --repository-name "llm-gateway/$svc" \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=AES256 2>/dev/null || echo "✓ $svc exists"
done
aws ecr get-login-password | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.us-west-2.amazonaws.com"

# (2) 빌드 목록 = prod values 로 helm 이 당길 이미지
CHART=deployment/charts/llm-gateway
helm template t "$CHART" -f "$CHART/values-eks-fargate-prod.yaml" \
  | grep -oE 'image: "[^"]+"' | sed 's/image: "//; s/"$//' | sort -u \
  | grep /llm-gateway/ > /tmp/images.txt
cat /tmp/images.txt            # 6줄: admin-api·admin-ui·cost-recorder-worker·gateway-proxy·migration·notification-worker

# (3) 목록대로 build+push — repo 이름 = 빌드 컨텍스트, migration 만 ./db
while IFS= read -r img; do
  repo=${img##*/llm-gateway/}; repo=${repo%%:*}
  tag=${img##*:}
  ctx="./$repo"; [ "$repo" = migration ] && ctx=./db
  echo "=== build $repo:$tag  (context $ctx) ==="
  docker build --platform linux/amd64 -t "$ECR_BASE/$repo:$tag" "$ctx" \
    && docker push "$ECR_BASE/$repo:$tag"
done < /tmp/images.txt
```

**확인** — ECR 에 6 repo × 태그가 있는지:

```bash
for svc in gateway-proxy admin-api admin-ui notification-worker cost-recorder-worker migration; do
  echo "$svc: $(aws ecr list-images --repository-name "llm-gateway/$svc" \
    --query 'imageIds[].imageTag' --output text)"; done
```

기대 태그: `1.0.52-workers` · `1.0.48-websearch` · `1.0.97-brand` · `1.0.43-rebrand` · `1.0.47-websearch` · `1.0.49-xacct`.
렌더된 이미지 이름 앞의 `123456789012.dkr.ecr.ap-northeast-2…` 는 values 의 placeholder 라 무시 — (3)이 repo·tag 만 뽑아
실제 계정의 `$ECR_BASE` 로 push 한다(설치 때는 `install-eks.sh` 가 `global.imageRegistry` 를 주입).

**완료 기준**: 확인 블록 6줄 모두 태그 1개씩.

### 2-6. helm values 채우기 — org 값 · https · admin internal · inbound-cidrs (install-guide §3-6)

> **한 줄**: [§3-6](../install-guide.md#3-6-values--org-값만-채우기--web-search-키)(org 값) + [8-H](8-H-alb-https.md) §1~2(https 방식 B)
>
> - [8-I](8-I-admin-internal.md)(admin internal)를 **설치 전에 한 번에** `values-eks-fargate-prod.yaml` 에 넣는다. 자동주입값
> (DB/Redis host · IRSA · issuer · imageRegistry)은 건드리지 않는다. 웹서치 URL 은 2-8(§5)에서.

**① org 값** — 이메일 · 관리자 PC 공인 IP · 리포팅 타임존만 묻고 나머지는 terraform output 에서:

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway && bash deployment/scripts/fill-org-values.sh prod
```

PC IP 는 **us-west-2 에 제시되는 출구 IP** 를 넣는다 — 랩톱에서 SSH 로 들어온 EC2 셸에서 `echo ${SSH_CLIENT%% *}`
(사내 프록시는 리전마다 출구가 달라 `checkip` 이 틀릴 수 있음). 타임존은 스크립트가 리전 기준으로 제안하는 값을 Enter 로
받거나, 사용자 조직의 달력 기준으로 **정규 IANA 이름**을 넣는다(약어 `KST`·`IST`·`PST` 나 `US/Pacific` 은 서비스가 거부):
`America/Los_Angeles`(미 서부) · `America/Chicago`(미 중부 — Austin 등 텍사스) · `America/New_York`(미 동부) · `Asia/Kolkata`(인도) · `Asia/Seoul`(한국) · `Asia/Tokyo`(일본) ·
`Asia/Singapore` · `Europe/London` · `Europe/Berlin` · `UTC`. 대시보드·일별 집계의 날짜 경계만 바뀌고 예산 월 경계는 항상 UTC.

**② update-scripts 준비** — 이후 스크립트(`https-env.sh`·`10-switch-https.sh`)가 `config.env` 와 **클러스터 namespace** 를 요구한다.
신규 설치는 아직 namespace 가 없으므로 먼저 만든다(`install-eks.sh` 는 기존 ns 를 허용):

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
[ -f config.env ] || cp config.env.example config.env
sed -i "s/^AWS_ACCOUNT_ID=.*/AWS_ACCOUNT_ID=\"$(aws sts get-caller-identity --query Account --output text)\"/; s/^DEPLOY_ENV=.*/DEPLOY_ENV=\"prod\"/" config.env
grep -n '^AWS_ACCOUNT_ID\|^DEPLOY_ENV' config.env
aws eks update-kubeconfig --region us-west-2 --name llm-gateway-prod
kubectl create namespace llm-gateway
```

**③ https 방식 B** — 도메인 하나 입력 → ZONE_ID·CERT_ARN(2-0) 자동 조회 → Ingress 블록을 443·인증서·host 3개로:

▶ **실행** · 배포 EC2 (`<DOMAIN>` = 2-0 의 값)

```bash
DOMAIN=<2-0 의 도메인>                       # 예: DOMAIN=prod.awsome-ai-gw.click
source https-env.sh $DOMAIN                 # ZONE_ID · CERT_ARN 이 (none yet) 이면 2-0 미완
HOSTS="--gateway-host gateway.$DOMAIN --admin-ui-host admin.$DOMAIN --admin-api-host admin-api.$DOMAIN"
bash 10-switch-https.sh --fresh $HOSTS      # --fresh = 첫 설치 전(Ingress 없음). dry-run: diff·렌더 확인
bash 10-switch-https.sh --fresh $HOSTS --apply
```

`--fresh` 는 **첫 설치 전에만**(스크립트가 기존 Ingress 를 조회하는 `discover` 를 건너뜀) — 운영 중 전환(8-H)은 플래그 없이.
호스트 플래그는 2-0 ⑥ 의 규칙(도메인이 환경을 담으면 `gateway.<DOMAIN>`). 도메인에 환경이 없으면 플래그 없이 기본
`gateway-prod.<DOMAIN>`. diff 에 `listen-ports` 443 · `certificate-arn` · host 3개 · `tls.enabled: true` 가 보이고
`inbound-cidrs` 는 그대로여야 한다.

**④ admin ALB 2개 internal + admin 전용 inbound-cidrs** — `ingress.adminUi`·`ingress.adminApi` 를 internal 로 돌리고 **admin 만**
허용 대역을 VPN 쪽으로 바꾼다(공용 `ingress.annotations` 의 `inbound-cidrs` = EC2+PC IP 는 public gateway 용으로 그대로).

**용어 — 이 두 값이 하는 일**: internal ALB 앞의 **보안그룹(SG) 인바운드 규칙**은 "어느 **출발지 IP 대역**에서 온 443 요청을
받을지"를 정한다. internal ALB 는 사설 IP 만 가지므로 여기엔 공인 IP 가 아니라 **VPC 내부·VPN 경유 트래픽의 출발지 대역**을 넣는다.

- `VPC_CIDR` — VPC 의 사설 주소 공간(2-2 의 `vpc_cidr`, 예 `10.40.0.0/16` = `10.40.0.0`~`10.40.255.255`). 서브넷·파드·ALB·NAT 와
**Client VPN 엔드포인트의 ENI** 가 전부 이 안에서 IP 를 받는다. AWS Client VPN 은 클라이언트 패킷을 엔드포인트 ENI 의 사설 IP 로
**SNAT(출발지 주소 변환)** 하므로, ALB 가 보는 출발지는 VPN 클라이언트가 아니라 **VPC 대역**이다 → 반드시 허용.
- `VPN_CLIENT_CIDR` — Client VPN 엔드포인트가 접속한 각 랩톱/PC 에 할당하는 **터널 내부 IP 풀**(AWS 용어 *Client IPv4 CIDR*).
VPC 와 **겹치면 라우팅이 충돌**하므로 별개 사설 대역이어야 하고, AWS 제약상 `/22`~`/12`(동시 접속 수 ×2 이상 여유). 2-9 에서
엔드포인트를 만들 때 같은 값을 쓴다. SG 에 함께 넣는 이유: 라우트 구성에 따라 SNAT 없이 원래 클라이언트 IP 가 보이는 경우를 대비.
- **고객사(S2S VPN)** 는 `VPN_CLIENT_CIDR` 자리에 VPN(VGW/TGW)을 타고 들어오는 **온프렘 사내 대역**(사무실·VDI 서브넷)을 넣는다.

차트가 per-ingress 어노테이션을 공용 위에 덮어쓴다. 세 액션으로 나눈다:

**④-1 VPN 클라이언트 대역 결정** — 조직이 정하는 값이라 `config.env` 에 한 번 적고, 2-9(Client VPN)도 여기서 읽는다:

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
VPN_CLIENT_CIDR=<VPN 클라이언트 CIDR>          # 예: VPN_CLIENT_CIDR=10.99.0.0/22
grep -q '^VPN_CLIENT_CIDR=' config.env || echo "VPN_CLIENT_CIDR=\"$VPN_CLIENT_CIDR\"" >> config.env
```

**④-2 VPC 대역 읽기** — terraform 이 적용한 값을 그대로. ⚠️ README §3 의 `git reset` 직후면 `.terraform.lock.hcl` 이 되돌아가
`Required plugins are not installed` 가 난다 — 2-3 의 `terraform init` 블록을 한 번 더(lock 재조정만, apply 아님) 하고 진행:

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
VPC_CIDR=$(echo 'var.vpc_cidr' | terraform console 2>/dev/null | tr -d '"' | grep -oE '^[0-9.]+/[0-9]+$')   # 경고문 제거, CIDR 만
echo "admin inbound-cidrs = $VPC_CIDR,$VPN_CLIENT_CIDR"      # 예: 10.40.0.0/16,10.99.0.0/22
```

**④-3 values 편집 — 주석 2줄 해제 +** `inbound-cidrs` **추가**

- 파일(배포 EC2): `/home/ubuntu/awsome-ai-gateway/deployment/charts/llm-gateway/values-eks-fargate-prod.yaml`
- 고칠 곳: `ingress:` 아래 `adminUi:` **와** `adminApi:` **두 블록**(각 블록 끝에 준비된 주석 2줄). `gateway:` 블록은 손대지 않는다.

**전** — 두 블록 모두 이렇게 끝난다(③ 이후라 `host`·`tls.enabled` 는 이미 채워져 있음):

```yaml
  adminUi:
    host: "admin.<DOMAIN>"
    tls:
      enabled: true
    # 최종형(고객 권장): admin 을 private 서브넷 internal ALB 로 — 아래 2줄 해제.
    # 전제 S2S VPN. 상세: docs/us-llm-gateway/architecture.md 「고객사 최종 아키텍처」
    # annotations:
    #   alb.ingress.kubernetes.io/scheme: internal
```

**후** — 주석 2줄의 `#`  를 지우고, 그 아래에 admin 전용 `inbound-cidrs` 1줄을 넣는다(`adminApi:` 도 동일).
⚠️ `#` 한 글자만 지우면 공백이 하나 남아 `annotations:` 가 5칸 들여쓰기가 된다(정상 4칸/6칸) — YAML 이 `adminUi` 의 자식으로
읽지 못해 렌더가 깨진다. `#`  **두 글자**를 지우거나 아래 python 을 쓴다(실제로 겪음):

```yaml
  adminUi:
    host: "admin.<DOMAIN>"
    tls:
      enabled: true
    # 최종형(고객 권장): admin 을 private 서브넷 internal ALB 로 — 아래 2줄 해제.
    # 전제 S2S VPN. 상세: docs/us-llm-gateway/architecture.md 「고객사 최종 아키텍처」
    annotations:
      alb.ingress.kubernetes.io/scheme: internal
      alb.ingress.kubernetes.io/inbound-cidrs: "<VPC_CIDR>,<VPN_CLIENT_CIDR>"    # 예: "10.40.0.0/16,10.99.0.0/22"
```

손으로 하지 말고 아래를 돌린다 — 파일이 어떤 상태든(주석 그대로 · `#` 만 지운 5칸 · 이미 정상) 두 블록을 **같은 결과**로 만든다
(블록 안의 `annotations`·`scheme`·`inbound-cidrs` 줄을 걷어내고 정확한 3줄을 넣는 멱등 편집, 블록이 2개가 아니면 중단):

▶ **실행** · 배포 EC2

```bash
V=~/awsome-ai-gateway/deployment/charts/llm-gateway/values-eks-fargate-prod.yaml
python3 - "$V" "$VPC_CIDR,$VPN_CLIENT_CIDR" <<'PY'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1]); cidrs = sys.argv[2]; L = p.read_text().split("\n")
DROP = ("annotations:", "alb.ingress.kubernetes.io/scheme:", "alb.ingress.kubernetes.io/inbound-cidrs:")
def key_of(l): return re.sub(r"^[\s#]*", "", l).split(" ")[0] if l.strip() else ""
out, i, done = [], 0, 0
while i < len(L):
    l = L[i]
    if l in ("  adminUi:", "  adminApi:"):
        out.append(l); i += 1; blk = []
        while i < len(L) and (L[i].startswith("    ") or not L[i].strip()): blk.append(L[i]); i += 1
        while blk and not blk[-1].strip(): blk.pop()
        blk = [b for b in blk if key_of(b) not in DROP]
        blk += ["    annotations:", "      alb.ingress.kubernetes.io/scheme: internal",
                f'      alb.ingress.kubernetes.io/inbound-cidrs: "{cidrs}"']
        out += blk; done += 1; continue
    out.append(l); i += 1
assert done == 2, f"adminUi/adminApi 블록이 2개여야 하는데 {done}개"
p.write_text("\n".join(out)); print("adminUi · adminApi → scheme: internal + inbound-cidrs (멱등)")
PY
grep -n -A2 '^    annotations:' $V | grep -A1 'scheme: internal'      # 두 블록에 internal + inbound-cidrs 가 보여야
```

**⑤ 렌더 확인** — 파일만 바뀌었고 클러스터는 아직 그대로:

```bash
cd ~/awsome-ai-gateway && helm template t deployment/charts/llm-gateway -f deployment/charts/llm-gateway/values-eks-fargate-prod.yaml \
  | grep -E 'scheme:|inbound-cidrs:|certificate-arn:|^\s+- host:' | sort | uniq -c
```

기대: `scheme: internal` ×2 · `scheme: internet-facing` ×1 · `inbound-cidrs` 2종 · `certificate-arn` ×3 · host 3개.

**완료 기준**: ⑤ 기대값 + `grep -n 'reportingTimezone\|inbound-cidrs\|COGNITO_USER_POOL_ID\|certificate-arn\|scheme:' values-eks-fargate-prod.yaml` 가 전부 실값.

### 2-7. 게이트웨이 설치 — `install-eks.sh prod` → DNS CNAME 3개 (install-guide §3-7)

> **한 줄**: 무엇을 왜 하는지는 [§3-7](../install-guide.md#3-7-설치-실행) — `install-eks.sh` 가 `terraform output`(엔드포인트·IRSA·Cognito)을
> helm `--set` 으로 주입해 migration Job → 파드 6종 → ALB 3개를 만든다. 2-6 의 values 대로 gateway 는 **public·https**, admin 2개는
> **internal·https** 로 태어난다. 끝나면 [8-H §2-4](8-H-alb-https.md) 의 `11-route53-cname.sh` 로 이름 3개를 ALB 에 붙인다.

**① 설치** — tmux 안에서(15분 타임아웃, migration Job 이 먼저 돈다):

▶ **실행** · 배포 EC2 — tmux 안

```bash
export AWS_DEFAULT_REGION=us-west-2
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh prod
```

`STATUS: deployed` 로 끝나야 한다. 스크립트 마지막의 "NEXTAUTH_URL 이 chart 에서 결정됨 — 패치 스킵" 은 https host 를 넣었기 때문(정상).

**② 파드·ALB 확인**:

```bash
kubectl -n llm-gateway get pods
kubectl -n llm-gateway get ingress
```

기대: 파드 전부 `Running`(gateway-proxy 3 · admin-api 3 · admin-ui 2 · cost-recorder-worker 3 · notification-worker 2 · scheduler 1,
migration 은 `Completed`). Ingress 3개의 ADDRESS 가 나오는데 **admin 2개는** `internal-k8s-…`, gateway 는 `k8s-…`(1~2분 걸림).

**③ CNAME 3개** — Ingress 의 host 를 읽어 prod zone(2-0)에 `CNAME → ALB DNS` 로 넣는다(dry-run → apply):

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 11-route53-cname.sh
bash 11-route53-cname.sh --apply
```

admin 2개의 CNAME 은 `internal-…` ALB 를 가리킨다 — 공개 zone 에 있어도 값이 사설 IP 로 풀리므로 VPN 안에서만 닿는다(의도).

> **Route 53 을 안 쓰는 고객(사내 DNS · 외부 레지스트라)** — 스크립트 대신 DNS 담당자에게 **CNAME 3건**을 요청한다. 값은
> `bash 11-route53-cname.sh`(dry-run, 쓰기 없음)의 "Planned records" 표를 그대로 전달하면 된다(= `kubectl -n llm-gateway get ingress`
> 의 HOSTS → ADDRESS 쌍):
>
>
> | 레코드(CNAME)           | 값                                                     | TTL |
> | -------------------- | ----------------------------------------------------- | --- |
> | `gateway.<DOMAIN>`   | gateway Ingress 의 ADDRESS (`k8s-….elb.amazonaws.com`) | 300 |
> | `admin.<DOMAIN>`     | admin-ui Ingress 의 ADDRESS (`internal-k8s-…`)         | 300 |
> | `admin-api.<DOMAIN>` | admin-api Ingress 의 ADDRESS (`internal-k8s-…`)        | 300 |
>
>
> 주의 ① internal ALB 이름은 **사설 IP 로 풀린다** — 사내 리졸버가 사설 응답을 걸러내는(DNS rebinding 보호) 설정이면 예외 등록이
> 필요하다. ② ALB 를 재생성(helm 으로 Ingress 를 지웠다 만들면)하면 ADDRESS 가 바뀌어 CNAME 도 갱신해야 한다 — Route 53 이면
> `11-route53-cname.sh --apply` 재실행, 아니면 DNS 팀에 재요청. ③ 2-0 의 ACM 검증 CNAME 도 같은 DNS 에 있어야 인증서 자동 갱신이 된다.
> 이런 왕복을 피하려면 8-H 0-2-보충처럼 **서브도메인만 Route 53 에 위임**하는 편이 운영이 쉽다.

**④ 첫 https 확인** — gateway 는 배포 EC2 IP 가 `inbound-cidrs` 에 있으니 EC2 에서 바로:

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && source https-env.sh -q   # DOMAIN 을 config.env 에서 복원
sleep 60   # DNS 전파
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://gateway.$DOMAIN/health     # 200 0 (인증서 검증 통과)
dig +short admin.$DOMAIN | tail -2                                                                 # 10.x 사설 IP = internal
```

admin-ui·admin-api 는 이 시점엔 **접속 불가가 정상**(internal) — 2-9 VPN 후 3 절에서 확인한다.

### 2-8. 온보딩 · DB 설정 — Cognito 관리자 · 모델 라우팅 · 웹서치 · Cowork 라우팅 · 팀 예산 (install-guide §3-8 · §4 · §5)

> **한 줄**: [§3-8](../install-guide.md#3-8-cognito-온보딩--스모크)(관리자 1명) → [§4](../install-guide.md#4-claude-code--bedrock-runtime--us-geo-프로파일-배선-us-핵심)
> (모델 alias·라우팅 SQL) → [§5](../install-guide.md#5-서버측-web-search-us-east-1)(web search) → US-02 `01`(Cowork 라우팅)을 prod 계정에서
> 반복한다. DB 는 새로 시드됐으므로 §4 를 **다시** 해야 한다. **admin-ui 가 필요한 것**(팀 예산 · 앱별 웹서치 토글 확인)은 admin 이
> internal 이라 **2-9 VPN 이후 3 절**에서 한다.

**① Cognito 관리자 1명** (§3-8) — 이메일은 2-6 ① 에서 values 에 넣은 것을 자동으로 읽는다. **직접 정해서 넣는 값 2개**:


| 입력값          | 무엇               | 규칙                                                                                                                                           |
| ------------ | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `TEAM_GROUP` | 관리자를 넣을 **팀 그룹** | 자동 = terraform output `cognito_groups` 에서 `ClaudeAdmin` 을 뺀 **첫 그룹**. 팀이 여럿이면 `echo` 로 보고 원하는 이름으로 덮어쓴다(없는 그룹이면 `ResourceNotFoundException`) |
| `TEMP_PW`    | 관리자의 **임시 비밀번호** | Cognito 정책: **12자 이상 · 대문자 · 소문자 · 숫자 · 특수문자** 모두 포함. 첫 로그인 때 새 비번으로 바꾸도록 강제됨                                                                |


▶ **실행** · 배포 EC2 · **⚠ 먼저 바꿀 것:** 아래 블록 **첫 줄**의 `<…>` 

```bash
TEMP_PW='<임시비번 12자+ 대소문자·숫자·특수문자>'      # 예: TEMP_PW='Tmp#Pass-2026-prod'
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
POOL_ID=$(terraform output -raw cognito_user_pool_id)
TEAM_GROUP=$(terraform output -json cognito_groups | jq -r '[.[] | select(. != "ClaudeAdmin")][0]')   # 팀이 여럿이면 덮어쓰기
V=~/awsome-ai-gateway/deployment/charts/llm-gateway/values-eks-fargate-prod.yaml
EMAIL=$(awk '/^    emails:$/{f=1;next} f&&/^      - /{gsub(/^ *- *"?|"$/,""); print; exit}' "$V")
echo "EMAIL=$EMAIL  POOL=$POOL_ID  TEAM=$TEAM_GROUP"
aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$EMAIL" \
  --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --temporary-password "$TEMP_PW" --message-action SUPPRESS
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" --username "$EMAIL" --group-name ClaudeAdmin
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" --username "$EMAIL" --group-name "$TEAM_GROUP"
aws cognito-idp admin-list-groups-for-user --user-pool-id "$POOL_ID" --username "$EMAIL" --query 'Groups[].GroupName' --output text
```

기대: 마지막 줄에 `ClaudeAdmin` + 팀 그룹. 이 이메일·비번이 3 절의 admin-ui 로그인과 6 절 클라이언트 `gateway-cli login` 계정이다.

**② 모델 alias · claude-code 라우팅 SQL** (§4-2 · §4-3) — 세 단계: 접속 정보(prod) → SQL 파일 생성(§4 와 동일 내용, 여기 그대로) → 실행.
무엇을 왜 바꾸는지(`global.`→`us.` Geo 프로파일 · Sonnet 5 신규 · 3모델 외 INACTIVE · claude-code 를 in-account 로)는 §4-2·§4-3 참고.

**②-a 접속 정보** (RDS Proxy 는 private subnet 전용이라 배포 EC2 에서 직접 못 붙고, 클러스터 안 임시 psql 파드로 간다):

▶ **실행** · 배포 EC2

```bash
kubectl config current-context                         # …:cluster/llm-gateway-prod 여야(2-6 ②)
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
export PGHOST=$(terraform output -raw rds_proxy_endpoint)
export PGPASSWORD=$(aws secretsmanager get-secret-value --secret-id /llm-gateway/prod/db \
  --query SecretString --output text | jq -r '.password')
```

**②-b SQL 파일 생성** — 아래 두 블록을 **통째로** 붙여넣으면 `~/us-setup.sql` 이 만들어진다(SQL 에 dev/prod 구분 없음):

> 💲 **단가 근거** — (C)의 Sonnet 5 와 시드의 Opus 4.8($5/$25)·Haiku 4.5($1/$5)는 Anthropic 공표가 = **Bedrock US 리전 base** 값이고,
> `us.*` US Geo cross-region 은 source 리전(us-west-2) 가격 그대로(프리미엄 없음). AWS Price List API 는 Claude 4.x/5 를 안 실어
> 자동 검증이 불가하므로 **콘솔 Bedrock 가격 페이지(us-west-2)** 와 대조해 다르면 3 절에서 admin-ui `/models` 편집으로 고친다
> (소급 없음 — 비용 기록만 영향). Sonnet 5 프로모 종료일(8/31) 은 시점 의존.
>
> ⚠️ **리전이 다르면 단가도 바꾼다.** Bedrock 단가는 **리전별**이고(예: ap-northeast-2·eu-* 는 US 와 다를 수 있음) inference profile 도
> `us.`/`apac.`/`eu.`/`global.` 마다 다르다. us-west-2 가 아닌 리전에 prod 를 올리면 (C)의 Sonnet 5 값과 시드의 Opus 4.8·Haiku 4.5 값을
> **그 리전의 Bedrock 가격 페이지 값으로 교체**한다 — (C)는 SQL 숫자를 고치고, 시드 2개는 설치 후 admin-ui `/models` 편집
> (또는 8-M 의 `02-add-opus5-model.sh` 방식으로 새 단가 행 INSERT). `provider_model_id` 의 접두어(`us.` → `apac.` 등)도 함께.

▶ **실행** · 배포 EC2 — (A)(B)(C)(D)

```bash
cat > ~/us-setup.sql <<'SQL'
-- (A) 기존 Opus 4.8 / Haiku 4.5 alias → US Geo 프로파일 (provider·api_format 은 native 유지)
UPDATE model.model_aliases
   SET provider_model_id = CASE alias
                             WHEN 'claude-opus-4-8'            THEN 'us.anthropic.claude-opus-4-8'
                             WHEN 'claude-haiku-4-5-20251001'  THEN 'us.anthropic.claude-haiku-4-5-20251001-v1:0'
                           END
 WHERE alias IN ('claude-opus-4-8','claude-haiku-4-5-20251001');
--  ⚠️ Haiku 는 runtime ID 라 날짜접미사+버전(-20251001-v1:0) 이 붙는다. Opus/Sonnet 은 안 붙음.

-- (B) Sonnet 5 alias 신규 등록 (기본 시드에 없음) — native + US Geo
INSERT INTO model.model_aliases
    (alias, provider, provider_model_id, endpoint_url, api_format, status, description, created_by)
VALUES
    ('claude-sonnet-5', 'BEDROCK', 'us.anthropic.claude-sonnet-5', NULL, 'BEDROCK_NATIVE', 'ACTIVE',
     'Claude Code -> bedrock-runtime US Geo Sonnet 5 (source us-west-2)',
     '00000000-0000-4000-a000-000000000010')
ON CONFLICT (alias) DO UPDATE
   SET provider='BEDROCK', provider_model_id='us.anthropic.claude-sonnet-5',
       endpoint_url=NULL, api_format='BEDROCK_NATIVE';

-- (C) Sonnet 5 요금(비용 기록용) — **Amazon Bedrock 단가**(US Geo=base, 프리미엄 없음).
--   ⚠️ 컬럼명은 실제 스키마 기준: cache_creation_5m/1h_price_per_1k_tokens · effective_from/effective_until
--      (옛 예시의 cache_write_.../effective_date 는 존재하지 않는 컬럼 → INSERT 실패했음).
--   ⚠️ 프로모: ~2026-08-31 $2/$10, 2026-09-01~ 표준 $3/$15 (per 1M input/output).
--      cost-recorder(router_service)가 effective_from<=now +(effective_until IS NULL OR >now) 로
--      시점별 단가를 고르므로, 두 행을 넣으면 9/1에 자동 전환된다.
--   캐시 단가 = Anthropic 공식 published Sonnet 5 값(Bedrock base 동일). 기간별 base×(1.25 / 2 / 0.1):
--      프로모 $2.50 / $4.00 / $0.20 · 표준 $3.75 / $6.00 / $0.30 per 1M (5m write / 1h write / read).
INSERT INTO model.model_pricings
    (id, model_alias, input_price_per_1k_tokens, output_price_per_1k_tokens,
     cache_creation_5m_price_per_1k_tokens, cache_creation_1h_price_per_1k_tokens, cache_read_price_per_1k_tokens,
     effective_from, effective_until, created_by)
SELECT * FROM (VALUES
    -- 프로모 ($2/$10 in/out) + 캐시는 Sonnet 4.5 값, ~2026-08-31
    (gen_random_uuid(), 'claude-sonnet-5',
     0.002000, 0.010000, 0.003750, 0.006000, 0.000300,
     '2026-06-30T00:00:00Z'::timestamptz, '2026-09-01T00:00:00Z'::timestamptz,
     '00000000-0000-4000-a000-000000000010'::uuid),
    -- 표준 ($3/$15), 2026-09-01~
    (gen_random_uuid(), 'claude-sonnet-5',
     0.003000, 0.015000, 0.003750, 0.006000, 0.000300,
     '2026-09-01T00:00:00Z'::timestamptz, NULL::timestamptz,
     '00000000-0000-4000-a000-000000000010'::uuid)
) AS v
WHERE NOT EXISTS (SELECT 1 FROM model.model_pricings WHERE model_alias='claude-sonnet-5');

-- (D) 이 배포의 3모델(§0) 외 전부 INACTIVE — ⚠️ 반드시 (A)(B) 다음(sonnet-5 가 있어야).
--   시드는 alias 를 여럿 ACTIVE 로 깐다:
--     · global.* 잔재: claude-sonnet-4-6 · claude-sonnet-4-6[1m] · claude-opus-4-7 ·
--       global.anthropic.claude-opus-4-6-v1 · global.anthropic.claude-opus-4-8(= opus-4-8 중복)
--     · out-of-scope Mantle/Codex: cowork-opus(anthropic.*, Mantle Tokyo) · codex-gpt(openai.gpt-5.5)
--   전부 이 배포엔 없는 백엔드(전세계 라우팅 / Mantle 905·Tokyo / Codex us-east-2)라, ACTIVE 로
--   두면 /v1/models 에 떠서 고르는 순간 실패한다(AccessDenied·라우팅 에러). 그래서 provider_model_id
--   LIKE 'global.%' 만으로는 부족 — codex/cowork 는 다른 접두어라 안 걸린다. 3모델만 남긴다.
--   되돌리기: PATCH /admin/models/{alias}/status. INACTIVE 는 FK 안전(DELETE 아님).
UPDATE model.model_aliases
   SET status = 'INACTIVE'
 WHERE alias NOT IN ('claude-opus-4-8','claude-sonnet-5','claude-haiku-4-5-20251001');
SQL
```

▶ **실행** · 배포 EC2 — §4-3 이어붙이기(`>>`)

```bash
cat >> ~/us-setup.sql <<'SQL'

-- §4-3: claude-code 라우팅 — cross-account 배선 제거(이 계정에서 직접 호출)
UPDATE model.routing_profiles
   SET backend            = 'invoke',       -- native(Mantle 아님) — 기본값 유지
       account_role_arn   = NULL,           -- 다른 계정 assume 끔 → in-account(파드 IRSA 직접)
       external_id        = NULL,           -- cross-account 용 값 제거
       region             = 'us-west-2',    -- 스키마 NOT NULL — 이 경로에선 no-op
       web_search_enabled = true            -- §5 서버측 web search 클라이언트별 토글
 WHERE client = 'claude-code';
-- row 가 없으면 INSERT:
INSERT INTO model.routing_profiles (client, backend, account_role_arn, region, default_model, external_id, enabled, web_search_enabled)
SELECT 'claude-code','invoke',NULL,'us-west-2',NULL,NULL,true,true
WHERE NOT EXISTS (SELECT 1 FROM model.routing_profiles WHERE client='claude-code');

-- 검증 (결과가 안 보이면 SQL 미전달 = 무동작 성공 주의)
SELECT alias, provider_model_id, status FROM model.model_aliases ORDER BY status, alias;
SELECT client, backend, region, account_role_arn, external_id, web_search_enabled
  FROM model.routing_profiles WHERE client = 'claude-code';
SQL
```



**②-c 실행**:

▶ **실행** · 배포 EC2

```bash
wc -l ~/us-setup.sql                                   # 수십 줄이어야(몇 줄이면 붙여넣기 잘림)
kubectl -n llm-gateway run psql --rm -i --restart=Never --pod-running-timeout=5m \
  --image=public.ecr.aws/docker/library/postgres:16 \
  --env="PGHOST=$PGHOST" --env="PGUSER=gateway" --env="PGDATABASE=gateway" \
  --env="PGPASSWORD=$PGPASSWORD" \
  --command -- psql -v ON_ERROR_STOP=1 --echo-all < ~/us-setup.sql
```

기대(맨 끝 SELECT 2개): ACTIVE alias 3개가 전부 `us.anthropic.*` · `claude-code` 행이 `invoke / us-west-2 / NULL / NULL / true`.
kubectl 컨텍스트가 prod 클러스터(2-6 ②)인지 먼저 확인: `kubectl config current-context`.

**③ 서버측 web search** (§5-1 → §5-2) — prod 전용 AgentCore Gateway(us-east-1) 를 만들고 URL 을 values 에 넣어 재배포:

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
CALLER_ROLE_ARN=$(cd deployment/terraform/environments/llm-gateway-prod && terraform output -raw gateway_proxy_role_arn)
REGION=us-east-1 GW_NAME=llm-gateway-websearch-prod CALLER_ROLE_ARN="$CALLER_ROLE_ARN" \
  python3 deployment/scripts/provision_agentcore_websearch.py deploy
bash deployment/scripts/set-websearch-url.sh prod      # GW_NAME 기본값 = llm-gateway-websearch-prod → AGENTCORE_* 주입
./deployment/scripts/install-eks.sh prod               # env 가 바뀌어 gateway-proxy 재시작(values 실값은 그대로)
```

`deploy` 끝의 Gateway URL 이 `set-websearch-url.sh` 로 values 에 들어간다(손으로 옮기지 않음). dev 의 gateway 를 공유하지 않는 이유:
teardown 단위를 계정·환경과 맞추기 위함(IRSA 는 계정 안 `gateway/*` 를 허용하므로 기술적으론 공유도 됨).

**④ Cowork 라우팅** (US-02 `01`) — 시드가 Cowork 를 존재하지 않는 계정(Mantle)으로 보내므로 in-account 로 교정. `config.env` 의
`COWORK_BACKEND="invoke"`, `COWORK_REGION=""`(빈 값 = 이 리전) 기본값 그대로:

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 00-preflight-check.sh            # 읽기 전용 — 계정·ns·DB 시크릿·Ingress 가 prod 로 잡히는지
bash 01-fix-cowork-routing.sh         # dry-run
bash 01-fix-cowork-routing.sh --apply
```

**④-2 Opus 5 등록** (US-02 `02`, 상세 [8-M](8-M-models.md)) — 시드엔 Opus 4.8 까지라 Opus 5 는 등록해야 Claude Code·Cowork 에서 보인다.
`config.env` 의 `MODEL_ALIAS="claude-opus-5"` · `MODEL_PROVIDER_ID="us.anthropic.claude-opus-5"`(US Geo, `us.` 접두 필수) · 단가 5종
(`MODEL_PRICE_*`, `MODEL_PRICE_ASOF`) 기본값 그대로. **단가는 리전 기준**(②-b 의 💲·⚠️ 메모) — us-west-2 가 아니면 `config.env` 값부터 고친다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
grep -n '^MODEL_ALIAS\|^MODEL_PROVIDER_ID\|^MODEL_PRICE_' config.env     # alias · us. 접두 · 단가 5종 + ASOF 확인
bash 02-add-opus5-model.sh            # dry-run: 등록될 내용 + 현재 ACTIVE 목록
bash 02-add-opus5-model.sh --apply
```

기대: 마지막 Verification 에 `claude-opus-5 | us.anthropic.claude-opus-5 | ACTIVE | 0.005000 | 0.025000`. 반영은 ⑤ 캐시 5분 뒤.
`00-preflight-check.sh` 가 `team_allowed_models has N rows` 로 경고하면(화이트리스트 모드) `--team-id <uuid>` 를 붙인다(8-M ⓐ).

**⑤ 캐시 5분** (§4-4) — DB 를 직접 고쳤으므로 gateway-proxy 의 Redis 캐시(TTL 300s)가 빠질 때까지 기다린다. 파드 재시작으로는
안 지워진다(외부 ElastiCache). 급하면 §4-4 의 `redis-cli DEL` 블록을 prod 디렉터리로 바꿔 실행.

**⑥ 남은 것(2-9 VPN 후, 3 절)**: 첫 admin-ui 로그인 → 팀 예산(`/budgets`, 없으면 첫 요청이 `429`) → 앱별 웹서치 토글 확인(§5-3).

**완료 기준**: ①의 그룹 2개 · ②의 SELECT 기대값 · ③ `AGENTCORE_GATEWAY_URL` 이 values 에 있고 `install-eks.sh prod` `deployed` ·
④ `01 … --apply` 성공 · ④-2 `claude-opus-5` ACTIVE + 단가 행.

### 2-9. Client VPN 스탠드인 (`client-vpn.sh`) — 고객사 S2S VPN 의 대체

> **한 줄**: admin ALB 2개가 internal 이라 사용자망→VPC 경로가 있어야 admin-ui 로그인·VK 발급이 된다. 고객사는 **S2S VPN**(VGW/TGW)이
> 그 경로다. VPN 이 없는 검증 계정에선 **AWS Client VPN** 엔드포인트를 게이트웨이 VPC 에 세워 랩톱/PC 가 OpenVPN 으로 들어온다 —
> `deployment/scripts/client-vpn.sh` 가 인증서(mutual TLS) → ACM → 엔드포인트 → private 서브넷 연결 → VPC 인가까지 만든다.
> 고객사 배포에선 이 절을 **건너뛴다**(S2S VPN 개통 = 8-I 전제). 비용: 서브넷 연결 시간당 + 접속 시간당 → 안 쓸 땐 `down`.

**① 만들기** — `VPN_CLIENT_CIDR` 는 2-6 ④-1 에서 `config.env` 에 적은 값을 읽는다(VPC 와 겹치면 중단):

▶ **실행** · 배포 EC2

```bash
export AWS_DEFAULT_REGION=us-west-2
cd ~/awsome-ai-gateway && bash deployment/scripts/client-vpn.sh prod up
```

`available` 까지 서브넷 연결 후 수 분. 끝에 `status` 표(엔드포인트 · 서브넷 associated · 인가 `10.x/16` active)가 나온다.

**② 클라이언트 설정 파일** — 클라이언트 인증서·키를 인라인한 `.ovpn` 을 만든다:

▶ **실행** · 배포 EC2

```bash
bash deployment/scripts/client-vpn.sh prod config      # ~/client-vpn/prod/prod-client.ovpn
```

▶ **실행** · 작업자 랩톱 — 파일 가져오기

```bash
scp -i <키> ubuntu@<배포 EC2 IP>:client-vpn/prod/prod-client.ovpn .
```

- **Mac / Windows**: [AWS VPN Client](https://aws.amazon.com/vpn/client-vpn-download/) 설치 → File ▸ Manage Profiles ▸ Add Profile 로 `.ovpn` 등록 → Connect.
(Windows 테스트 머신 2-10 도 같은 파일.) OpenVPN 호환 클라이언트도 된다.
- split-tunnel 이라 VPC 대역(`10.40/16`)만 VPN 으로 가고 나머지 인터넷은 그대로 — gateway(public ALB)는 기존처럼 공인 IP 로 간다.

**③ 접속 확인** — 랩톱에서 VPN Connected 상태로:

▶ **실행** · 작업자 랩톱

```bash
dig +short admin.<DOMAIN> | tail -1                                  # 10.40.x — internal ALB 사설 IP
curl -sS -o /dev/null -w '%{http_code}\n' https://admin.<DOMAIN>/       # 307(로그인 리다이렉트) — VPN 없이면 타임아웃
curl -sS -o /dev/null -w '%{http_code}\n' https://admin-api.<DOMAIN>/health
```

브라우저 `https://admin.<DOMAIN>` 이 열리면 2-8 ⑥(팀 예산)으로 — 3 절.

**상태 · 정리**: `bash deployment/scripts/client-vpn.sh prod status` / `… down`(엔드포인트·ACM·SG 삭제, 인증서 파일은 `~/client-vpn/prod/` 에 남음).

> ⚠️ 두 CIDR 규칙(2-6 ④ 용어): 클라이언트 트래픽은 엔드포인트 ENI 로 SNAT 되므로 ALB 가 보는 출발지는 **VPC 대역** — admin `inbound-cidrs`
> 에 VPC CIDR 이 빠지면 VPN 이 붙어도 admin 이 안 열린다. `.ovpn` 에는 **개인키가 들어 있다** — 공유·커밋 금지.

**완료 기준**: `status` 가 `available`/`associated`/`active` · 랩톱에서 `https://admin.<DOMAIN>` = 307/200 · `route -n get <admin 사설 IP>` 의 interface 가 `utun`.

### 2-10. Cowork Windows — 테스트 머신 #2 생성(prod 계정) → 설치기 빌드 → 설치 → 검증

> **한 줄**: 고객 PC 역할의 Windows 머신을 prod 계정에 **새로** 만든다 — dev 계정의 테스트 머신은 dev 용이고,
> 설치기의 HKLM 정책이 머신 전역이라 한 PC 에 dev·prod 를 같이 둘 수 없다. Cowork 의 Windows 샌드박스가
> Hyper-V 를 요구하므로 **metal 인스턴스 필수**.

**(a) 머신 스펙** — dev 테스트 머신(`window-pc-metal`) 과 동일 원칙:

- 계정·리전·서브넷: prod 계정 / `<region>` / default VPC 의 public 서브넷(배포 EC2 와 같은 AZ, public IP 자동)
- AMI `Windows Server 2025 English Full Base (BIOS)` 최신(dev 테스트 머신과 동일) ·
`m5zn.metal`(≈$6/hr — **안 쓸 땐 stop**) · 200 GB gp3
- 키 페어 `<키 페어>`(기존) · SG 신규 `window-pc-rdp` = 3389 ← 작업자 IP `/32` 만 ·
IAM 신규 `window-pc-ssm` = `AmazonSSMManagedInstanceCore` 만 · Name `window-pc-metal-prod`

**(b) 생성 순서**: IAM role+profile → SG → `run-instances` → `get-password-data`(`.pem`) → RDP →
1회 준비(Hyper-V 역할+재부팅 · Claude Desktop · AWS VPN Client · 빌드 도구 Inno Setup+Python —
[cowork-installer-build-windows.md](../cowork/installer/cowork-installer-build-windows.md) §0) → **stop**.
실제 설치·검증은 2-7(게이트웨이 기동) 이후 start.

**(c) 설치기 빌드 · 설치 · Cowork 실행** — 절차는 기존 가이드 그대로, prod 에서 다른 것만 아래 표:


| 단계                                | 따라갈 문서                                                                                     | prod 에서 다른 것                                                                                                                                                                                                                                                                             |
| --------------------------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 값 뽑기                              | [빌드 가이드 §2](../cowork/installer/cowork-installer-build-windows.md) — `07-client-values.sh` | URL 3개가 https(`gateway.<DOMAIN>`, `admin-api.<DOMAIN>`), Cognito 는 prod 풀. `coworkModels` = `00-preflight-check.sh` 의 ACTIVE alias                                                                                                                                                       |
| 빌드                                | [빌드 가이드 §0~§3](../cowork/installer/cowork-installer-build-windows.md)                      | 없음(winget `--source winget` 반영됨)                                                                                                                                                                                                                                                         |
| 설치 · `setup` · `login` · `verify` | [관리자 E2E §2~§4](../cowork/installer/cowork-installer-admin-e2e-windows.md)                 | **admin-api 가 internal** → `login`·`verify`·VK 갱신은 PC 가 **VPN Connected** 상태(2-9 `.ovpn`, `Test-NetConnection admin-api.<DOMAIN> -Port 443` = True). PC **공인 IP 를 gateway 허용목록**에: `05-allow-client-ip.sh --add <IP>/32 --targets gateway --apply` → `06-persist-annotations.sh --apply` |
| Claude Desktop(Cowork)            | [수동 설치 §절차 0·3](../cowork/manual/cowork-client-install-windows.md) — offline `.msix`       | Windows **Server** 테스트 머신이면 Hyper-V 역할 + `VirtualMachinePlatform` + `Containers` 후 재부팅(readiness check 의 HNS/vfpext)                                                                                                                                                                     |
| 사용자 안내                            | [사용자 가이드](../cowork/installer/cowork-installer-user-windows.md)                            | 없음                                                                                                                                                                                                                                                                                       |


**Windows PC 의 AWS VPN Client 설정**(검증 계정 = 2-9 Client VPN 을 쓸 때만; 고객사 S2S VPN 이면 PC 가 이미 사내망이라 불필요):

▶ **실행** · Windows PC — 관리자 PowerShell

```powershell
$m = "$env:USERPROFILE\Downloads\AWS_VPN_Client.msi"
Invoke-WebRequest "https://d20adtppz83p9s.cloudfront.net/WPF/latest/AWS_VPN_Client.msi" -OutFile $m   # mask-ok: AWS 공식 다운로드 URL
Start-Process msiexec.exe -ArgumentList "/i `"$m`" /qn" -Wait
& "C:\Program Files\Amazon\AWS VPN Client\AWSVPNClient.exe"
```

2-9 ② 의 `<env>-client.ovpn` 을 PC 로 옮겨(RDP 클립보드 → 메모장 저장 등) File ▸ Manage Profiles ▸ Add Profile → Connect. 확인:

```powershell
Test-NetConnection admin-api.<DOMAIN> -Port 443 | Select TcpTestSucceeded     # True 여야 (internal admin-api)
```

**완료 기준**: `gateway-cli-cowork verify` 의 `inferenceGatewayBaseUrl`·`inferenceModels` 가 prod 값 · `login` 에 `vk_cached`.

**(d) 되돌리기**: `terminate-instances` + SG · role 삭제(EBS 만 과금이던 것도 소멸).

## 3. 검증

1. **팀 예산** — 첫 VK 발급(2-10 `login` 또는 Claude Code `gateway-cli login`)으로 Cognito 그룹 팀이 생긴 뒤, VPN 안에서
  `https://admin.<DOMAIN>/budgets` → 그 팀에 월 한도 입력(없으면 첫 요청이 `429`). 반영 ~3분.
2. **Cowork** — Claude Desktop → Cowork 탭 → 모델 선택 → 질문 1개 → 응답.
3. **서버 기록** — `usage_logs` 에 client 별 행과 **0 이 아닌 비용**이 있어야 단가 경로까지 정상(2-8 ②-a 의 `PGHOST`·`PGPASSWORD` 가 있는 셸):

▶ **실행** · 배포 EC2

```bash
kubectl -n llm-gateway run psql-check --restart=Never --image=public.ecr.aws/docker/library/postgres:16 \
  --env="PGHOST=$PGHOST" --env="PGUSER=gateway" --env="PGDATABASE=gateway" --env="PGPASSWORD=$PGPASSWORD" \
  --command -- psql -c "SELECT client, model_alias, cost_usd, web_search_count, requested_at FROM usage.usage_logs ORDER BY requested_at DESC LIMIT 5;"
kubectl -n llm-gateway wait --for=jsonpath='{.status.phase}'=Succeeded pod/psql-check --timeout=120s
kubectl -n llm-gateway logs psql-check; kubectl -n llm-gateway delete pod psql-check --wait=false
```

기대: `client = cowork`(또는 `claude-code`) 행, `cost_usd > 0`. (`--rm -i` 로 돌리면 파드 삭제 경합으로 출력이 유실될 수 있어 run → wait → logs 로.)

1. (선택) **Claude Code** — [client-install.md](../client-install.md) §6 그대로(env 4줄 = `07-client-values.sh`, VPN 안에서 `gateway-cli login`) → `claude` 로 질문 → `usage_logs` 에 `client=claude-code`.
2. (선택) **서버측 웹서치** — install-guide §5-4(CloudWatch `InvokeGateway tools/call` 또는 `usage_logs.web_search_count`).

**완료 기준**: 2·3 통과 — Cowork 응답 + `usage_logs` 의 cowork 행 비용 > 0.

## 4. teardown

> 🔴 되돌릴 수 없다. 데이터가 필요하면 **Aurora 스냅샷 먼저**. 아래 순서는 실측 검증 전(문서 작성 시점엔 스택 유지) — 실행할 땐
> 배포 EC2 의 **Claude Code(Bedrock)** 에게 이 절을 그대로 주고 "한 단계씩 확인하며 진행" 시키는 것을 권장한다(리소스 ID·순서 실수 방지).

**terraform 이 지우는 것**: VPC·EKS·Aurora·Valkey·Cognito·IRSA·NAT·VPC endpoint
**terraform 밖(손으로)**: ECR 6 repo · Secrets Manager `/llm-gateway/prod/`* · AgentCore WebSearch GW(us-east-1) · Client VPN(2-9) ·
Route 53 zone `prod.<domain>` + 부모 zone 의 NS 1건 · ACM 인증서 · Windows 테스트 머신(2-10) · 배포 EC2 · tfstate 버킷(유지)

▶ **실행** · 배포 EC2 (위에서 아래 순서)

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/client-vpn.sh prod down                                   # 1) VPN 엔드포인트·ACM(VPN)·SG
helm uninstall llm-gateway -n llm-gateway && kubectl delete namespace llm-gateway  # 2) ALB 3개가 사라진다(컨트롤러)
C=$(cd deployment/terraform/environments/llm-gateway-prod && terraform output -raw aurora_endpoint | cut -d. -f1)
aws rds modify-db-cluster --db-cluster-identifier "$C" --no-deletion-protection --apply-immediately   # 3) prod 는 삭제 보호
cd deployment/terraform/environments/llm-gateway-prod && terraform destroy       # 4) ~20분, 최종 스냅샷 자동
REGION=us-east-1 GW_NAME=llm-gateway-websearch-prod python3 ~/awsome-ai-gateway/deployment/scripts/provision_agentcore_websearch.py teardown
for s in app db redis; do aws secretsmanager delete-secret --secret-id /llm-gateway/prod/$s --force-delete-without-recovery; done
for r in gateway-proxy admin-api admin-ui notification-worker cost-recorder-worker migration; do aws ecr delete-repository --repository-name llm-gateway/$r --force; done
```

그다음 콘솔/CLI 로: ACM 인증서 삭제 → Route 53 `prod.<domain>` zone 삭제(레코드 먼저) → 부모 zone 의 NS 레코드 삭제 →
Windows 테스트 머신 terminate(+SG·IAM role) → 배포 EC2 stop.

## 5. 함정 모음 (검증 계정 실측)

- **install-guide 블록을 그대로 복사하면 dev 경로** — `llm-gateway-dev`·`values-eks-fargate-dev.yaml`·`install-eks.sh dev`. 명령은 이 문서 블록만(2-3 에서 실제로 dev plan 이 나옴).
- **README §3 의** `git reset` **뒤엔** `terraform init` **다시** — `.terraform.lock.hcl` 이 되돌아가 `Required plugins are not installed`(2-3 의 init 블록 재실행, apply 아님).
- `10-switch-https.sh` **는 첫 설치 전엔** `--fresh` — 없으면 Ingress 조회에서 중단.
- `#` **만 지우면 YAML 들여쓰기가 5칸** — values 주석 해제는 2-6 ④-3 의 스크립트로.
- **Client VPN 인증서엔 keyUsage 필수** — 없으면 `TLS handshake error`(스크립트가 넣음).
- **public gateway ALB 는 허용목록** — 테스트 PC/Windows 머신 공인 IP 를 `05-allow-client-ip.sh … --targets gateway` 로, `06-persist-annotations.sh` 로 values 에 영구화. EC2 공인 IP 는 stop/start 마다 바뀐다(EIP 미사용 시).
- **팀 예산은 첫 VK 발급 뒤에** — Cognito 그룹 팀이 그때 생기고 $0 · admin-ui 는 dev-login(무인증)이라 internal ALB + VPN 이 유일한 보호.
- **Windows Server 에선 Hyper-V 만으론 Cowork readiness 실패** — `VirtualMachinePlatform` + `Containers` 후 재부팅. winget 은 `--source winget`.
- **prod 전용 자원 이름** — AgentCore 실행 역할이 dev 이름(`llm-gateway-dev-agentcore-websearch-gw`)으로 재사용될 수 있음(`ROLE_NAME` 으로 지정 가능). Aurora `deletion_protection`·Valkey `apply_immediately=false`(변경이 유지보수 창 대기).

