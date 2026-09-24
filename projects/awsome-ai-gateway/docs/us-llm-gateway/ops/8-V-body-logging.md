# 8-V. 본문 로깅(body logging) 활성화 — 요청/응답 전문을 S3 에

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-V**

> 📒 **`IN-02` · 등급 선택(감사·디버깅 필요 시)** — [README.md 「최신 업데이트」](../README.md#2-최신-업데이트). 적용 여부는 `deployment/scripts/enable-body-logging.sh <env> --verify` 로 확인한다.

> ⚠️ **무엇이 저장되는지 먼저 알아야 한다.** 켜면 게이트웨이가 **요청 JSON 전문과
> 응답 전문**(스트리밍이면 재구성된 SSE 텍스트)을 S3 에 보낸다 — 사용자가 프롬프트에
> 붙여 넣은 것이 **마스킹 없이** durable 저장소로 나간다. 같은 코드베이스의 trace
> 경로는 PII 마스커가 기본 ON 인데 본문 로거에는 적용되지 않는다(알고 있는 격차,
> 향후 개선 대상). 개인정보·보안 검토 없이 켜지 말 것.

> **신규 설치는 기본 꺼짐이다.** `terraform.tfvars.example` 은 `enable_body_logging`
> 을 주석으로 두고 변수 기본값도 `false` 라, 예시를 복사해 설치하면 sink 자체가
> 만들어지지 않는다. 처음부터 켜려면 주석을 해제하고 첫 apply 때 함께 만들어도
> 되고, 나중에 켜도 이 절의 절차는 신규·기존 동일하다(스크립트가 하는 일이
> tfvars 설정 + apply + helm env 주입이라 구분이 없다).

**왜 존재하는가** — Mantle 엔드포인트(`bedrock-mantle.{region}.api.aws`) 트래픽은
AWS model invocation logging 에 **전혀** 잡히지 않는다(실측: runtime 은 기록,
Mantle 은 0건). Codex·Cowork 가 그 평면을 쓰므로 그 트래픽의 본문 감사는 이 sink 가
유일한 정본이다.

**잠금이 두 겹이다 — 둘 다 열려야 수집된다.** 토글만으로는 절대 켜지지 않는다.

| 잠금 | 위치 | 누가 여나 |
| --- | --- | --- |
| ① 인프라 | S3 버킷 + Firehose + IAM(terraform module `body_logging`) + `gatewayProxy.env` 의 `FIREHOSE_STREAM_NAME`/`BODY_LOG_S3_BUCKET` | **이 절** (`enable-body-logging.sh`) |
| ② 런타임 | `public.system_settings` 의 `body_logging_enabled` (기본 OFF) | 관리자가 `/monitoring` 토글로 — audit_logs 에 불변 기록 |

런타임 토글의 내부는 이렇다: admin-api 가 DB upsert + Redis `bodylog:enabled`
write-through → 게이트웨이는 요청당 인프로세스 캐시(TTL 기본 5초)로 읽고, 캐시 만료
시 Redis → 미스면 DB 에서 rehydrate → 에러 시 **OFF(fail-safe)**. 즉 토글 반영은
워커당 최대 ~5초이고, 장애가 나면 수집이 멈추는 쪽으로 실패한다.

**무엇이 바뀌나**

| | 기본 (OFF) | 이 절 적용 + 토글 ON |
| --- | --- | --- |
| Firehose 스트림 | 없음 | `<project>-<env>-body-logs` (provider/client/날짜 파티셔닝, gzip) |
| S3 버킷 | 없음 | `<project>-<env>-gateway-body-logs-<account>` — SSE, public 차단, lifecycle 90일 만료 |
| 요청 본문 | 저장 안 함 | Firehose → S3 (1MB 초과 레코드는 버킷 직행) |
| admin 토글 | 켜도 아무 일 없음(인프라 게이트 닫힘) | 실제 수집 on/off 로 동작 |

> ★ 버킷 이름 함정 — 이 버킷(`…-gateway-body-logs-…`)과 AWS 네이티브 invocation
> logging 의 `…-bedrock-invlogs-<account>-<region>` 은 **별개**다. 이름이 한 단어
> 차이라 대조할 때 바꿔 넣으면 "테이블은 정상, 스캔 0건"이 된다.

**(1) 대상 상태 확인** — 읽기 전용이다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/enable-body-logging.sh dev
```

tfvars 의 `enable_body_logging` · terraform output · 파드 env 를 찍고 `terraform
plan -target` 을 보여준다. 여기까지 아무것도 바꾸지 않는다.

**(2) 적용**

▶ **실행** · 배포 EC2

```bash
bash deployment/scripts/enable-body-logging.sh dev --apply
```

스크립트가 순서대로 한다 — `terraform.tfvars` 에 `enable_body_logging = true`
반영(없으면 추가) → `terraform plan/apply -target=module.body_logging
-target=module.irsa` → output 에서 스트림/버킷 이름 추출 → values 의
`gatewayProxy.env` 에 두 키 주입 → `helm upgrade` → gateway-proxy 롤아웃.

> ℹ️ `-target` 을 쓰는 이유는 오래 운영한 배포의 무관한 드리프트가 같이 적용되는
> 것을 막기 위해서다(8-N 절의 사례: VPC 엔드포인트 추가 때 DB 시크릿 replace 가
> 딸려 나옴). IRSA 는 게이트웨이에 `firehose:PutRecordBatch` + 버킷 `s3:PutObject`
> 권한을 얹는다 — 새 권한이므로 같이 타깃한다.
>
> 🔴 **plan 에 add 외 change/destroy 가 보이면 멈춘다.** body-logging 모듈과
> 무관한 diff 는 이 작업의 범위가 아니다.

**(3) 검증**

```bash
bash deployment/scripts/enable-body-logging.sh dev --verify
```

버킷 존재 · 스트림 `ACTIVE` · 파드 env 설정을 확인한다. `READY` 가 나오면 인프라
게이트는 열린 것이다 — **수집은 아직 꺼져 있다.**

**(4) 수집 시작/중지 — admin 토글**

`/monitoring` → body logging 토글. 켜는 쪽은 확인 대화상자가 빨간색(파괴적)으로
뜬다 — 프라이버시에 영향을 주는 방향이기 때문이다. 반영은 ~5초 내, 켜는 조작은
`audit.audit_logs` 에 불변 행으로 남는다. 저장되는 형태:

```
s3://<bucket>/provider=<p>/client=<c>/dt=<YYYY-MM-DD>/<records>.gz
```

**(5) 끄기**

수집만 멈추려면 admin 토글 OFF — 가장 빠르고 되돌리기 쉽다.

인프라 게이트까지 닫으려면(토글을 켜도 수집 불가 상태로):

```bash
bash deployment/scripts/enable-body-logging.sh dev --disable
```

env 두 개를 values 에서 제거하고 재배포한다. **S3 버킷과 Firehose 는 지우지
않는다** — 버킷엔 프롬프트 본문이 있을 수 있어(`force_destroy=false` 라 terraform
destroy 도 객체가 있으면 실패한다) 객체 비우기 → `enable_body_logging = false` →
apply 는 운영자가 명시적으로 결정한다.

**함정 모음**

- **토글을 켰는데 아무것도 안 나온다** — 인프라 게이트가 닫혀 있다. env 두 개가
  파드에 있는지 `--verify` 로 확인. 토글은 ①이 열린 뒤에만 의미가 있다.
- **dev 라고 안심 금물** — 켜면 dev 사용자의 프롬프트도 그대로 저장된다.
  lifecycle 기본 90일(`body_log_retention_days`)이라 테스트 후에는 버킷 정리를
  잊지 말 것.
- **레코드 1MB 상한** — Firehose hard limit 이라 초과분은 `BODY_LOG_S3_BUCKET` 직행.
  이 버킷이 비어 있으면 초과 레코드는 조용히 버려진다(요청은 영향 없음).
- **런타임으로 버킷을 만들게 할 수는 없다** — BodyLogger 는 기동 시 env 로만
  구성되고, 버킷 생성 권한을 프록시 IRSA 에 주는 것은 과하다. 인프라는 terraform
  이 담당한다(두 겹 잠금의 이유).
- **롤백 순서** — 수집 중지는 토글 OFF 가 먼저다. terraform 쪽을 먼저 지우면
  로거가 죽은 스트림에 쓰려다 enqueue 실패 로그만 쌓인다(요청 자체는 영향 없음).
