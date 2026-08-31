# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

variable "project" {
  type    = string
  default = "llm-gateway"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
}

variable "azs" {
  type    = list(string)
  default = ["ap-northeast-2a", "ap-northeast-2c"] # prod는 multi-AZ (HA)
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16" # prod 환경 — 기존 새 dev(10.30)와 분리
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.40.1.0/24", "10.40.2.0/24"]
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.40.101.0/24", "10.40.102.0/24"]
}

variable "database_subnet_cidrs" {
  type    = list(string)
  default = ["10.40.201.0/24", "10.40.202.0/24"]
}

variable "elasticache_subnet_cidrs" {
  type    = list(string)
  default = ["10.40.211.0/24", "10.40.212.0/24"]
}

variable "eks_cluster_version" {
  # AWS EKS 는 minor version downgrade 불가. 한번 apply 된 버전 이상으로만 올릴 수 있고,
  # 올릴 때도 마이너 1단계씩만 가능하다(1.31 → 1.34 같은 점프 불가).
  # 기본값은 기존 배포가 apply 때 흔들리지 않도록 그대로 둔다 — 신규 설치 권장 버전은
  # terraform.tfvars.example 에 eks_addon_versions 와 한 쌍으로 적어 두었다.
  type    = string
  default = "1.34"
}

variable "eks_addon_versions" {
  # EKS add-on(coredns·kube-proxy·vpc-cni) 버전. null 이면 모듈 기본값(= eks_cluster_version
  # 기본값과 호환) 사용. eks_cluster_version 을 바꾸는 설치·업그레이드에서는 반드시 같이
  # 지정한다 — 한 버전의 addon 은 다른 버전에 존재하지 않아 apply 가 실패한다.
  # 호환값 조회:
  #   aws eks describe-addon-versions --addon-name coredns --kubernetes-version <ver> \
  #     --query 'addons[0].addonVersions[?compatibilities[0].defaultVersion==`true`].addonVersion'
  type = object({
    coredns    = string
    kube_proxy = string
    vpc_cni    = string
  })
  default = null
}

variable "aurora_engine_version" {
  type    = string
  default = "16.11"
}

variable "aurora_prod_instance_class" {
  # prod는 provisioned 인스턴스 (모듈이 prod일 때 serverlessv2_scaling_configuration={}로 보내고
  # instance_class를 var.prod_instance_class 그대로 사용 — 따라서 db.serverless 로 두면 모순).
  # 옛 prod에서 사용하던 동일 인스턴스 클래스. 부하 따라 r6g.xlarge 등으로 조정 가능.
  type    = string
  default = "db.r7g.large"
}

variable "elasticache_prod_node_type" {
  # prod는 큰 노드 (옛 prod 동일).
  type    = string
  default = "cache.r7g.large"
}

# ElastiCache prod HA(deepdive Q50 Phase4). 샤드당 replica 수 — 2 면 primary failover
# 시 zero-redundancy 윈도우 제거 + read 스케일(3샤드×3노드=9). 1 이면 기존(6노드).
# **비용 영향(+50% 노드시간)** — 운영 예산 결정 후 tfvars 로 명시. 모듈 기본 1 과 달리
# prod 권장값 2 를 이 env 기본으로 둔다(plan 으로 영향 확인 후 apply).
variable "elasticache_prod_replicas_per_node_group" {
  type    = number
  default = 2
}

# prod 커스텀 cluster 파라미터그룹(maxmemory-policy/reserved-memory). true 면 메모리압박
# noeviction OOM-거부 회피 + failover 헤드룸. 기존(false)은 AWS default.valkey7.cluster.on.
variable "elasticache_prod_enable_custom_param_group" {
  type    = bool
  default = true
}

variable "enable_rds_proxy" {
  description = "Aurora 앞단에 RDS Proxy 배치 (connection pool). dev/prod 모두 기본 true 로 통일 — gateway user 인증을 Terraform 이 자동 관리하므로 Proxy 기본 사용 가능. 꼭 필요한 경우만 `-var enable_rds_proxy=false` 로 내릴 것."
  type        = bool
  default     = true
}

variable "application_namespace" {
  type    = string
  default = "llm-gateway"
}

# ─── Cognito (OIDC IDP) ───
variable "cognito_domain_suffix" {
  description = "Hosted UI 도메인 suffix. 최종: {project}-{env}-{suffix}.auth.{region}.amazoncognito.com (전 세계 unique). 빈 값이면 account_id 로 자동 생성(vanilla-auth-<account_id>) — 신규 계정 이식 시 별도 지정 불필요."
  type        = string
  default     = ""
}

