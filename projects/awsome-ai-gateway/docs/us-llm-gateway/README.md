# US AWSome AI Gateway

**사내 Claude Code · Cowork 를 Amazon Bedrock 으로 연결하는 LLM 게이트웨이**
설치(최초 1회)와 그 이후의 업데이트를 한자리에서 관리합니다.

**한국어** · [English](README.en.md)

- 🔴 **코드는 fork 의 `us/deploy-fixes` 브랜치에서 받습니다**
  https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway
  <br>페이지 상단에 `forked from aws-samples/…` 배너가 보이는 것이 정상입니다 — 주소창의 `gonsoomoon-ml` 과 브랜치 `us/deploy-fixes` 로 확인하십시오.
- **upstream** — [`aws-samples/sample-agentic-ai-acceleration-kr`](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr/tree/main/projects/awsome-ai-gateway). 원본으로서  US AWSome AI Gateway는 원본의 US 를 위한 커스터마이즈 버전 입니다. 
- **리전** — 이 배포는 `us-west-2` (인프라). 추론은 **US Geo** 라 us-east-1/2 · us-west-2 로 분산됩니다
  - ⚠️ **리전 변경은 파라미터 하나로 끝나지 않습니다** — `terraform.tfvars` 의 `aws_region`·`azs`·`bedrock_model_arns`(리전 스코프 ARN)를 함께 고치고, 가이드 본문의 `us-west-2`(install-guide 51곳 등)를 치환해야 합니다.
  - ⚠️ **US 밖(예: 유럽)에 설치하려면 설정을 바꿔야 합니다** — 추론 프로파일을 `eu.anthropic.`* 로 교체하고 모델 ID·IAM 리소스 ARN 을 함께 조정해야 하며, 그 리전의 모델 제공 여부를 먼저 확인해야 합니다. 서버측 web search 커넥터는 **us-east-1 전용**이라 cross-region 호출이 됩니다.
- **추론 백엔드** — `bedrock-runtime` + US Geo 추론 프로파일 (`us.anthropic.`*). Bedrock **Mantle 아님**
- **클라이언트** — Claude Code (Mac · Windows · Linux) · Cowork (`US-02` 적용 후)
- **모델** — Opus 4.8 · Sonnet 5 · Haiku 4.5 (+ **Opus 5** = `US-02`)

