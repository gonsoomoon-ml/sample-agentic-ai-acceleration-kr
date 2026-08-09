# US LLM Gateway 아키텍처 (Claude Code on Amazon Bedrock)

> 이 배포(단일 계정 · us-west-2)의 실제 구성을 그림 한 장으로 기록한다. 개념도가 아니라 **배포된 terraform·helm 소스와 대조해 확정**한 그림이다(2026-07-20 기준). 범위 합의는 [prd.md](prd.md), 설치 절차는 [install-guide.md](install-guide.md), 설치 후 운영은 [operations.md](operations.md).

## 전체 그림

```text
Claude Code on Amazon Bedrock — US LLM Gateway  (single AWS account · us-west-2)
clients: Claude Code (Mac/Windows/Linux) · inference: US Geo (us.anthropic.*)

                                  ┌─ AWS Cloud ────────────────────────────────────────────────────┐
                                  │ ┌─ Region: us-west-2 ────────────────────────────────────────┐ │
┌─ Office / Employee PC ───────┐  │ │                                                            │ │
│ User — Claude Code           │  │ │   ┌─ Amazon Cognito ────────────────────────────┐          │ │
│ (1) SSO login (OIDC) ────────┼──┼─┼──▶│ user pool · Hosted UI · OIDC issuer/token   │          │ │
│   Mac · Windows · Linux      │  │ │   └─────────────────────────────────────────────┘          │ │
│  gateway-cli + api-key-helper│  │ │                                                            │ │
│                              │  │ │ ┌─ VPC · ALB = IP allowlist (inbound-cidrs) ─────────────┐ │ │
│                              │  │ │ │ ┌───────────────────┐  ┌─ EKS on Fargate ────────────┐ │ │ │
│ (2) get Virtual Key ─────────┼──┼─┼─┼▶│ ALB — Admin API   │─▶│ · admin-api                 │ │ │ │
│                              │  │ │ │ └───────────────────┘  │     (OIDC verify · VK issue)│ │ │ │
│                              │  │ │ │                        │ · scheduler                 │ │ │ │
│                              │  │ │ │ ┌───────────────────┐  │                             │ │ │ │
│ (3) call LLM · Bearer VK ────┼──┼─┼─┼▶│ ALB — Gateway     │─▶│ · gateway-proxy             │ │ │ │
│                              │  │ │ │ └───────────────────┘  │     (auth·ratelimit·budget) │ │ │ │
│                              │  │ │ │                        │ · cost-recorder-worker      │ │ │ │
│ Admin — browser              │  │ │ │ ┌───────────────────┐  │ · notification-worker       │ │ │ │
│ (A) admin console ───────────┼──┼─┼─┼▶│ ALB — Admin UI    │─▶│ · admin-ui                  │ │ │ │
└──────────────────────────────┘  │ │ │ └───────────────────┘  │   + migration Job (install) │ │ │ │
                                  │ │ │                        └─┬────┬───────┬───────────┬──┘ │ │ │
                                  │ │ │           ┌──────────────┼────┘       │           │    │ │ │
                                  │ │ │ ┌─────────▼───────────┐  │ ┌──────────▼────────┐  │    │ │ │
                                  │ │ │ │ Aurora PostgreSQL   │  │ │ ElastiCache Valkey│  │    │ │ │
                                  │ │ │ │ (via RDS Proxy)     │  │ │ VK·rate·budget Lua│  │    │ │ │
                                  │ │ │ └─────────────────────┘  │ └───────────────────┘  │    │ │ │
                                  │ │ └──────────────────────────┼────────────────────────┼────┘ │ │
                                  │ │                            │   (via VPC endpoint)   │      │ │
                                  │ │                            │  ┌─ Amazon Bedrock ────▼────┐ │ │
                                  │ │                            │  │ bedrock-runtime us-west-2│ │ │
                                  │ │                            │  │ US Geo: us.anthropic.*   │ │ │
                                  │ │                            │  └────────────┬─────────────┘ │ │
                                  │ └────────────────────────────┼───────────────┼───────────────┘ │
                                  │             ┌─(4) web search ┘               │                 │
                                  │             ▼                                ▼                 │
                                  │ ┌─ Region: us-east-1 ───┐   ┌─ US Geo (us.anthropic.*) ──────┐ │
                                  │ │ AgentCore Gateway     │   │ us-east-1·us-east-2·us-west-2  │ │
                                  │ │ └─▶ Web Search        │   │ Opus 4.8·Sonnet 5·Haiku 4.5    │ │
                                  │ │ (managed · SigV4/IRSA)│   │ (Anthropic models on Bedrock)  │ │
                                  │ └───────────────────────┘   └────────────────────────────────┘ │
                                  │  CloudWatch · CloudTrail · Secrets Manager (ESO) · IAM (IRSA)  │
                                  └────────────────────────────────────────────────────────────────┘
```

> 박스 위치는 표기용이다(실제 배치 의미 없음). 계정 ID·리소스 이름은 의도적으로 넣지 않았다 — 설치마다 달라지는 값은 [install-guide.md](install-guide.md)의 각 절이 terraform output 에서 얻는다.



## 요청 흐름 5개


