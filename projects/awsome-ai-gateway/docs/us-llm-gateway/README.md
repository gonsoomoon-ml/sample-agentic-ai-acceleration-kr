# AWSome AI Gateway — 해외 배포판

**한국어** · [English](README.en.md)

**사내 Claude Code · Cowork 를 Amazon Bedrock 으로 연결하는 LLM 게이트웨이** — 설치(최초 1회)와 이후 업데이트를 한자리에서.

**직원 PC**(Claude Code · Cowork) → **게이트웨이**(인증 · 예산 · 비용 기록) → **Amazon Bedrock** · 구조는 [architecture.md](architecture.md) 「전체 그림」

[원본](../../README.md)(서울 배포 기준)과 다른 점: 해외 리전 · 모든 요청을 한 계정의 Bedrock 으로 바로 보냄(원본은 앱마다 다른 계정·백엔드로 나누고 일부는 Bedrock Mantle) · 공개 https 입구 · 영문 UI. 업데이트 ID `US-NN` 의 "US" 는 첫 배포 리전(us-west-2)에서 온 **트랙 이름**이라 리전을 바꿔도 번호는 이어진다.

**지금 하려는 것**
- **처음 설치한다** — POC 인지 운영인지 [1. 신규 설치 범위](#1-신규-설치-범위--무엇을-쓰느냐--poc-인가-운영인가)에서 먼저 고른다 → POC: [install-overview.md](install-overview.md)(범위·흐름 10분) → [install-guide.md](install-guide.md) · 운영: [ops/8-P-prod.md](ops/8-P-prod.md)
- **이미 설치했다 — 업데이트 상태를 보겠다** — 배포 EC2 에서 [3. 적용하기](#3-적용하기-배포-ec2-에서) 순서대로: 저장소 최신화 → `status.sh` → 미적용 항목만 [2. 최신 업데이트](#2-최신-업데이트) 표의 문서로
- **직원 PC 만 설정한다** — Claude Code: [client-install.md](client-install.md)(수동) · Windows 설치 파일 [US-14](claude-code/installer/cc-installer-admin-e2e-windows.md) · Cowork: [cowork/README.md](cowork/README.md)(설치 파일 · 수동, OS 별 문서 목록)

**이 배포**
- 🔴 **코드** — fork 의 **`us/deploy-fixes`** 브랜치: https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway (원본 [aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr) 에 아직 없는 배포·벤더 픽스 포함, `forked from aws-samples/…` 배너가 정상). upstream 위로 **리베이스**되어 해시가 바뀌므로 버전은 **`US-NN`** 으로 센다
- **리전 · 추론** — 인프라 `us-west-2` · 추론은 `bedrock-runtime` 의 **US Geo** 프로파일(`us.anthropic.*`, us-east-1/2·us-west-2 분산) · 리전 변경/US 밖 설치는 [install-overview §0](install-overview.md#0-이번-배포의-범위-확정)
- **클라이언트 · 모델** — Claude Code(Mac·Windows·Linux) · Cowork · **Opus 5.5 · Sonnet 5.5 · Haiku 5.5 · Haiku 4.5** — POC·운영 모두 같다

---

## 1. 신규 설치 범위 — 무엇을 쓰느냐 · POC 인가 운영인가

**POC 와 운영은 옵션 차이가 아니라 다른 스택이다** — 운영은 dev 를 고치는 게 아니라 **별도 계정에 `environment=prod` 로 새로** 세운다(`US-08`).

**① POC 와 운영 — 무엇이 다른가**

| | POC (dev) | 운영 (prod) |
|---|---|---|
| 계&#8288;정 · 사&#8288;이&#8288;징 | 한 계정 · `environment=dev` (Aurora 1 · Valkey 1 · NAT 1) | **별도 계정** · `environment=prod` (Aurora ×2 · Valkey 3 shard × 3 · NAT ×2) |
| 입구 | http ALB + IP 허용목록(방식 A) | https 도메인(`US-06`) + admin ALB 2개 internal(`US-07`, S2S VPN 전제) |
| 절차 | `US-01` — [install-guide.md](install-guide.md) §1~§6 | **`US-08` 만** — [ops/8-P-prod.md](ops/8-P-prod.md) 순서로 [install-guide.md](install-guide.md) §1~§6 을 prod 계정에서 |

**② 쓰는 앱에 따라 — 무엇을 설치하나**

| 사용 구성 | POC (dev) | 운영 (prod) |
|---|---|---|
| Claude Code 만 | `US-01` 만 | `US-08` 만 |
| Claude Code + **Cowork** | `US-01` + https 입구 하나(도메인 있으면 `US-06`, 없으면 `US-02` 의 CloudFront) | `US-08` 만(https 포함이라 입구 추가 없음) |

**신규 설치에서 각 `US-NN` 은** — 자세한 설명은 [install-overview.md §1](install-overview.md#1-신규-설치와-us-nn)

- **대부분 포함** — 아래에 없는 `US-NN` 은 설치 절차에 이미 들어 있다. 따로 적용하지 않는다
- **설치 뒤 따로**: `US-12`(관리 화면 Cognito 로그인 — 운영 `US-08` 은 사실상 필수) · `US-15`(VK 수명 24시간, 선택)
- **둘 중 하나 고르기**: 직원 PC 를 수동 설치 ↔ 설치 파일(`US-14` Claude Code · `US-09` Cowork)
- **POC 에서 선택**: `US-06`(https) · `US-07`(admin internal) · 도메인 없이 Cowork 를 쓰면 `US-02` 의 CloudFront 만

---

## 2. 최신 업데이트

**최근 5개만** — 전체 이력(US-01~)과 항목별 이유·함정은 [updates.md](updates.md). **적용 전 [3. 적용하기](#3-적용하기-배포-ec2-에서)로 현재 상태부터.**

| ID (문서) | 무엇 | 등급 · 처음 설치한다면 | 이미 설치했다면 — 적용 방법 |
|---|---|---|---|
| [**US-19**](ops/8-D-upstream-sync.md#us-19-로-따라-할-때-달라지는-것) 2026/10 | **Haiku 5.5 추가** — 빠르고 싼 최신 Haiku(입력 단가가 Haiku 4.5 의 1/10) | 권장 · 신규 포함 | [8-D](ops/8-D-upstream-sync.md#us-19-로-따라-할-때-달라지는-것) 를 「US-19」 차이대로 · 약 1시간 |
| [**US-18**](ops/8-D-upstream-sync.md#us-18-로-따라-할-때-달라지는-것) 2026/10 | **예산·비용·권한 결함 수정** — 예산 알림 · 팀 예산 권한 · 비용 기록(Sonnet 5.5 캐시 단가) 등 7건 | 필수 · 신규 포함 | [8-D](ops/8-D-upstream-sync.md#us-18-로-따라-할-때-달라지는-것) 를 「US-18」 차이대로 · 약 1시간 |
| [**US-17**](ops/8-A-automode-server.md) 2026/10 | Claude Code **Auto mode 의 안전 판정을 Bedrock 이** — 따로 보내던 판정 요청과 과금 안내가 사라짐 | 권장 · Auto mode 를 쓰면 · 신규 포함 | US-18 을 적용하면 함께 적용됨 · 확인은 [8-A](ops/8-A-automode-server.md) |
| [**US-16**](ops/8-M-models.md) 2026/10 | **Sonnet 5.5 를 기본 모델로** — 이전 세대(Opus 5 · Sonnet 5 · Opus 4.8)는 목록에서 내림 | 권장 · 신규 포함 | [8-M](ops/8-M-models.md) 의 「0」~「C」 순서대로 |
| [**US-15**](ops/8-Z-token-ttl.md) 2026/10 | 게이트웨이 열쇠(VK) **수명 1시간 → 24시간** — 열쇠 재발급이 하루 한 번으로 | 선택 · 신규 설치도 따로 | [8-Z](ops/8-Z-token-ttl.md) · 약 10분 · 추론 무중단 |

---

## 3. 적용하기 (배포 EC2 에서)

**① 저장소 최신화** — 리베이스 브랜치라 `git pull` 대신 아래 명령.
- `git remote -v` 의 origin 이 `gonsoomoon-ml/…` 이어야 한다(aws-samples 면 `set-url`)
- `values-*.yaml` 은 이 EC2 유일본이라 백업·복원이 핵심 — `values restored OK` 를 확인
- prod(`US-08`)는 **prod 계정의 배포 EC2** 에서 `V=…/values-eks-fargate-prod.yaml` 로 같은 절차

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

**② 상태 점검** — 라이브 시스템(DB 행·엔드포인트·이미지·ALB)을 조회해 판정, 구성 변경 없음, 1~2분(일회용 psql 파드). `US-01` 부터 모든 항목이 한 줄씩 나온다 — `OK` 적용 · `!!`/`XX` 일부·미적용 · `--` 선택이거나 다른 곳에서 확인(판정하지 않는 항목은 어디서 보는지 적어 준다). 근거 원문은 `--verbose`.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash status.sh
```

**③ 미적용 항목만** 위 §2 표의 문서로. 「적용 방법」이 **8-D** 인 업데이트(코드·DB·단가가 함께 바뀌는 것)는 모두 [ops/8-D-upstream-sync.md](ops/8-D-upstream-sync.md) 한 절차다 — 맨 앞 (0) 에서 내 업데이트의 차이만 읽고, (1) 부터는 같다. prod 는 같은 문서 (10). 서비스 하나만 바뀌는 업데이트와 롤백 일반은 [ops/8-U-update.md](ops/8-U-update.md).

---

시스템이 무엇을 하는지(인증·예산·레이트리밋·추론·집계)와 구조도 → [architecture.md](architecture.md) 「전체 그림」 · 운영 구성도 [8-P §1](ops/8-P-prod.md#1-dev-와-무엇이-다른가) · 요구사항 [prd.md](prd.md) · 운영 [operations.md](operations.md)
