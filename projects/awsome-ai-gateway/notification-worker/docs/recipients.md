# Notification Recipients — 이벤트별 메일 수신자

`notification-worker`는 이벤트를 받으면 `notification.notification_configs` 의
`recipient_roles` 를 보고 **누구에게 보낼지**를 결정한다.
이 문서는 역할 해석 규칙, 이벤트별 현재 설정, 변경 방법을 정리한다.

> 각 event_type의 발행자(producer)와 live/dead 상태는
> [event-producers.md](./event-producers.md) 참조.
> 발송 채널(mock/smtp/ses) 설정은
> [8-W-notifications](../../docs/us-llm-gateway/ops/8-W-notifications.md) 참조.

---

## 1. 역할(role) 해석 규칙

`src/worker/services/recipient_resolver.py` 가 3가지 역할을 지원한다.

| role | 해석 | 비고 |
|---|---|---|
| `affected_user` | `payload.user_id` → `auth.users` | `is_active` 사용자만. 없으면 스킵 |
| `team_leader` | `payload.team_id` → `auth.teams.leader_user_id` → `auth.users` | `team_id` 없으면 user의 소속 팀으로 폴백. **리더 미지정 팀이면 발송 안 함**(BR-RCP-04) |
| `admin` | `auth.users` 전체 조회 | `role='ADMIN'` + `is_active` 인 **모든** 사용자 |

공통 규칙:

- **중복 제거**(BR-RCP-03): 같은 이메일이 여러 역할에 잡히면 1통만 발송.
  예: 사용자가 자기 팀 리더이면 `affected_user`+`team_leader` 양쪽에 걸려도 1통.
- 역할 해석 실패는 경고 로그만 남기고 나머지 수신자에게는 발송을 계속한다.
- 설정에 없는 역할 문자열은 `unknown_recipient_role` 경고 후 무시된다.

## 2. 이벤트별 수신자

| event_type | 본인 | 팀 리더 | Admin | 상태 | 비고 |
|---|:-:|:-:|:-:|:-:|---|
| `budget_threshold` | ✅ | ✅* | | live | 유일하게 팀 리더에게 가는 이벤트. *리더 미지정 팀은 리더 메일 없음 |
| `key_revoked` | ✅ | | ✅ | live | |
| `auth_failure_spike` | | | ✅ | live | |
| `degradation_mode` | ✅* | | ✅ | live | *seed는 admin만 — dev DB에 affected_user 추가됨(§4 참고) |
| `key_expiring` | ✅ | | | dead | producer 없음 — api-key-helper가 자동 갱신 |
| `key_expired` | ✅ | | | dead | producer 없음 — key_expirer는 DB status만 갱신 |
| `permission_violation` | | | ✅ | dead | producer 없음 — enum 값만 존재 |
| `suspicious_usage` | | | ✅ | dead | producer 없음 — enum 값만 존재 |
| `provider_error` | | | ✅ | dead | producer 없음 — 게이트웨이의 "provider_error"는 클라이언트용 error payload |
| `service_health_change` | | | ✅ | dead | producer 없음 |

패턴으로 보면:

- **사용자 리소스 이벤트**(예산·API 키): 본인 중심. admin은 `key_revoked`만.
- **시스템/보안 이벤트**(인증 급증·권한 위반·프로바이더 오류·의심 사용): admin 전용.
- `budget_threshold`만 `team_leader` 역할을 사용한다.

**상태** 컬럼은 producer(발행자) 존재 여부다 — `live`는 실제 publish 중,
`dead`는 설정만 있고 발행자가 없다
([event-producers.md §2](./event-producers.md)의 producer 위치 참조).
⚠️ dead 타입도 `enabled=true`라서, 누군가 producer를 추가하는 순간
**위 수신자 설정대로 발송이 바로 시작된다**.

## 3. 설정 위치

```text
notification.notification_configs
  event_type       TEXT PK   — worker EventType 과 1:1
  recipient_roles  JSONB     — ["affected_user","team_leader","admin"] 중 조합
  enabled          BOOL      — false면 이벤트 수신 즉시 종료(역할 조회도 안 함)
```

seed 기본값은 `db/init/06_seed_notification_configs.sql`에 있다 —
단 **`ON CONFLICT DO NOTHING`** 이라 이미 초기화된 DB에는 재적용되지 않는다.
운영 DB의 현재 값이 진실의 원천이며 seed와 다를 수 있다(예: dev의
`degradation_mode`는 `["affected_user","admin"]`).

## 4. 수신자 변경 방법

worker는 `ConfigCache`로 설정을 메모리에 올려 쓴다 — DB UPDATE 후 두 경로로 반영된다:

1. **즉시**: `notifications:config_reload` 채널에 publish하면 캐시 reload
2. **자동**: 5분 주기 DB 폴링 (`_POLL_INTERVAL`)

```sql
-- 예: budget_threshold 를 admin에게도 보내기
UPDATE notification.notification_configs
SET recipient_roles = '["affected_user","team_leader","admin"]'
WHERE event_type = 'budget_threshold';
```

```bash
# 캐시 즉시 갱신 (5분 기다리지 않으려면)
redis-cli -u "$REDIS_URL" PUBLISH notifications:config_reload '{}'
```

또는 admin-api의 알림 설정 API가 있으면 그쪽 경로를 쓴다(그 API가
`config_reload` publish까지 수행).

⚠️ enabled는 수신자 on/off가 아니라 **이벤트 자체의 on/off**다. 수신자만
바꾸려면 `recipient_roles`를 수정하고 `enabled`는 건드리지 않는다.

## 5. 안티패턴

- ❌ 수신자를 코드(`RecipientResolver`)에서 하드코딩으로 추가 — 설정이 DB에
  있는 이유가 운영 중 무중단 변경이다.
- ❌ 특정 사용자만 빼려고 `recipient_roles`를 건드림 — 개인 수신 거부는 현재
  개념이 없다. 필요하면 `is_active` 또는 별도 opt-out 설계가 먼저다.
- ❌ `affected_user`를 기대하고 `payload.user_id` 없는 이벤트를 발행 —
  수신자 0명으로 `no_recipients_resolved` 경고만 남고 아무에게도 안 간다.
