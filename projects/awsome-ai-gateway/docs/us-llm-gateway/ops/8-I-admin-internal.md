# §8-I. admin ALB 2개를 internal 로 — 고객사 최종형 (US-07)

> 컨트롤 플레인(admin-api·admin-ui) ALB 를 private 서브넷의 internal ALB 로 내려 **S2S VPN 으로만**
> 접근하게 한다. 데이터 플레인(gateway)은 public 유지. 설계 그림·근거:
> [architecture.md 「고객사 최종 아키텍처」](../architecture.md#고객사-최종-아키텍처-목표형--admin-alb-2개를-private-으로).

## 언제

- **전제 = S2S VPN** (사용자망 → VPC 라우팅). VK 발급(`api-key-helper` → admin-api)도 이 경로를
  타므로 **VPN 없이 적용하면 게이트웨이 사용 자체가 불가** — 개통 전 적용 금지.
- **신규 설치**: `US-01`([install-guide.md](../install-guide.md)) 의 **§3-6 시점**(values 채우기,
  §3-7 설치 실행 전)에 values 주석만 해제하면 admin ALB 가 처음부터 internal 로 만들어진다 —
  아래 전환 절차는 불필요. (§3-6 본문에 같은 안내가 있다.)

## 절차 (기존 배포의 전환)

0. 백업 — values 백업 + 현재 admin ALB DNS 2개 기록
1. values 주석 해제 — `adminUi`·`adminApi` 의 `alb.ingress.kubernetes.io/scheme: internal`
   (values 파일 안 주석 참조 · terraform 은 무변경 — vpc 모듈이 private 서브넷·태그를 이미 만든다)
2. `install-eks.sh <env>` → 컨트롤러가 admin ALB 2개를 **재생성** (admin 만 수 분 단절, gateway 무영향)
3. admin SG 인바운드를 **온프레 CIDR** 로 교체 — internal 후 소스가 사설 IP 라 기존 공인 IP 룰은 무효
4. Route53 의 admin CNAME 2개 값을 새 internal ALB DNS 로 교체
   (방식 A 로 도메인 없이 쓰는 중이면 클라이언트의 `ADMIN_API_URL`/admin 주소를 직접 교체)
5. 검증 — VPN 경유 admin-ui 로그인 + VK 발급, gateway 추론 정상

## 롤백

values 주석을 되돌리고 다시 `install-eks.sh` → internet-facing 으로 재생성 → CNAME 원복.

## 확인된 사실 (2026-08-20 리허설, US 배포)

- `scheme: internal` 만으로 컨트롤러가 private 서브넷(`internal-elb` 태그 자동 발견)에 ALB 를 만들고,
  VPC 내부에서 admin-api 200 응답까지 확인했다. 남은 검증은 고객망 → VPC 라우팅(S2S VPN)뿐이다.
- internal ALB 의 DNS 이름은 공개 해석되어 사설 IP 를 반환한다 — CNAME 방식이 그대로 성립한다.
