# Notification Event Producers — 발행자 현황 및 연결 가이드

`notification-worker`는 Redis Pub/Sub으로 들어오는 `NotificationEvent`를 소비한다.
이 문서는 **각 event_type의 실제 발행자(producer)가 어디에 있는지**, 그리고
**새 발행자를 추가할 때 어느 지점을 연결해야 하는지**를 정리한다.

> 발송 채널(mock/smtp/ses/internal_api) 설정은 [8-W-notifications](../../docs/us-llm-gateway/ops/8-W-notifications.md) 참조.
> 이벤트별 메일 수신자(recipient_roles)는 [recipients.md](./recipients.md) 참조.

---

## 1. 처리 흐름

```
producer (gateway-proxy / admin-api / cost-recorder-worker)
    │  PUBLISH notifications:{channel}  <NotificationEvent JSON>
    ▼
Redis Pub/Sub ──► worker/main.py ChannelListener
    │  parse_pubsub_message() — envelope 검증, 실패 시 폐기 + 로그
    ▼
BaseHandler.handle()
    1. notification_configs 에서 enabled·recipient_roles 조회
       — enabled=false 이면 여기서 종료 (on/off 스위치는 이 값)
    2. recipient_resolver 로 admin/team_leader/affected_user → 이메일 매핑
    3. templates/{event_type}.{locale}.html 렌더링 (없으면 {event_type}.* → default.*)
    4. notification_logs 기록 + RetryExecutor(최대 3회)
```

## 2. Event type별 발행자 현황 (2026-05 기준)

채널→타입 라우팅은 `src/worker/main.py` 의 `_CHANNEL_EVENT_TYPES` 에 정의돼 있다.

| event_type | 채널 | 발행자 위치 | 상태 |
|---|---|---|---|
| `budget_threshold` | `notifications:budget` | `cost-recorder-worker/src/worker/batch_flusher.py` (`_publish_thresholds`) | ✅ live |
| `key_revoked` | `notifications:key` | `admin-api/src/app/services/key_service.py` (`_publish_key_revoked`) | ✅ live |
| `auth_failure_spike` | `notifications:security` | `gateway-proxy/src/app/security/event_detector.py` (IP당 5분/10회 실패) | ✅ live |
| `degradation_mode` | `notifications:system` | `gateway-proxy/src/app/middleware/downgrade.py` (`_publish_degradation_event`) | ✅ live |
| `key_expiring` | `notifications:key` | **없음** — api-key-helper가 VK를 자동 갱신하므로 만료 임박 개념이 없음 | ⚠️ dead |
| `key_expired` | `notifications:key` | **없음** — `admin-api/scheduler/key_expirer.py`는 DB status만 EXPIRED로 갱신, 이벤트 미발행 | ⚠️ dead |
| `permission_violation` | `notifications:security` | **없음** — `gateway-proxy/schemas/domain.py`에 enum 값만 존재 | ⚠️ dead |
| `suspicious_usage` | `notifications:security` | **없음** — 동일 (enum만) | ⚠️ dead |
| `provider_error` | `notifications:system` | **없음** — gateway 응답의 `"provider_error"` 문자열은 클라이언트용 error payload이지 notification 이벤트가 아님 | ⚠️ dead |
| `service_health_change` | `notifications:system` | **없음** | ⚠️ dead |

**주의**: dead 타입도 `notification_configs.enabled`는 seed 기본값 `true`다.
즉 지금은 발행자가 없어 무해하지만, **누군가 producer를 추가하는 순간 알림이
바로 발송되기 시작한다**. 특정 타입을 미리 꺼두려면:

```sql
UPDATE notification.notification_configs SET enabled=false WHERE event_type='...';
```

(seed 파일은 `ON CONFLICT DO NOTHING`이라 이미 초기화된 DB에는 재적용되지 않는다.)

## 3. Envelope 계약

producer는 반드시 이 JSON 구조로 publish해야 한다 (`src/worker/schemas/events.py`):