| #   | 흐름               | 경로                                                                                                                                                                                                                   | 상세 절                                                                                                                   |
| --- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| (1) | SSO login        | `gateway-cli login` → **Cognito Hosted UI**(OIDC) → 브라우저 로그인 → 토큰 수령. AWS 계정/자격증명 불필요 — 직원은 Cognito 사용자만 있으면 된다                                                                                                      | [client-install.md](client-install.md), [client-setup-explained.md](client-setup-explained.md)                         |
| (2) | get Virtual Key  | `api-key-helper`가 OIDC 토큰 → **ALB — Admin API**(IP 허용목록) → admin-api 가 토큰 검증 후 **VK 발급**(Aurora 저장 · Valkey 캐시). Claude Code 는 이 VK 를 `apiKeyHelper` 로 자동 사용                                                         | [install-guide.md §6-0](install-guide.md#6-0-linux-배포-ec2--관리자가-먼저-익힌다)                                                 |
| (3) | call LLM         | Claude Code 가 `Bearer VK` → **ALB — Gateway** → gateway-proxy 미들웨어(auth → rate limit → budget) → **VPC endpoint(PrivateLink) 이그레스** → `bedrock-runtime`(us-west-2) → **US Geo 프로파일**(`us.anthropic.`*)이 us-east-1/us-east-2/us-west-2 로 분산 | [install-guide.md §4](install-guide.md#4-claude-code--bedrock-runtime--us-geo-프로파일-배선-us-핵심)                             |
| (4) | web search (서버측) | gateway-proxy → **NAT 이그레스** → **AgentCore Gateway**(us-east-1, SigV4/IRSA) → Web Search 관리형 커넥터. 클라이언트는 검색 사실을 모른다 — 게이트웨이가 툴 루프를 서버에서 돈다. 클라이언트별 ON/OFF = `routing_profiles.web_search_enabled`                                   | [install-guide.md §5](install-guide.md#5-서버측-web-search-us-east-1), [web-search-explained.md](web-search-explained.md) |
| (A) | admin console    | 관리자 브라우저 → **ALB — Admin UI**(IP 허용목록) → admin-ui. 예산·모델·라우팅·앱별 웹서치 토글 관리                                                                                                                                            | [install-guide.md §3-8](install-guide.md#3-8-cognito-온보딩--스모크)                                                          |




## 구성 요소 메모

- **입구 3개가 전부 ALB +** `inbound-cidrs`**(IP 허용목록)** — Gateway·Admin API·Admin UI 모두 잠근다. CloudFront·전용선 없음. 사내 VPN 은 목적지 리전별로 출구 IP 가 달라지므로, 허용할 IP 는 반드시 대상 리전 호스트에서 잰다 → [install-guide.md §5-3](install-guide.md#5-3-web-search-선택--토글).
- **EKS on Fargate — 파드 6종 + migration Job**: `gateway-proxy`(데이터 플레인 · VK 인증 후 Bedrock 프록시), `admin-api`(컨트롤 플레인 · OIDC 검증·VK 발급·CRUD), `admin-ui`(대시보드), `scheduler`(집계·만료 정리), `cost-recorder-worker`(비용 스트림 → Aurora), `notification-worker`(예산 임계값 알림). migration Job 은 install 시 pre-install hook 으로 스키마 반영.
- **Aurora PostgreSQL (RDS Proxy 경유)** = 정본 저장소(VK·팀·예산·모델 alias·routing·usage). **ElastiCache Valkey** = VK 캐시 + rate limit·budget 의 원자적 체크(Lua). 그래서 VK 발급 직후엔 통하는데 예산 변경 반영엔 캐시 TTL(~3–5분)이 걸린다.
- **추론이 US Geo 인 이유**: us-west-2 는 대상 3모델(Opus 4.8 · Sonnet 5 · Haiku 4.5)의 In-Region 추론 미지원 → `us.anthropic.`* 프로파일 필수. Geo 가 3리전으로 라우팅하므로 IAM 에는 (a) inference-profile ARN 과 (b) 3리전 foundation-model ARN 이 **둘 다** 필요하다.
- **web search 가 us-east-1 인 이유**: Bedrock AgentCore 의 Web Search 관리형 커넥터가 us-east-1 전용 → gateway-proxy(us-west-2)가 cross-region 으로 호출한다. 인증은 Cognito 가 아니라 **IAM(SigV4, IRSA)**.
- **이그레스 = VPC endpoint(Bedrock·STS) + NAT(나머지)** — terraform 이 `bedrock-runtime`·`bedrock`·`sts` Interface endpoint(PrivateLink, private DNS)를 만들어 추론(US Geo 포함)과 STS(IRSA) 호출은 NAT 를 타지 않는다. AgentCore(us-east-1 web search)·Cognito·Secrets Manager·CloudWatch·이미지 pull 등 나머지 이그레스는 여전히 파드 → NAT → AWS 공인 엔드포인트라 **NAT gateway 는 계속 필요하다**. 2026-07-29 upstream 리베이스(PR #32)로 유입 — 그 전에 apply 한 스택은 다음 `terraform apply` 때 endpoint 3종(+전용 SG)이 추가 생성된다.
- **운영 계층**: CloudWatch(웹서치 실동작 검증 로그 포함) · CloudTrail · Secrets Manager + External Secrets(ESO, 파드 시크릿 주입) · IAM IRSA(파드별 최소 권한) · ECR(이미지 6종).



