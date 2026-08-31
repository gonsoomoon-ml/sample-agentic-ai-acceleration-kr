# US LLM Gateway — 운영 참조 (§8 설치 후 운영 작업)

> **설치 중엔 이 문서를 볼 일이 없다.** 설치는 [install-overview.md](install-overview.md) → [install-guide.md](install-guide.md) 순서로 한다.
> 이 문서는 **설치가 끝난 뒤** 하는 **운영 작업**(업데이트·직원 온보딩·보안 하드닝·네트워크 경로·EKS 업그레이드·teardown·TTL·prod 승격·멀티계정)을 할 때 본다.
>
> 📌 본문의 `§0`**~`§6` 은 다른 문서의 절 번호**다 — `§0` = [install-overview.md](install-overview.md)의 범위, `§1`~`§6` = [install-guide.md](install-guide.md). (옛 §7 배포 후 보안은 이 문서 [§8-S](#8-s-배포-후-보안-하드닝-직원-오픈-전-필수) 로 옮겨왔다.)

---

## 8. 설치 후 운영 작업

> 순서 = **POC 사용 빈도순** — 자주(업데이트·모델·온보딩·보안) → 가끔(네트워크 경로·EKS 업그레이드·teardown·TTL) → POC 이후(prod 승격·멀티계정).

---

| ID | 절 | 언제 | 문서 |
|---|---|---|---|
| §8-U | 업데이트 (코드 변경 반영) | 코드·차트·terraform 이 바뀔 때마다 | [ops/8-U-update.md](ops/8-U-update.md) |
| §8-M | 모델 추가와 교체 | 모델 추가·교체 | [ops/8-M-models.md](ops/8-M-models.md) |
| §8-Y | 직원 온보딩 — Cognito 사용자 추가 | 직원 추가 시 | [ops/8-Y-onboarding.md](ops/8-Y-onboarding.md) |
| §8-S | 배포 후 보안 하드닝 (직원 오픈 전 필수) | 직원 오픈 전 1회 | [ops/8-S-hardening.md](ops/8-S-hardening.md) |
| §8-N | Bedrock 을 NAT 대신 VPC Endpoint(PrivateLink)로 | 기존 VPC 1회 (US-04) | [ops/8-N-vpc-endpoint.md](ops/8-N-vpc-endpoint.md) |
| §8-E | EKS 버전 업그레이드 (1.31 → 1.34) | EKS 버전 올릴 때 (US-05) | [ops/8-E-eks-upgrade.md](ops/8-E-eks-upgrade.md) |
| §8-H | ALB HTTPS — 커스텀 도메인 + ACM (방식 A → B) | 도메인이 있을 때 (US-06, 선택 · 운영이면 강력 권장) | [ops/8-H-alb-https.md](ops/8-H-alb-https.md) |
| §8-I | admin ALB 2개를 internal 로 (고객사 최종형) | S2S VPN 개통 후 (US-07, 선택) | [ops/8-I-admin-internal.md](ops/8-I-admin-internal.md) |
| §8-P | dev → prod 승격 — 별도 계정에 prod 스택 신설 | prod 승격 (US-08) | [ops/8-P-prod.md](ops/8-P-prod.md) |
| §8-L | Admin UI Cognito 로그인 활성화 (dev-login 대체) | dev-login 끄고 싶을 때 (US-10, 선택 · 운영이면 강력 권장) | [ops/8-L-admin-ui-login.md](ops/8-L-admin-ui-login.md) |
| §8-W | Notification 발송 채널 변경 | 메일을 실제로 보내고 싶을 때 | [ops/8-W-notifications.md](ops/8-W-notifications.md) |
| §8-T | teardown (과금 중단 · 초기화) | 과금 중단 | [아래](#8-t-teardown-과금-중단--초기화) |
| §8-Z | 토큰 TTL 조절 | 토큰 수명 바꿀 때 | [ops/8-Z-token-ttl.md](ops/8-Z-token-ttl.md) |
| §8-X | 멀티계정 확장 — claude-code 를 별도 계정 Bedrock 으로 | 멀티계정 확장 | [아래](#8-x-멀티계정-확장--claude-code-를-별도-계정-bedrock-으로) |

---

### 8-U. 업데이트 (코드 변경 반영)

`git pull` 후 바뀐 것에 따라 **A 서비스 코드 / B 차트·values / C terraform** 중 하나 → 공통 마지막 `install-eks.sh dev`. 0단계(저장소 갱신·values 백업) 를 건너뛰면 추론이 멈춘다.
→ **[ops/8-U-update.md](ops/8-U-update.md)**

---

### 8-M. 모델 추가와 교체

`02-add-opus5-model.sh` 는 범용 — `config.env` 의 `MODEL_ALIAS`·`MODEL_PROVIDER_ID` 로 어떤 모델이든 등록. ⚠️ 단가 누락 = 비용 `$0` 기록·예산 우회.
→ **[ops/8-M-models.md](ops/8-M-models.md)**

---

### 8-Y. 직원 온보딩 — Cognito 사용자 추가

직원 Cognito 사용자 추가(`admin-create-user` + 그룹) — **팀 그룹 필수**(없으면 VK 발급 403), 관리자만 `ClaudeAdmin`. 그룹은 tfvars `cognito_groups` 에서만 생성.
→ **[ops/8-Y-onboarding.md](ops/8-Y-onboarding.md)**

---

### 8-S. 배포 후 보안 하드닝 (직원 오픈 전 필수)

직원 오픈 전 필수 — 입구 `inbound-cidrs` 를 직원 대역으로, admin 콘솔은 `DEV_LOGIN_ENABLED` 그대로 두고 관리자 IP/VPN 전용으로 네트워크 보호, ALB 잠금 검증. HTTPS 없는 배포는 IP 허용목록이 유일한 보호막.
→ **[ops/8-S-hardening.md](ops/8-S-hardening.md)**

---

### 8-N. Bedrock 을 NAT 대신 VPC Endpoint(PrivateLink)로

`US-04` 필수 — Bedrock·STS 를 NAT 대신 VPC Endpoint(PrivateLink)로(`enable-bedrock-vpce.sh`). 신규 설치는 이미 포함, 그 전에 만든 VPC 만 대상. 적용 후 gateway-proxy 재시작 필수.
→ **[ops/8-N-vpc-endpoint.md](ops/8-N-vpc-endpoint.md)**

---

### 8-E. EKS 버전 업그레이드 (1.31 → 1.34)

`US-05` 필수 — EKS 1.31 → 1.34 를 **1단계씩 3회 apply**, 단계마다 전 네임스페이스 파드 재시작. plan 에 클러스터 1 + add-on 3 외 diff 가 있으면 중단.
→ **[ops/8-E-eks-upgrade.md](ops/8-E-eks-upgrade.md)**

---

### 8-H. ALB HTTPS — 커스텀 도메인 + ACM 인증서 (방식 A → B)

`US-06` 선택(POC) · 운영이면 강력 권장 — 도메인 + ACM 으로 ALB 3개를 https:443 으로. `https-env.sh` 로 값 추출 → `10-switch-https.sh` → `install-eks.sh` → `11-route53-cname.sh`. CloudFront(US-02 `03`)는 폐기. 2026-08-16 US 배포에서 종단 검증(Mac·Windows Claude Code/Cowork).
→ **[ops/8-H-alb-https.md](ops/8-H-alb-https.md)**

---

### 8-I. admin ALB 2개를 internal 로 — 고객사 최종형

`US-07` 선택 — 전제 S2S VPN. values 주석 2곳 해제 → `install-eks.sh`(ALB 재생성) → admin SG·CNAME 교체. VPN 없이 적용하면 VK 발급이 끊겨 게이트웨이 사용 불가. terraform 무변경. 신규 설치는 `US-01` 때 values 로 포함.
→ **[ops/8-I-admin-internal.md](ops/8-I-admin-internal.md)**

---

### 8-P. dev → prod 승격 — 별도 계정에 prod 스택 신설

`US-08` prod 는 dev 의 스위치가 아니라 **별도 계정에 나란히 서는 별개 스택**(tfstate·EKS·Aurora·Valkey·Cognito·ECR 전부 새로).
`environment = "prod"` 한 줄로 HA 사이징(Aurora r7g ×2 · Valkey 3 shard × 3 · NAT ×2)이 켜지고,
처음부터 **https(US-06) + admin ALB internal(US-07) + VPN** 형태로 세운다. dev 에서 가져오는 것은 tfvars 원본과 도메인 위임뿐.

→ **[ops/8-P-prod.md](ops/8-P-prod.md)** — 준비(계정·도메인·tfvars) → terraform → 이미지 → values → https/internal
→ Cognito·SQL → Client VPN → Cowork Windows 설치기 → 검증 → teardown. 검증 계정 실측(2026-08-28~29).

---

### 8-L. Admin UI Cognito 로그인 활성화 (dev-login 대체)

`US-10` 선택(운영이면 강력 권장) — admin-ui 에 이메일/비밀번호로 로그인하는 실제 Cognito 로그인 폼 추가. 이미지 재빌드 → `setup-admin-ui-login.sh`(세션 서명 키 발급 + DB/Secret 반영) → `install-eks.sh` → 확인 후 `global.devLoginEnabled: false` 로 dev-login 우회 차단.
→ **[ops/8-L-admin-ui-login.md](ops/8-L-admin-ui-login.md)**

---

### 8-W. Notification 발송 채널 변경

메일을 실제로 보내려면 `notificationWorker.email.provider`를 `mock`에서 `internal_api`·`smtp`·`ses`로 전환한다. 값 파일을 직접 고치지 않고 `deployment/scripts/set-notification-provider.sh`를 사용할 수 있다. `ses` 선택 시 IAM/IRSA 는 `update-scripts/08-setup-notification-ses-irsa.sh`로 자동 설정한다.

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/set-notification-provider.sh dev internal_api
bash deployment/scripts/install-eks.sh dev
```

상세 절차·제약·수동 설정 → **[ops/8-W-notifications.md](ops/8-W-notifications.md)**

---

### 8-T. teardown (과금 중단 · 초기화)

> 🔴 **되돌릴 수 없다.** 아래는 dev 스택 전체(EKS·Aurora·시크릿·웹서치)를 파기한다. 데이터가 필요하면 **먼저 Aurora 스냅샷**을 뜬다.

```bash
helm uninstall llm-gateway -n llm-gateway
kubectl delete namespace llm-gateway
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev && terraform destroy
# Terraform이 안 지우는 잔여물: ECR 이미지, Secrets Manager(/llm-gateway/dev/*),
#   AgentCore WebSearch gateway(provision_agentcore_websearch.py teardown), tfstate(S3/DynamoDB)
python3 ~/awsome-ai-gateway/deployment/scripts/provision_agentcore_websearch.py teardown  # (REGION/GW_NAME env 동일)
```

---

### 8-Z. 토큰 TTL 조절

토큰 수명 — refresh 7일·access/id 1시간(Cognito, terraform) · VK 1시간(admin-api env). 바꾸는 이유는 client-setup-explained 「만료 조건」.
→ **[ops/8-Z-token-ttl.md](ops/8-Z-token-ttl.md)**

---

### 8-X. 멀티계정 확장 — claude-code 를 별도 계정 Bedrock 으로

이 배포는 **단일 계정**이라 claude-code 가 이 계정 Bedrock 을 직접 쓴다(§4-3 에서 `account_role_arn=NULL` = in-account). 나중에 claude-code 트래픽을 **다른 AWS 계정**의 Bedrock 으로 보내려면(계정 분리·비용 격리·규제 등) 아래 3가지를 세팅하고 §4-3 SQL 을 **반대로** 돌리면 된다.

> **게이트웨이 코드 변경은 없다.** cross-account 분기는 이미 구현돼 있고(`BedrockAccountClientProvider` + `client_resolver`), **DB 의** `account_role_arn` **한 컬럼이 스위치**다 — 값이 있으면 그 역할을 AssumeRole, 비어 있으면 in-account(`main.py:181`). §4-3 이 그 스위치를 끈 것뿐이다.

**① 대상 계정에 역할 만들기** (terraform / 콘솔) — 게이트웨이 계정이 assume 할 수 있는 역할:

- **trust policy**: 게이트웨이 파드의 IRSA 역할(`llm-gateway-dev-gateway-proxy-bedrock`)이 `sts:AssumeRole` 하도록 허용 + `ExternalId` **조건**(confused-deputy 방지).
- **권한**: `bedrock:InvokeModel` 등 대상 계정 Bedrock 호출 권한.

**② 게이트웨이 파드 IRSA 에** `sts:AssumeRole` **추가** — 지금은 자기 계정 Bedrock 만 부른다. 다른 계정 역할을 assume 하려면 그 권한이 IRSA 정책에 있어야 한다(대상 = ①의 역할 ARN).

**③ DB 한 줄 — §4-3 을 되돌린다** (in-account → cross-account):

▶ **실행** · 배포 EC2 (§4-1 처럼 psql 파드로)

```sql
UPDATE model.routing_profiles
   SET account_role_arn = 'arn:aws:iam::<대상계정>:role/<역할>',   -- ①에서 만든 역할
       external_id      = '<ExternalId>',                        -- ①의 trust 조건과 동일
       region           = '<대상 리전>'                            -- 대상 계정 Bedrock 리전
 WHERE client = 'claude-code';
```

그다음 **Redis 캐시 플러시** 필수 — `routing_profiles` 를 psql 로 직접 고치면 캐시(`routing_profile:claude-code`, TTL 5분)가 안 지워져 최대 5분 지연된다. admin-api 경로(있으면)로 바꾸거나 캐시 키를 지운다.

> **롤백은 즉시**: `account_role_arn`·`external_id` 를 다시 `NULL` 로 → 다음 요청부터 in-account 복귀(무배포).
>
> **codex·cowork 도 같은 구조**다(원 설계: claude-code·codex·cowork 를 각각 다른 계정으로). 이 배포는 둘을 out-of-scope(§0)로 두고 [§4-2 (D)](install-guide.md#4-2-3모델-alias-를-us-geo-프로파일로-sonnet-5는-신규)에서 INACTIVE 처리했다. 그것들까지 멀티계정으로 살리려면 클라이언트마다 ①②③ 을 반복한다.
