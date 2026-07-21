# US LLM Gateway 설치 가이드 (Claude Code on Amazon Bedrock)

> **목적**: 시스템 운영자가 **직접 명령을 실행**해 단일 계정에 게이트웨이를 설치합니다. 시스템 관리자가 LLM-Gateway 의 설치와 동시에 이 시스템을 배우기 위해서, 명령어의  복사-실행용으로 많이 구성했습니다.  
>
> **코드베이스**: 저장소 `sample-agentic-ai-acceleration-kr`([aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr)) 의 `projects/awsome-ai-gateway` 를 사용합니다.
>
> 🔴 **단, clone 은 원본이 아니라 [fork](https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr) 의** `us/deploy-fixes` **브랜치**에서 합니다([§1-4](install-guide.md#1-4-git-저장소-세팅)). 원본에 아직 없는 **배포 픽스**(안 하면 `terraform apply`·이미지 빌드가 실패)와 **벤더 버그 픽스**(안 하면 클라이언트가 **에러 없이 게이트웨이를 우회**)가 들어 있습니다. US 특화 소스 편집은 **0** 이고, 이 픽스들은 전부 **upstream PR 후보**입니다([§2](install-guide.md#2-배포-전-코드-준비-us-특화-소스-편집-0)) — 머지되면 fork 없이 원본을 그냥 clone 하면 됩니다.

---



## 0. 이번 배포의 범위 (확정)


| 항목     | 값                                                                                                           |
| ------ | ----------------------------------------------------------------------------------------------------------- |
| AWS 계정 | **단일 계정**, Administrative Access                                                                            |
| Region | **us-west-2** (인프라·추론) — 추론은 US Geo라 us-east-1/us-east-2/us-west-2 분산                                       |
| 클라이언트  | **Claude Code** (Mac, Windows, Linux)                                                                       |
| 추론 백엔드 | `bedrock-runtime` **+ US Geo 추론 프로파일** (`us.anthropic.`*) — us-west-2 In-Region 미지원이라 Geo 사용. **Mantle 아님** |
| 모델     | **Opus 4.8 · Sonnet 5 · Haiku 4.5** (Geo ID `us.anthropic.`*)                                               |
| 핵심 기능  | **서버측 Web Search** (AgentCore 관리형 커넥터, **us-east-1 전용 → cross-region 호출**)                                  |
| 보안(입구) | **IP 제한**(`inbound-cidrs`)                                                                                  |


---



## 설치 흐름 한눈에

```
준비 → terraform(인프라) → install-eks(앱) → 라우팅·웹서치 → 클라이언트·보안
```

**A. 준비 (1회) — ⏱️ ~1시간**

1. AWS Account 를 준비 - AWS Console 에 Admin 으로 접속할 IAM User 준비
2. 설치 작업 환경 (작업자 Laptop)
3. 명령 돌릴 **작업 서버(EC2)를 만든다**(IAM 역할 부여까지. 도구 설치는 6번) … [§1-2](install-guide.md#1-2-배포-작업용-ec2-deployment-ec2-us-west-2)
4. 계정에서 Bedrock **Claude 3개의 모델을 켠다**(안 켜면 403) — 콘솔 Bedrock ▸ Model catalog(us-west-2) … [§1-3](install-guide.md#1-3-bedrock-모델-액세스-us-west-2-먼저-확인-대개-불필요)
5. **게이트웨이 코드를 받는다** — fork 에서 `us/deploy-fixes` 브랜치를 clone(픽스 포함) … [§1-4](install-guide.md#1-4-git-저장소-세팅) (내용 설명 [§2-1](install-guide.md#2-배포-전-코드-준비-us-특화-소스-편집-0))
6. EC2 에 **도구를 설치한다**(스크립트가 5번 clone 안에 있어 순서가 뒤) — `bash deployment/scripts/bootstrap-ec2.sh` … [§2-2](install-guide.md#2-2-배포-ec2-도구-설치-1-4-clone-이후에-실행)

**B. 인프라 프로비저닝 (terraform) — ⏱️ ~2시간** 

1. terraform **상태 저장소(S3/DynamoDB)를 만든다** — `bootstrap-tfstate.sh` … [§3-1](install-guide.md#3-1-tfstate-창고)
2. **이 배포의 값(리전·역할 ARN·모델 ARN)을 지정** — `terraform.tfvars` 작성 … [§3-2](install-guide.md#3-2-tfvars-채우기)
3. **VPC·EKS·Aurora·Redis·Cognito 인프라를 실제 생성** — `terraform apply` ⏳ **30분** … [§3-3](install-guide.md#3-3-terraform-apply-인프라-약-30분)

**C. 앱 배포 (build → install → 검증) — ⏱️ ~2시간** 

1. 앱이 쓸 **암호키·DB/Redis 비번을 Secrets Manager에 넣는다** … [§3-4](install-guide.md#3-4-시크릿-손으로-만드는-건-2개-app-redis)
2. **서비스 컨테이너를 빌드해 ECR에 올린다** ⏳ **20분** … [§3-5](install-guide.md#3-5-이미지-빌드-ecr)
3. terraform으로 못 구하는 **조직 값(이메일·Cognito)을 채운다** — values 편집 … [§3-6](install-guide.md#3-6-values-org-값만-채우기-web-search-키)
4. **앱(파드)을 클러스터에 배포** — `install-eks.sh dev` … [§3-7](install-guide.md#3-7-설치-실행)
5. **첫 운영자 계정 생성 + 동작 확인** — Cognito 온보딩 + smoke test … [§3-8](install-guide.md#3-8-cognito-온보딩-스모크)

**D. 배선·기능 — ⏱️ ~30분**

1. **Claude Code 를 US Geo 프로파일로 연결** — Aurora 에 alias·routing SQL … [§4](install-guide.md#4-claude-code-bedrock-runtime-us-geo-프로파일-배선-us-핵심)
2. **서버측 웹검색 게이트웨이를 만들고 URL 연결** — 프로비저닝 → values → install 재실행 … [§5](install-guide.md#5-서버측-web-search-us-east-1)

**E. 클라이언트·오픈 — ⏱️ ~35분**

1. **직원 PC 가 게이트웨이 통해 쓰게 설정** — Claude Code 배포 … [§6](install-guide.md#6-클라이언트-설치-claude-code-awsome-gateway-cli) (운영자는 §6-0 까지, **직원 PC 는 [client-install.md](client-install.md)**)
2. **IP 제한·로그인 우회 차단 후 재배포** — 보안 하드닝(직원 오픈 전 필수) … [operations.md §8-S](operations.md#8-s-배포-후-보안-하드닝-직원-오픈-전-필수)

> ⏱️ 대기의 대부분은 **9번**(terraform apply)과 **11번**(이미지 빌드)이다.

---



## 📍 문서 지도


| 문서                                                                 | 무엇                                                                             | 언제 본다                         |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------ | ----------------------------- |
| **README.md** (이 문서)                                               | 범위 · 설치 흐름 · 문서 지도                                                             | **시작할 때 한 번**                 |
| **[install-guide.md](install-guide.md)**                           | **§1~§6-0 실행 런북** (운영자용. §7 보안은 operations.md)                              | **설치하는 4시간 내내 — 여기만 본다**      |
| **[client-install.md](client-install.md)**                         | **§6-1~§6-3 직원 PC 설치** (macOS · Windows) — 직원에게 그대로 전달                    | 직원 온보딩할 때 (**직원이 받는 문서**)     |
| [operations.md](operations.md)                                     | §8 **설치 후 운영 작업** — 업데이트·직원 온보딩·**§8-S 보안 하드닝(직원 오픈 전)**·teardown·prod 승격·멀티계정 | **설치가 끝난 뒤 · 직원 오픈 전**        |
| [prd.md](prd.md)                                                   | 요구사항 · 확정 범위 · out-of-scope                                                    | 시작 전 · 고객사와 범위 합의할 때          |
| [architecture.md](architecture.md)                                 | **전체 그림 1장** — ASCII 아키텍처 · 요청 흐름 5개 · 벤더 레퍼런스 대비                              | 시작 전 · 구조를 한눈에 보고 싶을 때        |
| [web-search-explained.md](web-search-explained.md)                 | 서버측 web search 가 동작하는 원리 (초보자용 ASCII 흐름)                                       | §5 를 개념부터 이해하고 싶을 때           |
| [client-setup-explained.md](client-setup-explained.md)             | 클라이언트 설치·인증 흐름 (초보자용 ASCII 흐름)                                                 | §6 을 개념부터 이해하고 싶을 때           |
| [telemetry-explained.md](telemetry-explained.md)                   | Claude Code 텔레메트리(OTEL) — 무엇을 수집·어디로·켤까끌까                                      | §6 setup 이 켜는 텔레메트리를 이해·결정할 때 |


> **처음이라면**: 이 문서를 끝까지 읽고 → [install-guide.md](install-guide.md) 를 위에서 아래로 실행한다.

>

