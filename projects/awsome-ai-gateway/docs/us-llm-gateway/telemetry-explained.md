# Claude Code 텔레메트리(OTEL)는 무엇인가 (초보자용)

> [install-guide.md §6](install-guide.md#6-클라이언트-설치--claude-code-awsome-gateway-cli) 의 `gateway-cli setup` 은 게이트웨이 연결과 **함께 텔레메트리(OpenTelemetry, OTEL)도 켠다.** 이게 뭐고, 내 직원 데이터가 어디로 가는지, 켜고 끄는 결정을 어떻게 하는지 설명한다.

---

## 한 문장

`setup` 은 **직원 Claude Code 의 사용 지표를 회사가 수집**하도록 켠다. **하지만 이 배포에선 받는 쪽이 안 열려 있어 아무 데도 안 간다** — 켜려면 관리자가 명시적으로 배관을 이어야 하고, 그건 **프라이버시 결정**이다.

---

## 텔레메트리가 뭔가

OpenTelemetry(OTEL)는 프로그램이 **"내가 뭘 했는지"를 지표·추적으로 내보내는 표준**이다. Claude Code 를 켜면 이런 걸 수집할 수 있다:

- **지표(metrics)**: 요청 수, 토큰 사용량, 세션 시간, 활성 사용자 수 …
- **추적(traces) · 코드 활동**: 어떤 명령을 썼는지, 어떤 도구를 호출했는지 (`ENHANCED_TELEMETRY_BETA`)

즉 **"누가·얼마나·무엇을"** 을 회사가 대시보드로 보게 하는 기능이다. 개인 Claude 와 달리 **회사가 직원 사용 현황을 파악**하려는 용도다.

---

## setup 이 실제로 켜는 것

`gateway-cli setup` 은 managed-settings 에 이 env 를 박는다(`managed.py:115-122`):

| env | 뜻 |
|---|---|
| `CLAUDE_CODE_ENABLE_TELEMETRY=1` | 텔레메트리 **on** |
| `OTEL_METRICS_EXPORTER=otlp` · `OTEL_TRACES_EXPORTER=otlp` | 지표·추적을 OTLP 로 내보냄 |
| `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` | 코드 활동까지(베타) |
| `OTEL_EXPORTER_OTLP_ENDPOINT=http://<게이트웨이>:4317` | **여기로 쏜다** |

`--otel-endpoint` 를 안 주면 게이트웨이 호스트의 **4317 포트**로 자동 유도한다.

---

## 데이터가 어디로 흐르나 (의도된 그림)

```
   직원 PC (Claude Code)
        │  사용 지표·추적
        │  OTLP/gRPC → <게이트웨이 호스트>:4317
        ▼
   ┌──────────────────────────────────────────┐
   │  otel-collector   (클러스터 안)            │
   │  0.0.0.0:4317 수신                         │
   └──────────────────────────────────────────┘
        │  prometheusremotewrite
        ▼
   Prometheus (kps-prometheus:9090)
        │
        ▼
   Grafana 대시보드  ← 관리자가 사용 현황을 본다
```

이게 **벤더가 의도한** 그림이다. 직원 지표가 회사 관측 스택(Prometheus·Grafana)으로 모인다.

---

## 왜 이 배포에선 "무해"한가

위 그림의 **첫 화살표가 끊겨 있다.** 직원 PC 가 쏘는 곳과 받는 곳이 안 이어졌다:

```
   직원 PC ──쏨──►  <게이트웨이 ALB>:4317
                          │
                          ✕  받는 리스너가 없다
                             · ALB 는 80(HTTP)만 연다 (values listen-ports)
                             · otel-collector 는 클러스터 내부
                               (otel-collector.observability.svc:4317) 에만 있다
                             · 게이트웨이 ALB 에 4317 을 노출한 적 없다
                          ▼
                    export 가 조용히 실패 → 데이터 어디로도 안 감
```

즉 **env 는 켜졌지만 배관이 안 이어져** 직원 데이터가 실제로 나가지 않는다. `setup` 이 이 배관까지 열어주지는 않기 때문이다.

> ⚠️ **"무해"는 지금 상태 한정이다.** 나중에 게이트웨이 ALB 에 4317 리스너를 열면 **그 순간부터 실제로 흐르기 시작한다.** env 는 이미 켜져 있으므로, 관리자가 배관만 이으면 별도 재설치 없이 수집이 시작된다 — 이건 **프라이버시 결정**이니 의식하고 열어야 한다.

---

## 관리자의 선택 — 켤까, 끌까

| | 하는 일 | 결과 |
|---|---|---|
| **그대로 둔다** (기본) | 아무것도 안 함 | env 는 켜져 있으나 배관 없음 → 데이터 안 나감 |
| **실제로 켠다** | 아래 "실제로 켜려면" 참고 — **포트만 여는 게 아니다** | 직원 사용 지표가 Grafana 로 → **프라이버시 고지 필요** |
| **완전히 끈다** | `setup` 후 managed-settings 에서 `OTEL_*`·`CLAUDE_CODE_*TELEMETRY*` env 삭제 | 직원 PC 가 아예 텔레메트리를 안 켬 |

> ℹ️ **CLI 로는 못 끈다** — `setup --otel-endpoint ""` 를 줘도 빈 문자열이라 게이트웨이 호스트로 다시 유도된다(`setup.py`). 끄려면 **setup 후 파일에서 직접 지우는** 수밖에 없다. 대량 배포면 managed-settings 를 그렇게 편집한 버전을 MDM/GPO 로 푸시한다.

---

## 실제로 켜려면 — "포트만 열면 되나?" (아니오)

"게이트웨이 ALB 에 4317 을 열면 되지 않나?" 로는 **안 된다.** 이 배포 구성에서 벽이 4개다:

**① otel-collector 가 클러스터 내부 전용(ClusterIP)** — Service 에 타입 지정이 없어 밖에서 못 닿는다. Ingress/LoadBalancer 로 외부에 내보내야 한다.

**② 프로토콜이 gRPC → HTTPS 가 선결** — Claude Code 는 `OTEL_EXPORTER_OTLP_PROTOCOL=grpc` 로 4317(gRPC)에 쏜다. gRPC 는 **HTTP/2 전용**이고, ALB 의 gRPC 는 **HTTPS(TLS) 필수**(평문 HTTP 불가)다. 그런데 이 배포는 [§0](install-overview.md#0-이번-배포의-범위-확정)에서 **HTTPS 를 안 쓰기로** 결정했다(도메인·ACM 없음, IP 제한만). → **정면충돌**: 텔레메트리를 밖에 열려면 HTTPS 부터 도입해야 한다.

**③ 인증이 없다** — 그대로 열면 **누구나 가짜 지표를 쏠 수 있다.** `setup` 에 `--otel-auth-token` 파라미터가 있지만(`managed.py:123` → `OTEL_EXPORTER_OTLP_HEADERS: Authorization=Bearer …`) onboard 스크립트가 그걸 **안 넘긴다.** 토큰 발급·주입 체계를 따로 세워야 한다.

**④ IP 제한** — 직원 PC IP 가 그 리스너의 허용 대역 안이어야 한다([operations.md §8-S](operations.md#8-s-배포-후-보안-하드닝-직원-오픈-전-필수)과 같은 문제).

즉 "실제로 켠다" = **HTTPS 도입(§0 뒤집기) + collector 외부노출 + gRPC/HTTPS ALB + 인증 토큰 + IP 허용 + 프라이버시 고지.** 포트 하나가 아니라 **인프라 결정**이다.

데이터가 흐르려면 **경로 위의 관문 ①~⑤ 가 전부** 갖춰져야 한다. 하나라도 없으면 거기서 끊긴다:

```
   경로 (위 → 아래)              그 지점에 필요한 것 (지금 상태)
  ═══════════════════════════════════════════════════════════════

   직원 PC · Claude Code
        │
        │   OTLP / gRPC (HTTP/2)  ......  ✅ setup 이 이미 켬
        │
        ▼
   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   ④ 직원 PC IP 가 허용 대역 안   (§8-S)
        │
        ▼
   게이트웨이 ALB / Ingress
        │                            ✕ 지금 HTTP:80 만 · 4317 리스너 없음
        │                            ② HTTPS 리스너(TLS)  ← gRPC 는 TLS 필수
        │                               = ACM 인증서 + 도메인 (§0 뒤집기)
        │                               + ALB 를 gRPC(HTTP/2) 로 설정
        │                            ③ 인증 토큰 검사
        │                               (--otel-auth-token, 온보딩 스크립트 수정)
        ▼
   otel-collector
        │                            ① 외부로 노출
        │                               ✕ 지금 ClusterIP = 클러스터 내부 전용
        │                               → LoadBalancer/Ingress 로 변경
        │   prometheusremotewrite
        ▼
   Prometheus
        │
        ▼
   Grafana  ── 관리자가 지표를 본다   ⑤ 직원에게 프라이버시 고지

  ═══════════════════════════════════════════════════════════════
   ✕ 지금은 두 번째 관문(ALB 4317)부터 막혀 첫 화살표에서 끊긴다.
     ①~⑤ 를 전부 갖춰야 끝까지 흐른다 — 포트 하나가 아니라 인프라 결정.
```

### 더 쉬운 대안 (권장) — 게이트웨이를 안 거친다

직원 지표를 **게이트웨이 ALB→collector** 로 우회시키지 말고, **회사 관측 엔드포인트로 직접** 보낸다:

```bash
gateway-cli setup --gateway-url <게이트웨이> --admin-api-url <admin-api> \
  --otel-endpoint https://<CloudWatch OTLP · Datadog · Honeycomb 등>:443 \
  --otel-auth-token <토큰>
```

- Claude Code 가 **회사 관측 SaaS 나 AWS CloudWatch OTLP** 로 직접 쏜다 — HTTPS·인증이 그쪽에 이미 있다.
- 게이트웨이 인프라(ALB·collector)를 **안 건드린다.** 그건 **서버측**(gateway-proxy) 지표용이고, **클라이언트측**(직원 Claude Code) 지표는 별개로 두는 게 깔끔하다.
- 이 배포의 §0(HTTPS 미사용) 결정과도 **충돌하지 않는다** — 게이트웨이는 HTTP 그대로, 텔레메트리만 외부 HTTPS 엔드포인트로.

---

## 한눈에 (요약 카드)

| 질문 | 답 |
|---|---|
| setup 이 텔레메트리를 켜나? | **켠다** (env 를 managed-settings 에 박음). |
| 직원 데이터가 지금 나가나? | **아니다.** 게이트웨이에 4317 리스너가 없어 배관이 끊김. |
| 무엇이 수집되나(켜면)? | 요청·토큰·세션 지표 + 코드 활동(베타). |
| 어디로 가나(켜면)? | otel-collector → Prometheus → Grafana (회사 관측 스택). |
| 실제로 켜려면? | 게이트웨이 ALB 에 4317 리스너 추가 → **프라이버시 고지**. |
| 끄려면? | managed-settings 에서 `OTEL_*`·`CLAUDE_CODE_*TELEMETRY*` 삭제(CLI 로는 불가). |
