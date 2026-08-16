# 업데이트 이력 (US-NN)

**한국어** · [English](updates.en.md)

README 의 「최신 업데이트」는 최근 5개만 보여준다 — 여기가 전체 이력이다. `US-NN` 은 리베이스에 영향받지 않는 고정 ID. 적용 상태는 배포 EC2 에서 `bash status.sh`.
등급 — **필수**: 반드시 · **권장**: 안 하면 그 기능 동작 안 함 · **선택**: 요구 있을 때

| ID (문서) | 무엇 | 등급 · 신규 설치 | 기존 배포가 할 일 |
|---|---|---|---|
| [**US-06**](ops/8-H-alb-https.md) 2026/08 | ALB HTTPS — 커스텀 도메인 + ACM 인증서 | 선택 · **운영이면 강력 권장** · 신규는 §3-6 시점에 같은 절차 | 도메인 확보 → 전환 → 클라이언트 URL 2개 교체 (약 30분) |
| [**US-05**](ops/8-E-eks-upgrade.md) 2026/08 | EKS 1.31 → 1.34 | 필수(지원 만료·비용) · 신규 포함 | 1단계씩 3회 apply + 전 ns 파드 재시작 |
| [**US-04**](ops/8-N-vpc-endpoint.md) 2026/08 | Bedrock·STS 를 NAT 대신 VPC Endpoint 로 | 필수(컴플라이언스) · 신규 포함 | 엔드포인트 apply → gateway-proxy 재시작 |
| [**US-03**](ops/8-U-update.md) 2026/08 | Admin UI 한/영 토글 | 필수(영문 지원) · 신규 포함 | admin-ui 이미지 재빌드 → install-eks |
| [**US-02**](update-scripts/README.md#실행-순서) 2026/08 | Cowork 연결 + Opus 5 등록 | 항목별 — Cowork 쓰면 `01`·`03`, Opus 5 쓰면 `02` 필수 · 🔴 **신규도 해당** | 01 라우팅 · 02 모델(단가 필수) · 03 CloudFront(도메인 없을 때만) |
| [**US-01**](install-overview.md) 2026/07 | 최초 설치 (기준선) | — | — |

## 왜 · 함정 (항목별)

- **US-06** — ALB 3개를 http:80(임시 ALB 주소) 대신 `https://gateway-<env>.<도메인>` 으로. ACM TLS 종료·고정 이름, Cowork 용 CloudFront 불필요. 등록이 막힌 계정(Amazon 내부 등)은 타 계정 등록 + NS 위임. 적용 후 `ANTHROPIC_BASE_URL`·`ADMIN_API_URL` 교체.
- **US-05** — 1.31 은 표준 지원 종료로 연장 요금(클러스터당 월 ~$365) · 최종 종료(2026-11-26) 후 강제 자동 업그레이드. 마이너 1단계씩만(3회 apply), 단계마다 전 ns 파드 재시작(Fargate 는 파드=노드).
- **US-04** — Bedrock·STS 호출이 NAT·퍼블릭 인터넷 대신 VPC 내부 PrivateLink 로. 엔드포인트 선언이 들어가기 전에 만든 VPC 만 대상(신규는 이미 포함) — Bedrock 은 계속 성공하니 아무도 안 알려준다. 적용 직후 gateway-proxy 재시작 필수(풀에 남은 죽은 소켓 → 연속 502 를 엔드포인트 탓으로 오진).
- **US-03** — 관리 화면 i18n, 헤더 KO/EN 토글이 실제로 번역. admin-ui 이미지 재빌드가 필요.
- **US-02** — 설치 마이그레이션이 Cowork 라우팅 행을 존재하지 않는 계정으로 심어 그대로 두면 Cowork 전부 502(`01`). `02` 모델 등록은 Claude Code 에서 Opus 5 를 쓸 때도 필요(설치 시드엔 Opus 5 없음) — 단가를 빼먹으면 비용 `$0` 기록·예산 우회. `03` CloudFront 는 도메인 없이 Cowork https 를 만들 때만(US-06 이면 불필요). Claude Code 만 + 시드 모델이면 전체 생략 가능.
- **US-01** — 단일 계정 · us-west-2 · Claude Code · US Geo 추론 기준선.
