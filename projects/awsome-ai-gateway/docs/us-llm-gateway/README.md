# AWSome AI Gateway — 해외 배포판

**한국어** · [English](README.en.md)

**사내 Claude Code · Cowork 를 Amazon Bedrock 으로 연결하는 LLM 게이트웨이** — 설치(최초 1회)와 이후 업데이트를 한자리에서.
국내판과 다른 점: 해외 리전 · Bedrock 직결(Mantle 아님) · 공개 https 입구 · 영문 UI. 업데이트 ID `US-NN` 의 "US" 는 첫 배포 리전(us-west-2)에서 온 **트랙 이름**이라 리전을 바꿔도 번호는 이어진다.

**지금 하려는 것**
- **처음 설치한다** — POC: [install-overview.md](install-overview.md)(범위·흐름 10분) → [install-guide.md](install-guide.md)(§1~§6-0 실행) · 운영(별도 계정 prod): [ops/8-P-prod.md](ops/8-P-prod.md) — 어느 쪽인지는 [1. 신규 설치 범위](#1-신규-설치-범위--무엇을-쓰느냐--poc-인가-운영인가)에서 먼저
- **이미 설치했다 — 업데이트 상태를 보겠다** — 배포 EC2 에서 `bash status.sh` → 아래 [2. 최신 업데이트](#2-최신-업데이트) 표에서 미적용 항목만
- **직원 PC 만 설정한다** — [client-install.md](client-install.md)(Claude Code) · [cowork/…windows.md](cowork/manual/cowork-client-install-windows.md) · [cowork/…macos.md](cowork/cowork-client-install-macos.md) · [cowork/installer/…e2e-windows.md](cowork/installer/cowork-installer-admin-e2e-windows.md)(Windows 설치기, US-09)

**이 배포**
- 🔴 **코드** — fork 의 **`us/deploy-fixes`** 브랜치: https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway (원본 [aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr) 에 아직 없는 배포·벤더 픽스 포함, `forked from aws-samples/…` 배너가 정상). upstream 위로 **리베이스**되어 해시가 바뀌므로 버전은 **`US-NN`** 으로 센다
- **리전** — `us-west-2`(인프라) · 추론은 **US Geo**(`us.anthropic.*`, us-east-1/2·us-west-2 분산) · 리전 변경/US 밖 설치는 [install-overview §0](install-overview.md#0-이번-배포의-범위-확정)
- **추론 백엔드** — `bedrock-runtime` + US Geo 추론 프로파일 (Mantle 아님)
- **클라이언트 · 모델** — Claude Code(Mac·Windows·Linux) · Cowork · Opus 4.8 · Sonnet 5 · Haiku 4.5 · Opus 5 — POC 는 Cowork·Opus 5 를 `US-02` 로 추가, 운영(`US-08`)은 포함
- **접속(입구)** — POC: http ALB + IP 허용목록(방식 A), 도메인이 있으면 https(`US-06`) · 운영(`US-08`): https 도메인 + admin ALB 2개 internal(S2S VPN)

---

## 1. 신규 설치 범위 — 무엇을 쓰느냐 · POC 인가 운영인가

**POC 와 운영은 옵션 차이가 아니라 다른 스택이다** — 운영은 dev 를 고치는 게 아니라 **별도 계정에 `environment=prod` 로 새로** 세운다(`US-08`).

| | POC (dev) | 운영 (prod) |
|---|---|---|
| 계정 · 사이징 | 한 계정 · `environment=dev` (Aurora 1 · Valkey 1 · NAT 1) | **별도 계정** · `environment=prod` (Aurora ×2 · Valkey 3 shard × 3 · NAT ×2) |
| 입구 | http ALB + IP 허용목록(방식 A) | https 도메인(`US-06`) + admin ALB 2개 internal(`US-07`, S2S VPN 전제) |
| 절차 | `US-01` — [install-guide.md](install-guide.md) §1~§6 | **`US-08`** — [ops/8-P-prod.md](ops/8-P-prod.md) (dev 는 그대로 둔다) |

| 사용 구성 | POC (dev) | 운영 (prod) |
|---|---|---|
| Claude Code 만 (Opus 4.8 · Sonnet 5 · Haiku 4.5) | `US-01` | `US-08` |
| Claude Code 만 + **Opus 5** | `US-01` + `US-02` 의 `02`(모델 등록) | `US-08` |
| Claude Code + **Cowork** | `US-01` + `US-02` 전체 (`01` 라우팅 · `02` 모델 · `03` CloudFront) | `US-08` |

- **운영(`US-08`)** — https(US-06) · admin internal(US-07) · Cowork 라우팅 · Opus 5 를 처음부터 포함. `US-03·04·05` 는 신규 설치에 포함(필수).
- **POC(`US-01`)** 에만 해당:
  - **`US-06`(ALB HTTPS)** — Cowork 는 https 필수. 도메인 없으면 CloudFront(`03`), 있으면 US-06 — 둘 다는 불필요. 나중에 도메인이 생기면 [전환 절차](ops/8-H-alb-https.md).
  - **`US-07`(admin ALB internal)** — S2S VPN 이 있는 운영의 최종형이라 POC 엔 보통 불필요. 적용하려면 [전환 절차](ops/8-I-admin-internal.md) — VPN 없이 internal 로 두면 VK 발급이 막힌다. 운영은 VPN 이 전제([8-P §0](ops/8-P-prod.md#0-결론--전제)).
  - ⚠️ **`US-02` 는 POC 신규 설치에도 필요** — 마이그레이션이 Cowork 라우팅 행을 없는 계정으로 심어 그대로 두면 Cowork 전부 502. Claude Code 만 + 시드 모델이면 생략 가능.

---

## 2. 최신 업데이트

**최근 5개만** — 전체 이력(US-01~)과 항목별 이유·함정은 [updates.md](updates.md). `US-NN` 은 리베이스에 영향받지 않는 고정 ID. **적용 전 [3. 적용하기](#3-적용하기-배포-ec2-에서)로 현재 상태부터.**

| ID (문서) | 무엇 | 등급 · 신규 설치 | 기존 배포가 할 일 |
|---|---|---|---|
| [**US-09**](cowork/installer/cowork-installer-admin-e2e-windows.md) 2026/08 | Cowork Windows 설치기 — 관리자가 .exe 1개 빌드 → 직원 PC 설치(HKLM 정책) | 선택 · Cowork Windows 쓰면 권장(수동 설치 대체) · 게이트웨이 변경 없음 | 빌드 PC 에서 `feat/cowork-installer-import` clone → `07-client-values.sh` 값으로 `site-config.json` → `build.ps1` → 직원 PC 설치 + `setup` |
| [**US-08**](ops/8-P-prod.md) 2026/08 | prod 스택 신설 — 별도 계정 · https + admin internal + VPN · Cowork Windows | 선택 · POC 이후 운영 전환 시 · `environment=prod` | dev 는 그대로 두고 prod 계정에 §1~§6 재실행(8-P 순서) |
| [**US-07**](ops/8-I-admin-internal.md) 2026/08 | 고객사 최종 아키텍처 — admin ALB 2개를 internal 로 | 선택 · 전제 S2S VPN · POC 신규는 §3-6 시점에 values 주석 해제 · 운영(`US-08`)은 포함 | values 주석 2곳 해제 → helm(ALB 재생성) → admin SG·CNAME 교체 |
| [**US-06**](ops/8-H-alb-https.md) 2026/08 | ALB HTTPS — 커스텀 도메인 + ACM | 선택 · POC 는 도메인 있을 때 · 운영(`US-08`)은 포함 | 도메인 확보 → 전환 → 클라이언트 URL 2개 교체 (30분) |
| [**US-05**](ops/8-E-eks-upgrade.md) 2026/08 | EKS 1.31 → 1.34 | 필수(지원 만료·비용) · 신규 포함 | 1단계씩 3회 apply + 전 ns 재시작 |
그 이전(`US-01` 최초 설치)과 항목별 이유·함정 → [updates.md](updates.md)

---

## 3. 적용하기 (배포 EC2 에서)

**① 저장소 최신화** — 리베이스 브랜치라 `git pull` 이 아니라 아래. `values-*.yaml` 은 이 EC2 유일본이라 백업·복원이 핵심(`values restored OK` 확인). `git remote -v` 의 origin 이 `gonsoomoon-ml/…` 이어야 한다(aws-samples 면 `set-url`). prod 스택(`US-08`)은 **prod 계정의 배포 EC2** 에서 `V=…/values-eks-fargate-prod.yaml` 로 같은 절차 — `status.sh` 는 US-08~09 를 판정하지 않는다(US-08 은 별도 스택, US-09 는 PC 쪽).

```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

**② 상태 점검** — 라이브 시스템(DB 행·엔드포인트·이미지·ALB)을 조회해 판정, 구성 변경 없음, 1~2분(일회용 psql 파드). 근거 원문은 `--verbose`.

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash status.sh
```
```
   OK   US-01  최초 설치 (기준선)
   !!   US-02  Cowork 연결 + Opus 5 등록 — 일부 적용   routing OK · opus-5 OK · CloudFront 없음
   XX   US-04  Bedrock·STS VPC Endpoint — 미적용 (필수)
   --   US-06  ALB HTTPS (커스텀 도메인) — 미적용 (선택 · 운영이면 권장)
 다음 작업: bash 03-create-cloudfront.sh … / (수동) ops/8-N-vpc-endpoint.md …
```

**③ 미적용 항목만** 위 §2 표의 문서로. 상세 절차·함정·롤백은 [ops/8-U-update.md](ops/8-U-update.md).

---

시스템이 무엇을 하는지(인증·예산·레이트리밋·추론·집계)와 구조도 → [architecture.md](architecture.md) 「전체 그림」 · 운영 구성도 [8-P §1](ops/8-P-prod.md#1-dev-와-무엇이-다른가) · 요구사항 [prd.md](prd.md) · 운영 [operations.md](operations.md)
