# 업데이트 이력 (US-NN)

**한국어** · [English](updates.en.md)

README 의 「최신 업데이트」는 최근 5개만 보여준다 — 여기가 전체 이력이다. `US-NN` 은 리베이스에 영향받지 않는 고정 ID. 적용 상태는 배포 EC2 에서 `bash status.sh`.
등급 — **필수**: 반드시 · **권장**: 안 하면 그 기능 동작 안 함 · **선택**: 요구 있을 때

| ID (문서) | 무엇 | 등급 · 신규 설치 | 기존 배포가 할 일 |
|---|---|---|---|
| [**US-10**](ops/8-L-admin-ui-login.md) 2026/08 | Admin UI Cognito 로그인 — dev-login 대체 | 선택 · **운영이면 강력 권장** · 신규 포함(별도 적용) | 이미지 재빌드 → `setup-admin-ui-login.sh` → install-eks → `devLoginEnabled=false` |
| [**US-09**](cowork/installer/cowork-installer-admin-e2e-windows.md) 2026/08 | Cowork Windows 설치기 — 관리자가 .exe 1개 빌드 → 직원 PC 설치(HKLM 정책) | 선택 · Cowork Windows 쓰면 권장(수동 설치 대체) · 게이트웨이 변경 없음 | 빌드 PC 에서 `feat/cowork-installer-import` clone → `07-client-values.sh` 값으로 `site-config.json` → `build.ps1` → 직원 PC 설치 + `setup` |
| [**US-08**](ops/8-P-prod.md) 2026/08 | prod 스택 신설 — 별도 계정 · https + admin internal + VPN · Cowork Windows | 선택 · POC 이후 운영 전환 시 · `environment=prod` | dev 는 그대로 두고 prod 계정에 §1~§6 재실행(8-P 순서) |
| [**US-07**](ops/8-I-admin-internal.md) 2026/08 | 고객사 최종 아키텍처 — admin ALB 2개를 internal 로 | 선택 · 전제 S2S VPN · POC 신규는 §3-6 시점에 values 주석 해제 · 운영(`US-08`)은 포함 | values 주석 2곳 해제 → helm(ALB 재생성) → admin SG·CNAME 교체 |
| [**US-06**](ops/8-H-alb-https.md) 2026/08 | ALB HTTPS — 커스텀 도메인 + ACM 인증서 | 선택 · POC 는 도메인 있을 때 · 운영(`US-08`)은 포함 | 도메인 확보 → 전환 → 클라이언트 URL 2개 교체 (약 30분) |
| [**US-05**](ops/8-E-eks-upgrade.md) 2026/08 | EKS 1.31 → 1.34 | 필수(지원 만료·비용) · 신규 포함 | 1단계씩 3회 apply + 전 ns 파드 재시작 |
| [**US-04**](ops/8-N-vpc-endpoint.md) 2026/08 | Bedrock·STS 를 NAT 대신 VPC Endpoint 로 | 필수(컴플라이언스) · 신규 포함 | 엔드포인트 apply → gateway-proxy 재시작 |
| [**US-03**](ops/8-U-update.md) 2026/08 | Admin UI 한/영 토글 | 필수(영문 지원) · 신규 포함 | admin-ui 이미지 재빌드 → install-eks |
| [**US-02**](update-scripts/README.md#실행-순서) 2026/08 | Cowork 연결 + Opus 5 등록 | 항목별 — Cowork 쓰면 `01`·`03`, Opus 5 쓰면 `02` 필수 · 🔴 **신규도 해당** | 01 라우팅 · 02 모델(단가 필수) · 03 CloudFront(도메인 없을 때만) |
| [**US-01**](install-overview.md) 2026/07 | 최초 설치 (기준선) | — | — |

## 왜 · 함정 (항목별)

- **US-10** — admin-ui 로그인을 dev-login(role 을 화면에서 직접 골라 서명 없는 쿠키를 발급하는 MVP 우회)에서 실제 Cognito 로그인(이메일/비밀번호)으로 전환. `ClaudeAdmin` 그룹은 그대로 ADMIN 을 자동 부여하지만, TEAM_LEADER 는 Cognito 그룹이 아니라 admin-ui `/users` 화면에서 관리자가 수동으로 지정한다(그룹 두 개를 맞춰야 하는 운영 부담과 오배정 위험 제거). 세션 서명용 RSA 키쌍을 새로 발급해야 하며(`admin-api/scripts/generate_admin_jwt_keypair.py`), `deployment/scripts/setup-admin-ui-login.sh <env>` 가 키 생성부터 DB(`auth.admin_jwt_configs`)·Secret 반영·values 패치까지 자동화한다. dev-login 은 `global.devLoginEnabled` 로 켜둔 채 병행 가능 — 실제 로그인 확인 후에 꺼도 된다.
- **US-09** — Cowork Windows 설치기: 수동 가이드(레지스트리 6키 손입력) 대신 관리자가 **설치 파일 1개**를 빌드해 배포한다 — 빌드 PC(`site-config.json` = `07-client-values.sh` 출력) → `gateway-cli-cowork-setup-<ver>.exe` → 직원 PC 에서 설치 + `gateway-cli-cowork setup`(HKLM `Policies\Claude` inference* 6키, 머신 전역) → 사용자는 Claude Desktop(offline .msix) + `login`. 게이트웨이·차트 변경 없음. 함정: HKLM 정책이라 한 PC 에 dev·prod 공존 불가 · Cowork 샌드박스는 Hyper-V 필요(EC2 면 metal + `VirtualMachinePlatform`·`Containers`) · `winget` 은 `--source winget` 명시 · 코드는 `feat/cowork-installer-import` 브랜치. 순서: [관리자 E2E](cowork/installer/cowork-installer-admin-e2e-windows.md) → [빌드](cowork/installer/cowork-installer-build-windows.md) · [사용자](cowork/installer/cowork-installer-user-windows.md) · [제거](cowork/installer/cowork-installer-uninstall-windows.md).
- **US-08** — prod 승격: dev 를 바꾸는 게 아니라 **별도 계정에 prod 스택을 새로** 세운다(`environment = "prod"` 한 줄 = HA 사이징). 처음부터 https(US-06)+admin internal(US-07) 로 세우므로 **VPN 이 먼저** — 없으면 VK 발급이 막힌다(검증은 AWS Client VPN 으로 대체). 함정: 네트워크 CIDR 은 dev 와 겹치면 안 됨(VPN 라우팅) · prod values 이미지 태그·`elasticache_endpoint`(cluster mode) 는 2026-08 수정본 필요 · Cowork Windows 테스트 머신은 metal(Hyper-V). 상세 [8-P §5](ops/8-P-prod.md#5-함정-모음-검증-계정-실측).
- **US-07** — 고객사 최종형: 컨트롤 플레인(admin-api·admin-ui) ALB 를 private 서브넷 internal 로 내려 S2S VPN 으로만 접근(데이터 플레인 gateway 는 public 유지). terraform 무변경 — vpc 모듈이 서브넷·태그를 이미 만든다. 신규 설치는 `US-01` 의 §3-6 시점에 values 주석 해제로 처음부터 internal(별도 절차 없음, install-guide §3-6 안내 참조), 운영 중 배포는 [§8-I 전환 절차](ops/8-I-admin-internal.md) — ALB 재생성이라 admin CNAME 교체 + 수 분 단절. ⚠️ S2S VPN 없이 적용하면 VK 발급(api-key-helper → admin-api)이 끊겨 게이트웨이 사용 자체가 불가 — VPN 개통 전 적용 금지. internal ALB 생성·in-VPC 통신은 리허설로 실증(2026-08-20).
- **US-06** — ALB 3개를 http:80(임시 ALB 주소) 대신 `https://gateway-<env>.<도메인>` 으로. ACM TLS 종료·고정 이름, Cowork 용 CloudFront 불필요. 등록이 막힌 계정(Amazon 내부 등)은 타 계정 등록 + NS 위임. 적용 후 `ANTHROPIC_BASE_URL`·`ADMIN_API_URL` 교체.
- **US-05** — 1.31 은 표준 지원 종료로 연장 요금(클러스터당 월 ~$365) · 최종 종료(2026-11-26) 후 강제 자동 업그레이드. 마이너 1단계씩만(3회 apply), 단계마다 전 ns 파드 재시작(Fargate 는 파드=노드).
- **US-04** — Bedrock·STS 호출이 NAT·퍼블릭 인터넷 대신 VPC 내부 PrivateLink 로. 엔드포인트 선언이 들어가기 전에 만든 VPC 만 대상(신규는 이미 포함) — Bedrock 은 계속 성공하니 아무도 안 알려준다. 적용 직후 gateway-proxy 재시작 필수(풀에 남은 죽은 소켓 → 연속 502 를 엔드포인트 탓으로 오진).
- **US-03** — 관리 화면 i18n, 헤더 KO/EN 토글이 실제로 번역. admin-ui 이미지 재빌드가 필요.
- **US-02** — 설치 마이그레이션이 Cowork 라우팅 행을 존재하지 않는 계정으로 심어 그대로 두면 Cowork 전부 502(`01`). `02` 모델 등록은 Claude Code 에서 Opus 5 를 쓸 때도 필요(설치 시드엔 Opus 5 없음) — 단가를 빼먹으면 비용 `$0` 기록·예산 우회. `03` CloudFront 는 도메인 없이 Cowork https 를 만들 때만(US-06 이면 불필요). Claude Code 만 + 시드 모델이면 전체 생략 가능.
- **US-01** — 단일 계정 · us-west-2 · Claude Code · US Geo 추론 기준선.
