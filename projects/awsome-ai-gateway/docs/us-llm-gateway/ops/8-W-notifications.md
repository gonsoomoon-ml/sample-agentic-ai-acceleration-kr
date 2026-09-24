# 8-W. Notification 발송 채널 변경

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-W**

`notification-worker`는 Redis Pub/Sub으로 들어오는 이벤트를 받아 이메일로 발송한다. **현재 dev 환경은 `mock`으로 설정돼 있어 실제 메일 발송은 하지 않고 structlog만 기록한다.** 운영이나 실제 메일 수신 테스트를 원하면 `internal_api`(사내 메일 API), `smtp`, `ses` 중 하나로 전환한다.

---

## 발송 흐름

1. `gateway-proxy`·`admin-api`·`cost-recorder-worker`에서 `NotificationEvent`를 Redis Pub/Sub 채널로 publish
   - `notifications:budget`, `notifications:key`, `notifications:security`, `notifications:system`
2. `notification-worker`가 subscribe → `BaseHandler`에서 아래 순서로 처리
   - `notification_configs`에서 `enabled`·`recipient_roles` 조회
   - `recipient_resolver`로 `affected_user`·`team_leader`·`admin` 매핑
   - Jinja2 템플릿 렌더링 (`templates/{event_type}.html`·`.subject.txt`)
   - `notification_logs`에 `pending` → `sent`/`failed` 기록
   - `RetryExecutor`로 최대 3회 재시도 (1s·2s·4s)
3. `EmailSender`에서 실제 발송
   - `mock` — 발송 없음
   - `internal_api` — `EMAIL_API_URL`로 HTTP POST
   - `smtp` — `aiosmtplib`로 외부 SMTP
   - `ses` — AWS SES (boto3 extra 필요, Pod 권한 설정 필요)

---

## 제공자 전환 (권장)

`deployment/scripts/set-notification-provider.sh`가 `values-eks-fargate-<env>.yaml`의 `notificationWorker.email` 블록을 채워준다. values 파일을 직접 고치지 않는다.

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/set-notification-provider.sh <env> <provider>
```

| 제공자 | 용도 | 추가 준비물 | 세부 가이드 |
|---|---|---|---|
| `mock` | 개발/테스트. 발송 안 함. | 없음 | — |
| `internal_api` | 사내 메일 API | 메일 API URL, from address/name | [8-W-c](8-W-internal-api.md) |
| `smtp` | 외부 SMTP 서버 | host, port, STARTTLS, from address, (선택) K8s Secret | [8-W-a](8-W-smtp.md) |
| `ses` | AWS SES | Verified Identity, IRSA/Fargate IAM 권한, from address/name | [8-W-b](8-W-ses.md) |

### 전환 예시

```bash
# 1. mock (기본)
bash deployment/scripts/set-notification-provider.sh dev mock

# 2. 사내 메일 API
bash deployment/scripts/set-notification-provider.sh dev internal_api
# 프롬프트: Internal API URL, From address, From name

# 3. SMTP
bash deployment/scripts/set-notification-provider.sh dev smtp
# 프롬프트: SMTP host, port, STARTTLS, From address, credentials Secret (선택)

# 4. SES
bash deployment/scripts/set-notification-provider.sh dev ses
# 프롬프트: AWS SES region, From address, From name
```

> ℹ️ **notification-worker 기본 이미지는 `mock`/`internal_api`/`smtp`/`ses` 모두 포함한다.** `Dockerfile`이 `http`·`aiosmtplib`·`boto3` extras를 기본 설치하므로, 제공자 전환 시 별도 이미지 rebuild는 필요 없다.

---

## 변경 적용

`set-notification-provider.sh`와 `08-setup-notification-ses-irsa.sh`는 `values` 파일만 고친다. 실제 Pod에 적용하려면:

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/install-eks.sh dev
```

---

## 확인

```bash
kubectl -n llm-gateway logs -l app.kubernetes.io/component=notification-worker --tail=100
```

- `EMAIL_SENDER_TYPE`가 원하는 제공자로 들어갔는지:
  ```bash
  kubectl -n llm-gateway get deploy llm-gateway-notification-worker -o yaml | grep -A2 'EMAIL_'
  ```
- `ses` 선택 시 IRSA 어노테이션이 붙었는지:
  ```bash
  kubectl -n llm-gateway get sa notification-worker -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}'
  ```
- 발송 기록은 DB `notification.notification_logs`에서 확인:
  ```bash
  kubectl -n llm-gateway exec -it deploy/llm-gateway-admin-api -- \
    psql "$DATABASE_URL" -c "SELECT event_type, status, recipient_email, resolved_at FROM notification.notification_logs ORDER BY created_at DESC LIMIT 10;"
  ```

---

## 이메일 국제화(i18n) 및 로캘 설정

`notification-worker`는 `NOTIFICATION_LOCALE` 환경변수(기본 `ko`)에 따라 템플릿을 선택한다.

- 본문: `templates/{event_type}.{locale}.html`
- 제목: `templates/{event_type}.{locale}.subject.txt`
- 지원 로캘: `ko` / `en`
- `TemplateEngine`은 `{event_type}.{locale}.*` 미존재 시 기존 `{event_type}.*` 파일로 폴백한다.

새 다크 모드 한/영 이메일 템플릿은 `notification-worker/src/worker/templates/`에 있으며, `admin-ui/src/app/globals.css`의 Glass 디자인 시스템 색상(#0c0d0f, #111214, #f4f5f6, #2dd4bf 등)을 인라인 CSS로 적용한다. 기존 `.html`/`.subject.txt` 파일은 폴백 목적으로 유지된다.

### `08-setup-notification-ses-irsa.sh`에서 설정

`08-setup-notification-ses-irsa.sh`를 실행하면 SES IRSA 구성과 함께 기본 알림 언어를 묻는 프롬프트가 표시된다(`ko`/`en`, 기본 `ko`). 선택한 값은 `values-eks-fargate-<env>.yaml`의 `notificationWorker.env` 섹션에 `NOTIFICATION_LOCALE`로 기록/갱신된다. dry-run 출력에도 반영된다.

### 수동 설정

`08-setup-notification-ses-irsa.sh`를 사용하지 않고 직접 설정하려면:

```yaml
notificationWorker:
  ...
  env:
    NOTIFICATION_LOCALE: "ko"  # ko | en
```

### 템플릿 변수

모든 템플릿에서 사용 가능한 변수:

- `recipient_name`
- `recipient_email`
- `event`
- `payload`
- `gateway_name`
- `timestamp_kr`
- `locale`

---

## 추가 설정

- **발송 시각 타임존**: `values-eks-fargate-*.yaml` 최상단 `global.reportingTimezone`이 이메일 템플릿에도 사용된다.
- **받는 사람/이벤트 on/off**: `auth.users`의 `role`/`is_active`와 `notification.notification_configs`의 `recipient_roles`·`enabled`를 조정한다.
