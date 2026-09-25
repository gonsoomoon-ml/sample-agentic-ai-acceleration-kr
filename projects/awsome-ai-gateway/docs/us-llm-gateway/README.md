# AWSome AI Gateway — 해외 배포판

**한국어** · [English](README.en.md)

**사내 Claude Code · Cowork 를 Amazon Bedrock 으로 연결하는 LLM 게이트웨이** — 설치(최초 1회)와 이후 업데이트를 한자리에서.
국내판과 다른 점: 해외 리전 · Bedrock 직결(Mantle 아님) · 공개 https 입구 · 영문 UI. 업데이트 ID `US-NN` 의 "US" 는 첫 배포 리전(us-west-2)에서 온 **트랙 이름**이라 리전을 바꿔도 번호는 이어진다.

**지금 하려는 것**
- **처음 설치한다** — POC: [install-overview.md](install-overview.md)(범위·흐름 10분) → [install-guide.md](install-guide.md)(§1~§6-0 실행) · 운영(별도 계정 prod): [ops/8-P-prod.md](ops/8-P-prod.md) 순서로 [install-guide.md](install-guide.md) §1~§6 을 prod 계정에서 — 어느 쪽인지는 [1. 신규 설치 범위](#1-신규-설치-범위--무엇을-쓰느냐--poc-인가-운영인가)에서 먼저
- **이미 설치했다 — 업데이트 상태를 보겠다** — 배포 EC2 에서 `bash status.sh` → 아래 [2. 최신 업데이트](#2-최신-업데이트) 표에서 미적용 항목만 · `status.sh` 는 모든 `US-NN` 을 한 줄씩 보여 주고, 판정하지 않는 항목(`US-08`·`09`·`10`·`11`·`14`)은 `--` 줄에 확인할 곳을 적는다 · `US-10`·`US-11` 은 `bash 14-postdeploy-check.sh`(DB 스키마 번호 · 단가 일치)로 확인
- **직원 PC 만 설정한다** — [client-install.md](client-install.md)(Claude Code) · [cowork/…windows.md](cowork/manual/cowork-client-install-windows.md) · [cowork/…macos.md](cowork/cowork-client-install-macos.md) · [cowork/installer/…e2e-windows.md](cowork/installer/cowork-installer-admin-e2e-windows.md)(Windows 설치기, US-09)

**이 배포**
- 🔴 **코드** — fork 의 **`us/deploy-fixes`** 브랜치: https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway (원본 [aws-samples](https://github.com/aws-samples/sample-agentic-ai-acceleration-kr) 에 아직 없는 배포·벤더 픽스 포함, `forked from aws-samples/…` 배너가 정상). upstream 위로 **리베이스**되어 해시가 바뀌므로 버전은 **`US-NN`** 으로 센다
- **리전** — `us-west-2`(인프라) · 추론은 **US Geo**(`us.anthropic.*`, us-east-1/2·us-west-2 분산) · 리전 변경/US 밖 설치는 [install-overview §0](install-overview.md#0-이번-배포의-범위-확정)
- **추론 백엔드** — `bedrock-runtime` + US Geo 추론 프로파일 (Mantle 아님)
- **클라이언트 · 모델** — Claude Code(Mac·Windows·Linux) · Cowork · Opus 5 · Opus 4.8 · Sonnet 5 · Haiku 4.5 — 전부 `US-01` 에 포함, 운영(`US-08`)도 포함
- **접속(입구)** — POC: http ALB + IP 허용목록(방식 A), 도메인이 있으면 https(`US-06`) · 운영(`US-08`): https 도메인 + admin ALB 2개 internal(S2S VPN)

---

## 1. 신규 설치 범위 — 무엇을 쓰느냐 · POC 인가 운영인가

**POC 와 운영은 옵션 차이가 아니라 다른 스택이다** — 운영은 dev 를 고치는 게 아니라 **별도 계정에 `environment=prod` 로 새로** 세운다(`US-08`).

| | POC (dev) | 운영 (prod) |
|---|---|---|
| 계정 · 사이징 | 한 계정 · `environment=dev` (Aurora 1 · Valkey 1 · NAT 1) | **별도 계정** · `environment=prod` (Aurora ×2 · Valkey 3 shard × 3 · NAT ×2) |
| 입구 | http ALB + IP 허용목록(방식 A) | https 도메인(`US-06`) + admin ALB 2개 internal(`US-07`, S2S VPN 전제) |
| 절차 | `US-01` — [install-guide.md](install-guide.md) §1~§6 | **`US-08`** — `US-01` 과 같은 설치를 prod 계정에서 [ops/8-P-prod.md](ops/8-P-prod.md) 를 따라 한다. 8-P 는 어느 단계에서 prod 설정(https · admin internal · VPN)을 더 넣는지 알려준다 (dev 는 그대로 둔다) |

| 사용 구성 | POC (dev) | 운영 (prod) |
|---|---|---|
| Claude Code 만 (Opus 5 · Opus 4.8 · Sonnet 5 · Haiku 4.5) | `US-01` | `US-08`(`US-01` 과 같은 설치를 prod 계정에서 8-P 대로 — https·admin internal·VPN 포함) |
| Claude Code + **Cowork** | `US-01` + https 입구 하나(도메인 있으면 `US-06`, 없으면 `US-02` 의 `03` CloudFront) | `US-08`(같음 · https 포함이라 입구 선택 없음) |

- **운영(`US-08`)** — `US-01` 과 같은 설치에 https(US-06) · admin internal(US-07) · VPN · prod 사이징을 처음부터 추가한다.
- **신규 설치(POC·운영 모두)에 이미 들어 있는 것 — 따로 적용하지 않는다**:
  - **`US-03·04·05`** — 관리 화면 한/영 · Bedrock VPC Endpoint · EKS 1.34 가 설치 절차에 포함.
  - **`US-10`** — 지금 코드가 곧 US-10 이다. 최신 DB 스키마 · 안정성 수정 · web search 비용 상한과 개선이 기본값으로 동작한다.
  - **`US-13`** — Opus 5.5 는 §4-2 의 SQL 이 등록한다(단가도 §4-2 (C) 가 심는다). 이미 설치한 곳만 US-13 을 따로 한다.
  - **`US-11`** — install-guide §4-2 (C) 가 `update-scripts/pricing.tsv` 의 단가를 심는다(기본 = `us.` Standard 티어). **다른 리전·티어로 청구받는 배포**는 §4-2 전에 이 파일을 자기 청구 단가로 고친다(방법은 install-guide §4-2 (C) 의 설명대로).
- **직원 PC 설치 방식은 둘 중 하나만 고른다** — Windows 는 `US-01` §6-3 의 수동 절차(Python·저장소·PATH)로 붙이거나, 설치 파일 하나로 끝내는 **`US-14`** 으로 한다. Cowork 도 같은 선택이 수동 가이드 ↔ **`US-09`** 로 있다. 어느 쪽이든 게이트웨이는 바뀌지 않는다.
- **POC(`US-01`)** 에만 해당:
  - **`US-06`(ALB HTTPS)** — Cowork 는 https 필수. 도메인 없으면 CloudFront(`03`), 있으면 US-06 — 둘 다는 불필요. 나중에 도메인이 생기면 [전환 절차](ops/8-H-alb-https.md).
  - **`US-07`(admin ALB internal)** — S2S VPN 이 있는 운영의 최종형이라 POC 엔 보통 불필요. 적용하려면 [전환 절차](ops/8-I-admin-internal.md) — VPN 없이 internal 로 두면 VK 발급이 막힌다. 운영은 VPN 이 전제([8-P §0](ops/8-P-prod.md#0-결론--전제)).
  - **`US-02` 는 기존 배포 전용** — 신규 설치는 §4-2(Opus 5)·§4-3(Cowork 라우팅)이 같은 내용을 포함한다. 신규에서 남는 것은 도메인 없이 Cowork 를 쓸 때의 `03` CloudFront 뿐.

---

## 2. 최신 업데이트

**최근 5개만** — 전체 이력(US-01~)과 항목별 이유·함정은 [updates.md](updates.md). `US-NN` 은 리베이스에 영향받지 않는 고정 ID. **적용 전 [3. 적용하기](#3-적용하기-배포-ec2-에서)로 현재 상태부터.**

| ID (문서) | 무엇 | 등급 · 처음 설치한다면 | 이미 설치했다면 — 적용 방법 |
|---|---|---|---|
| [**US-14**](claude-code/installer/cc-installer-admin-e2e-windows.md) 2026/09 | Claude Code **Windows 설치 파일** — 관리자가 설치 파일 1개를 만들어 배포하면 직원은 실행 후 명령 두 개로 끝난다(지금은 PC 마다 Python·저장소·PATH 를 손으로 맞춘다) | 선택 · Windows 직원 PC 가 있으면 권장 · `US-01` §6-3(수동 설치)으로도 붙는다 · 게이트웨이는 바뀌지 않는다 | 소스 브랜치(`feat/cc-installer-import`)에서 설치 파일 만들기 → 직원 PC 에 설치 → 로그인 1회 |
| [**US-13**](ops/8-M-models.md) 2026/09 | **Opus 5.5 모델 추가** — Opus 5 와 문맥·기능은 같고 단가가 20% 낮은 새 모델. 별칭 `claude-opus-5-5` 를 등록하고, 장애 시 Sonnet 5 로 내려가는 체인에도 넣는다 | 권장 · 기본 모델은 Sonnet 5 그대로 · **신규 설치는 §4-2 에 포함**(이미 설치한 곳만 이 절차) | [8-M](ops/8-M-models.md) 순서대로 — `config.env` 에 별칭·모델 ID·단가 → `02-add-opus5-model.sh --apply` → 5분 뒤 `04-verify.sh` → 폴백 체인 등록 후 gateway-proxy 재시작 |
| [**US-12**](ops/8-L-admin-login.md) 2026/09 | 관리 화면에 **Cognito 로그인** — 지금은 관리 화면 주소에 닿는 사람은 누구나 관리자로 들어간다(개발용 로그인). 직원과 같은 Cognito 계정으로 로그인하고 관리자 그룹만 들어가게 한다 | 선택 · 권장(주소만 막는 지금 방식에 계정 확인을 더한다 · https 주소 필요) · 처음 설치도 설치를 마친 뒤 이 문서로 | [8-L](ops/8-L-admin-login.md) 을 위에서 아래로(관리 API 새 버전 포함 · 두 번 배포 · 약 40분) — 콜백 주소 등록 → 관리 API 새 버전과 로그인 켜기 → 확인 → 개발용 로그인 끄기 |
| [**US-11**](ops/8-R-pricing.md) 2026/09 | 모델 단가를 **AWS 실제 청구**에 맞춤 — 미국 리전 묶음(`us.`) 호출은 글로벌 단가보다 10% 높게 청구된다 · Sonnet 5 의 9/1 인상은 취소돼 반영 · 단가가 바뀔 때마다 반복 | 필수(게이트웨이의 비용·예산이 실제 청구와 맞아야 한다) · 신규 포함(설치 중 §4-2 가 이 단가를 넣는다) | **US-10 을 [8-D](ops/8-D-upstream-sync.md) 로 했으면 끝**(⑧ 이 이 작업) · 그 뒤 단가가 바뀌면 단가 파일 `update-scripts/pricing.tsv` 를 고치고 `08` 스크립트로 적용(5분 뒤 반영) |
| [**US-10**](ops/8-D-upstream-sync.md) 2026/09 | 게이트웨이를 최신 코드로 올림(DB 구조 변경과 서비스 6개 전부 교체가 따르는 큰 업데이트) — 끊김·오류 6가지 수정(상태 점검 오판으로 전체가 한꺼번에 끊김 · thinking 요청 오류 · web search 반복 오류 · 예산 이중 차감 · Claude Code 의 advisor 를 켜면 전부 오류 · 오래 쉰 뒤 첫 요청 오류) · web search 비용 상한 · web search 개선(9/19 추가 — 검색 기록 유지 · 앱 도구와 함께 동작 · 설정 없이 기본 동작) | 필수(서비스가 끊기는 결함 수정) · 신규 포함(지금 설치하면 이 코드다) | [8-D](ops/8-D-upstream-sync.md) 를 위에서 아래로 따라 한다(약 1.5시간 · 사용자가 적은 시간에) — 점검 → DB 백업 → 새 버전 빌드 → 배포 → 단가 → 확인 · 9/19 전에 끝낸 곳은 [updates.md](updates.md) US-10 의 9/19 추가분만 |
그 이전(`US-01` 최초 설치)과 항목별 이유·함정 → [updates.md](updates.md)

---

## 3. 적용하기 (배포 EC2 에서)

**① 저장소 최신화** — 리베이스 브랜치라 `git pull` 이 아니라 아래. `values-*.yaml` 은 이 EC2 유일본이라 백업·복원이 핵심(`values restored OK` 확인). `git remote -v` 의 origin 이 `gonsoomoon-ml/…` 이어야 한다(aws-samples 면 `set-url`). prod 스택(`US-08`)은 **prod 계정의 배포 EC2** 에서 `V=…/values-eks-fargate-prod.yaml` 로 같은 절차 — `status.sh` 는 US-08~11·14 를 판정하지 않고 `--` 줄로 확인할 곳을 알려 준다(US-08 은 별도 스택, US-09·14 는 PC 쪽, US-10·11 은 `14-postdeploy-check.sh` 가 판정).

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

**③ 미적용 항목만** 위 §2 표의 문서로. 상세 절차·함정·롤백은 [ops/8-U-update.md](ops/8-U-update.md). **`US-10`·`US-11`**(upstream 대량 동기화 — 코드·스키마·단가 일괄)은 [ops/8-D-upstream-sync.md](ops/8-D-upstream-sync.md) 한 절차로 함께 적용한다 — prod 는 같은 문서 ⑩.

---

시스템이 무엇을 하는지(인증·예산·레이트리밋·추론·집계)와 구조도 → [architecture.md](architecture.md) 「전체 그림」 · 운영 구성도 [8-P §1](ops/8-P-prod.md#1-dev-와-무엇이-다른가) · 요구사항 [prd.md](prd.md) · 운영 [operations.md](operations.md)
