# US LLM Gateway (Claude Code)

## 목적

고객 (US) 에게 제공할 LLM-Gateway 설치 및 전반적인 운영을 위한 교육을 제공.

## 설치 작업 환경 (아래는 작업자의 Laptop 임.)

- VS Code 또는 IDE (예: Cursor) — 작업자 Laptop (Mac or Windows)
- IDE 는 아래 Deployment EC2 에 SSH 로 연결하여 Terminal 및 Coding Tool(Claude Code on Amazon Bedrock)을 이용한다. Coding Tool 은 설치 과정의 트러블슈팅 등에 사용한다.

## Deployment EC2

- Region: **us-west-2**
- OS/Type: **Ubuntu 26.04 LTS (x86_64), t3.xlarge 이상, gp3 50GB 이상** (x86_64·Ubuntu 는 필수 
- 권한: EC2 에 **IAM instance role** 을 부여하고, 그 role 에 `AdministratorAccess` + `AmazonSSMManagedInstanceCore` 정책을 붙인다.
  - 이 role 이 VPC·EKS·RDS·ElastiCache·Cognito·IAM·ECR·Secrets Manager·bedrock-agentcore 를 다룬다.
  - IAM User 액세스 키 대신 **instance role**(임시 자격증명 자동 순환) 사용
  - Administrative Access 는 첫 설치 편의를 위한 권장값 — 설치 대상 서비스 폭이 넓어 최소권한을 일일이 짜면 배포 중 에러가 잦음.
- 설치 도구 (EC2 1회, 권장 버전 / 이 배포에서 검증된 실측값):
  - aws-cli **v2** (검증 2.35.24)
  - terraform **≥ 1.9** (repo 요구 `required_version >= 1.9.0`, 검증 1.15.8)
  - kubectl **EKS 버전에 맞춤** (bootstrap 이 1.30.9 설치 — EKS 1.30까지 호환)
  - helm **3.x** (≥ 3.14; 설치 스크립트가 v3=`--atomic` 자동 감지, 검증 3.21.3)
  - docker **≥ 24** (검증 29.1.3) · buildx 플러그인 필요(bootstrap 이 설치)
  - jq **≥ 1.6** (검증 1.8)
  - psql (PostgreSQL client) **16.x** (Aurora PostgreSQL 16.11 에 맞춤, 검증 16.14)
- Coding Tool: **Claude Code on Amazon Bedrock**

## Installation

- Product
  - **Claude Code on Amazon Bedrock**
- 운영 Account
  - AWS **단일 계정 1개**, 관리자 권한.
  - **배포 실행 주체 = §Deployment EC2 의 instance role**.
  - 콘솔 작업(Bedrock Model access 승인 등)은 별도 **관리자 로그인**(SSO 또는 IAM User)으로 수행.
- 추론 방식
  - `bedrock-runtime` **+ US Geo 추론 프로파일**(`us.anthropic.`*). us-west-2는 이 3모델을bedrock-runtime의 Geo 프로파일로 호출한다(us-east-1/us-east-2/us-west-2 내 라우팅).
- Model Access  (us-west-2) 
  - **Opus 4.8** — Geo ID: `us.anthropic.claude-opus-4-8`
  - **Sonnet 5** — Geo ID: `us.anthropic.claude-sonnet-5`
  - **Haiku 4.5** — Geo ID: `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- Region
  - 인프라·추론 = **us-west-2**. 단 추론은 US Geo라 us-east-1/us-east-2/us-west-2 로 분산(us-west-2 고정 아님).
  - **Server-Side Web Search 만 us-east-1**(관리형 커넥터가 us-east-1 전용) — cross-region 호출(awsome 코드 지원).
- Main Feature
  - AWS Cognito OIDC 인증, Virtual Key 사용
  - **Server-Side Web Search**: AgentCore Bedrock Managed Web Search (**us-east-1** 관리형 커넥터, cross-region)
- 보안 전제 
  - 입구 = **VPN/IP 제한**(`inbound-cidrs`) — HTTPS 도메인 미설정, IP 제한이 보호막.

### Out of scope (이번 배포에서 미사용)

- CoWork, Codex, Cross-account 2번째 계정 (예: Cowork, Codex), Admin-chat-agent(BI 챗), ADFS(SSO) 연동
- 위의 범위는 논의에 따라서 변경될 수 있습니다.

## Claude Code Client Distribution

- **Windows**
  - **gateway-cli v2.0** (.exe 모듈이 아니라 **수동 설치 버전**)
- **Mac** / Linux
  - **gateway-cli v2.0**

## Base Repo

- `sample-agentic-ai-acceleration-kr` 
  - [https://github.com/aws-samples/sample-agentic-ai-acceleration-kr](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr)
  - ⚠️ **선행 의존성**: 배포 전 `us/deploy-fixes` 브랜치(커밋 3개 = install-eks 픽스 + `mantle_regions` 변수화 + `bootstrap-ec2.sh`) 필요. 공개 fork `[gonsoomoon-ml/sample-agentic-ai-acceleration-kr](https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr)` 에서 그 브랜치를 clone 하면 된다**upstream(aws-samples) PR 머지되면 fork 없이 base repo  를 그냥 clone 하여 사용**