> fork 는 upstream 위로 **리베이스**되므로 커밋 해시가 바뀝니다 — 그래서 이 문서는 버전을 해시가 아니라 `US-NN` 으로 셉니다. 전체 확정 범위는 [install-overview.md §0](install-overview.md#0-이번-배포의-범위-확정).

---



## 1. 작업별 진입점


| 구분            | 수행 작업                                 | 참조 문서                                                                                                       |
| ------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **신규 설치**     | 인프라 프로비저닝 → 앱 배포 → 라우팅·웹서치 → 클라이언트 연결 | [install-overview.md](install-overview.md) → [install-guide.md](install-guide.md)                           |
| **운영 중 업데이트** | 현재 적용 상태를 점검하고 미적용 항목만 반영             | [2. 최신 업데이트](#2-최신-업데이트) — `status.sh` 점검 후 미적용 항목만                                                                  |
| **클라이언트 배포**  | 직원 PC 에 Claude Code · Cowork 설치       | [client-install.md](client-install.md) · [cowork/cowork-client-install.md](cowork/cowork-client-install.md) |


> ⚠️ **신규 설치도** `US-02` **적용이 필요합니다.** 설치 마이그레이션이 Cowork 라우팅 행을 존재하지 않는 계정으로 심기 때문에, `install-guide.md` 를 끝내도 Cowork 는 동작하지 않습니다 — [최신 업데이트](#2-최신-업데이트) 참조.

---



## 2. 최신 업데이트

🔥 새 업데이트는 위로 쌓입니다. `US-NN` 은 리베이스에 영향받지 않는 고정 ID 입니다.

> ⚠️ **적용하기 전에 [3. 적용하기](#3-적용하기)로 현재 상태를 먼저 확인하십시오.** 이미 적용된 것을 다시 돌리거나 선행 조건을 건너뛰지 않기 위해서입니다.

> 등급 — **필수**: 반드시 적용(컴플라이언스·필수 기능) · **권장**: 해당 기능이 동작하지 않음 · **선택**: 요구가 있을 때만

- **[2026/08]** `US-04` **Bedrock·STS 를 NAT 대신 VPC Endpoint 로** — **필수**(컴플라이언스) · 신규 설치 **이미 포함**
Bedrock·STS 호출이 퍼블릭 인터넷을 지나지 않고 VPC 내부 PrivateLink 로만 흐르게 합니다. 기존 배포는 엔드포인트가 없어 NAT 를 거치므로 적용해야 합니다.
→ [operations.md §8-N](operations.md#8-n-bedrock-을-nat-대신-vpc-endpointprivatelink로)
- **[2026/08]** `US-03` **Admin UI 한/영 토글** — **필수**(영문 지원) · 신규 설치 **이미 포함**
관리 화면 전체가 i18n 으로 전환돼 헤더의 KO/EN 토글이 실제로 번역합니다. 기존 배포는 admin-ui 이미지를 다시 빌드해야 반영됩니다.
→ [operations.md §8-U](operations.md#8-u-업데이트-코드-변경-반영) 의 **A. 서비스 코드** — `rebuild-image.sh admin-ui <env>` → `install-eks.sh <env>` (선행: `06-persist-annotations.sh` dry-run)
- **[2026/08]** `US-02` **Cowork 연결 + Opus 5 등록** — **항목마다 대상이 다릅니다** · 🔴 **신규 설치도 해당**
  · **Cowork 를 쓰면 필수** — `01` 라우팅 교정 · `03` HTTPS(CloudFront). 설치 마이그레이션이 Cowork 라우팅 행을 **존재하지 않는 계정**으로 심어, 그대로 두면 Cowork 요청이 전부 502 로 실패합니다.
  · **Opus 5 를 쓰면 필수** — `02` 모델 등록. **Cowork 와 무관하며 Claude Code 에도 필요합니다**(시드는 Opus 4.8 까지). ⚠️ 단가를 빼먹으면 비용이 `$0` 으로 기록되고 예산이 우회됩니다. 전체 절차·함정은 [operations.md §8-M](operations.md#8-m-모델-추가와-교체). 이름과 달리 **다른 모델을 추가할 때도 쓰는 범용 스크립트**입니다(`config.env` 의 `MODEL_ALIAS`·`MODEL_PROVIDER_ID`).
  · Claude Code 만 쓰고 Opus 4.8 · Sonnet 5 · Haiku 4.5 로 충분하면 **US-02 전체를 건너뛰어도 됩니다.**
→ [update-scripts 실행 순서](update-scripts/README.md#실행-순서)
- **[2026/07]** `US-01` **최초 설치** — 기준선
단일 계정 · `us-west-2` · Claude Code · US Geo 추론으로 게이트웨이를 세웁니다.
→ [install-overview.md](install-overview.md)

---



## 3. 적용하기

저장소를 최신으로 맞추고, 위 목록 중 무엇이 이 배포에 반영돼 있는지 확인합니다.

> **아래 명령은 모두 「배포 EC2」에서 실행합니다.** `US-01` 최초 설치 때 만든 작업 서버이고, 이미 갖고 계십니다([install-guide.md §1-2](install-guide.md#1-2-배포-작업용-ec2-deployment-ec2-us-west-2)). 랩톱에서는 동작하지 않습니다 — DB 가 프라이빗 VPC 안에 있어 그 호스트를 거쳐야 하고, 클러스터 접근용 kubeconfig 와 게이트웨이 저장소 사본(`~/awsome-ai-gateway`)도 거기에만 있습니다.

▶ **① 저장소를 최신으로 맞춥니다** · 배포 EC2

**먼저 fork 를 보고 있는지 확인합니다.** upstream(`aws-samples`)에는 `us/deploy-fixes` 브랜치가 **없어서**, 원격이 잘못돼 있으면 아래 갱신이 "unknown revision" 으로 실패합니다.

```bash
cd ~/awsome-ai-gateway && git remote -v
```

`origin` 이 **`gonsoomoon-ml/sample-agentic-ai-acceleration-kr`** 이어야 합니다. `aws-samples` 로 나오면 upstream 을 clone 한 것이니 원격을 바꿉니다.

```bash
git remote set-url origin https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
```

이 브랜치는 upstream 위로 **리베이스**되므로 `git pull` 이 통하지 않습니다 — 히스토리가 갈라져 `--ff-only` 가 실패합니다. 원격에 그대로 맞추되, **이 EC2 에만 있는 실값 파일**을 먼저 백업합니다.

```bash
cd ~/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak
git fetch origin && git reset --hard origin/us/deploy-fixes
cp ~/values.bak $V
```

끝나면 확인합니다 — **마지막 `cp` 를 빠뜨리는 것이 이 절차의 유일한 위험**입니다.

```bash
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```
```bash
git status --short && git log --oneline -1
```

`values restored OK` 가 나오고 HEAD 가 `origin/us/deploy-fixes` 와 같으면 정상입니다. `RESTORE FAILED` 면 `cp ~/values.bak $V` 를 다시 돌리십시오.

ℹ️ 이 파일에 `<RDS_PROXY_ENDPOINT>`·`<ELASTICACHE_ENDPOINT>` 가 **남아 있는 것이 정상**입니다 — `install-eks.sh` 가 helm 실행 시점에 `terraform output` 에서 읽어 `--set` 으로 주입합니다. 그래서 `helm upgrade -f values` 를 직접 치면 안 됩니다.

⚠️ `values-*.yaml` 은 계정 실값(레지스트리·리전·`inbound-cidrs`·시크릿 키)이라 커밋할 수 없어 **이 EC2 한 대에만** 있습니다. 잃으면 다음 `helm upgrade` 가 placeholder 로 나가 **ALB IP 허용목록이 통째로 빠집니다.**
ℹ️ `terraform.tfvars`·`.terraform/`·`config.env`·`snapshots/` 는 gitignore 대상이라 `reset --hard` 로 지워지지 않습니다. 반대로 **디렉터리째 지우고 재클론하면 복구되지 않습니다.**
ℹ️ `.terraform.lock.hcl` 이 함께 되돌아가지만 `terraform init` 이 다시 채우므로 무방합니다.

▶ **② 적용 상태를 확인합니다** · 1~2분, 구성 변경 없음

`status.sh` 는 [2. 최신 업데이트](#2-최신-업데이트)의 각 업데이트가 이 배포에 반영돼 있는지를 **라이브 시스템을 조회해** 판정합니다. 판정 근거는 코드 버전이 아니라 실제 배포 상태입니다 — DB 라우팅 행, CloudFront 배포, VPC 엔드포인트, 실행 중인 컨테이너 이미지.

출력 예시 — 일부 업데이트만 적용된 배포:

```
 US AWSome AI Gateway — 업데이트 적용 상태
 ────────────────────────────────────────────────────────────
 계정 <ACCOUNT_ID> / us-west-2 · release llm-gateway · ns llm-gateway

   OK   US-01  최초 설치 (기준선)
   !!   US-02  Cowork 연결 + Opus 5 등록 — 일부 적용
        routing=invoke · claude-opus-5 ACTIVE · CloudFront 없음
   XX   US-03  Admin UI 한·영 토글 — 미적용
        이미지 tag 1.0.12 (푸시 2026-08-04 03:11 UTC) — i18n 반입 이전 빌드
   XX   US-04  Bedrock·STS VPC Endpoint — 미적용 (필수)
        엔드포인트 없음 → Bedrock·STS 호출이 NAT 경유

 다음 작업 (update-scripts 디렉터리에서 실행)
 ────────────────────────────────────────────────────────────
   bash 03-create-cloudfront.sh        # Cowork 를 사용하는 경우에만 필요
   bash 09-update-admin-ui.sh          # 선행: bash 06-persist-annotations.sh
```

**실행 조건 및 유의사항**

- **배포 EC2 에서만 동작합니다.** DB 가 프라이빗 VPC 안에 있어 해당 호스트를 경유해야 하며, 클러스터 접근에 필요한 kubeconfig 도 그곳에 있습니다.
- **구성을 변경하지 않습니다.** 다만 엄밀한 의미의 읽기 전용은 아닙니다 — DB 조회를 위해 클러스터에 일회용 psql 파드를 생성한 뒤 삭제합니다. Fargate 스케줄링 때문에 **1~2분**이 소요됩니다(실측 1분 20~30초).
- 판정 근거 원문이 필요하면 `bash status.sh --verbose` 로 실행합니다.

---



## 4. 시스템 개요

사용자의 Claude Code · Cowork 요청을 인증하고, 팀·사용자별 예산과 레이트리밋을 적용한 뒤 Amazon Bedrock 으로 전달하는 게이트웨이입니다. 요청 경로(데이터 플레인)와 관리 기능(컨트롤 플레인)이 분리된 별도 서비스로 동작하며, 사용량과 비용은 요청 시점에 기록됩니다.

- **인증** — Cognito OIDC 로그인으로 가상 키(VK)를 발급하고, 요청마다 VK 를 검증
- **제어** — 예산·레이트리밋을 요청마다 원자적으로 검사하고, 초과 시 차단 또는 모델 하향
- **추론** — `bedrock-runtime` US Geo 추론 프로파일로 전달 (us-east-1/2 · us-west-2 분산)
- **집계** — 요청별 토큰·비용을 기록하고 Admin UI 에서 팀·사용자 단위로 조회

구조도와 요청 흐름은 [architecture.md](architecture.md), 이 배포의 확정 범위는 [install-overview.md §0](install-overview.md#0-이번-배포의-범위-확정) 에 있습니다.