variable "cognito_callback_urls" {
  description = "OIDC redirect URI 화이트리스트. gateway-cli 의 PKCE callback 용. 사용자 PC 의 localhost 포트."
  type        = list(string)
  default = [
    "http://localhost:8090/callback",
    "http://localhost:8091/callback",
    "http://localhost:8092/callback",
  ]
}

variable "cognito_logout_urls" {
  description = "OIDC logout redirect URI"
  type        = list(string)
  default = [
    "http://localhost:8090/logout",
    "http://localhost:8091/logout",
    "http://localhost:8092/logout",
  ]
}

variable "cognito_groups" {
  description = "User groups. Claude_<team> 은 Default Department 하위 팀, Claude_<dept>_<team> 은 dept 자동 생성 후 team 매핑, ClaudeAdmin 은 admin 부트스트랩. team_leader 는 Cognito 그룹이 아니라 admin-ui 에서 지정(PUT /admin/teams/{id}/leader). prod는 dev 검증에서 결정된 깨끗한 셋만 — 빈 팀 잔재(aws-test, S/W-Culture-Office) 미포함."
  type        = list(string)
  default     = ["Claude_AWS-AI-Specialist", "ClaudeAdmin"]
}

variable "bedrock_allowed_model_arns" {
  # 애플리케이션은 `global.anthropic.*` (cross-region inference profile) 로 호출.
  # IAM 은 inference-profile + 호출될 foundation-model 양쪽에 InvokeModel 허용 필요.
  type = list(string)
  default = [
    # Foundation models (실제 추론이 실행되는 리소스)
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-4-*",
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-*",
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-*",
    # Global cross-region inference profiles (application 이 호출하는 엔트리포인트)
    "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*",
    "arn:aws:bedrock:*:*:inference-profile/global.anthropic.claude-*",
    # APAC cross-region inference profile (예비, ap-northeast-2 전용)
    "arn:aws:bedrock:ap-northeast-2::inference-profile/apac.anthropic.claude-*",
  ]
}

variable "mantle_regions" {
  # gateway-proxy IRSA 가 in-account Bedrock Mantle 을 호출할 수 있는 리전.
  # 기본 = Tokyo(Claude Code/Cowork) + Ohio(Codex). US 단일계정 배포는 ["us-east-1"].
  type     = list(string)
  nullable = false
  default  = ["ap-northeast-1", "us-east-2"]
}

variable "eks_access_entries" {
  type    = any
  default = {}
}

variable "cowork_role_arn" {
  # Cowork cross-account Mantle role (905, Tokyo Opus 4.8). gateway-proxy AssumeRole into it.
  # Must match model.routing_profiles.account_role_arn for client=cowork (migration 0009).
  # The 905 role's trust must allow this env's gateway-proxy IRSA + sts:ExternalId=cowork-bedrock.
  type    = string
  default = "arn:aws:iam::234567890123:role/llm-gateway-cowork-bedrock"
}

variable "claude_code_374_role_arn" {
  # Claude Code cross-account Bedrock NATIVE role (374). gateway-proxy AssumeRole into it,
  # builds a 374 bedrock-runtime client (boto3 invoke_model). Must match
  # model.routing_profiles.account_role_arn for client=claude-code (migration 0022).
  # The 374 role trust must allow this env's gateway-proxy IRSA + sts:ExternalId=claude-code-bedrock.
  type    = string
  default = "arn:aws:iam::345678901234:role/llm-gateway-claude-code-bedrock"
}

variable "tags" {
  type    = map(string)
  default = {}
}

# ─── admin-chat-agent (Phase 1 부트스트랩 — 활성 시 ECR/S3/IAM 만 생성) ───
variable "enable_chat_agent" {
  description = "admin-chat-agent 인프라 (ECR + S3 staging + IAM + KMS) 생성 여부"
  type        = bool
  default     = false
}

# ─── admin-chat-agent BI tool Lambdas (query_db / get_schema) ───
# enable_chat_agent=true 가 선행 조건 (같은 모듈에 추가됨). dev 와 동일 선언 — tfvars 를 dev 에서 복사해 그대로 쓰기 위함.
variable "enable_chat_db_tools" {
  description = "admin-chat-agent 의 query_db/get_schema Lambda + reader secret + SG 생성 여부"
  type        = bool
  default     = false
}