```json
{
  "event_id": "<uuid>",
  "type": "<event_type — 소문자 wire 값, 예: auth_failure_spike>",
  "timestamp": "<ISO8601, 예: 2026-05-01T12:00:00+00:00>",
  "source": "gateway-proxy | admin-api | cost-recorder-worker",
  "payload": { "...": "핸들러/템플릿이 쓰는 자유 형식" }
}
```

- `type`은 **소문자 문자열**이어야 한다 — 대문자(`"AUTH_FAILURE_SPIKE"`)로 보내면
  pydantic 검증에서 폐기된다 (과거 실제 장애 사례: `test_high_notification_event_envelope`).
- `source`는 `ServiceSource` enum 3값 중 하나. **새 서비스가 producer가 되면
  `schemas/events.py`의 `ServiceSource`에도 값을 추가해야 한다.**
- `payload` 키는 봉투 필수 — payload 없는 평탄한 이벤트는 거부된다.
- 참고 구현: `gateway-proxy/schemas/domain.py` `SecurityEvent.to_envelope()`,
  `admin-api/services/key_service.py` `_publish_key_revoked()`의 event dict.

## 4. 새 producer 연결 체크리스트

### 4-1. 기존 event_type에 발행자를 추가하는 경우 (dead 타입 활성화)

1. 해당 채널로 위 envelope을 `redis.publish` 한다 — worker 측 변경은 **없음**
   (`_CHANNEL_EVENT_TYPES`에 이미 라우팅돼 있음).
2. 발송을 원치 않는 환경이면 `notification_configs.enabled`를 false로 — 코드가 아니라
   설정으로 제어한다.
3. 템플릿 확인: dead 타입 6개는 locale 없는 fallback 템플릿(`{type}.html` +
   `{type}.subject.txt`)만 있다. ko/en 지원이 필요하면
   `src/worker/templates/{type}.ko.html`·`.ko.subject.txt`·`.en.*`를 추가한다
   (없어도 fallback으로 발송은 된다).
4. 예시 — `key_expired` 활성화: `key_expirer.py`의 bulk update 후 발행된 키 목록을
   돌아 `notifications:key`로 이벤트를 publish하면 된다.

### 4-2. 새 event_type을 추가하는 경우

producer 외에 아래 4곳이 **모두** 갱신돼야 한다 — 하나라도 빠지면 이벤트가 조용히
폐기되거나 계약 테스트가 실패한다:

| 위치 | 변경 |
|---|---|
| `notification-worker/src/worker/schemas/events.py` | `EventType` enum에 추가 |
| `notification-worker/src/worker/main.py` | `_CHANNEL_EVENT_TYPES`의 적절한 채널에 추가 (새 채널이면 `main()`의 listener 등록도) |
| `db/init/06_seed_notification_configs.sql` | seed 행 추가 (`recipient_roles`, `enabled`) + **운영 DB에는 별도 INSERT/UPDATE** |
| `notification-worker/src/worker/templates/` | `{type}.html` + `{type}.subject.txt` (최소 fallback) |

5. 계약 테스트: `gateway-proxy/tests/regression/test_high_notification_event_envelope.py`가
   `producer 발행 집합 ⊆ worker EventType`과 `seed == EventType`을 검증한다 —
   새 타입 추가 시 이 테스트가 어긋남을 잡아준다.

## 5. 안티패턴

- ❌ 알림을 끄려고 enum/라우팅에서 타입을 제거 — 계약 위반 + producer가 보내면 폐기됨.
  on/off는 **`notification_configs.enabled`**가 담당한다.
- ❌ `model_dump_json()` 등 envelope 없이 임의 필드를 publish — `payload` 봉투 필수.
- ❌ 발행 실패를 무시 — publish는 fire-and-forget이라 예외를 삼키면 알림이 증발한다.
  기존 producer들처럼 `try/except` + warning 로그는 남길 것.
- ❌ 새 이벤트 채널 추가 후 `main()`의 listener 등록을 잊는 것 — `_CHANNEL_EVENT_TYPES`
  에만 넣으면 구독되지 않는다.
