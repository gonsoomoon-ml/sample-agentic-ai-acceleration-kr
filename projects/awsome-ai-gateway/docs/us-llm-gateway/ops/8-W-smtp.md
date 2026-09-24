# 8-W-a. SMTP 발송 설정

`notification-worker`에서 외부/고객 SMTP 서버를 통해 이메일을 발송할 때의 설정입니다.

---

## 제공자 전환 (권장)

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/set-notification-provider.sh <env> smtp
```

프롬프트:
- SMTP host
- SMTP port (기본 587)
- STARTTLS 사용 여부 (기본 true)
- From address
- credentials Secret 이름 (선택)

---

## 세부 설정

- `startTls: true`이면 `aiosmtplib`이 `STARTTLS`를 사용합니다. 일반적으로 587 포트에서 사용합니다.
- `startTls: false`이면 암호화되지 않은 plain SMTP를 사용합니다. (내부 릴레이나 로컬 테스트용)
- `credentialsSecretName`이 비어 있으면 인증 없이 발송합니다.
- `credentialsSecretName`를 지정하면 `username`/`password` 키를 가진 K8s Secret에서 인증 정보를 읽어 사용합니다.

### 인증용 Secret 만들기

```bash
kubectl -n llm-gateway create secret generic smtp-creds \
  --from-literal=username="<From SMTP Server>" \
  --from-literal=password="<From SMTP Server>"
```

worker 내부에서는 `SMTP_STARTTLS`, `SMTP_USERNAME`, `SMTP_PASSWORD` 환경변수로 매핑됩니다.

> 참고: `EMAIL_SENDER_NAME`은 현재 `set-notification-provider.sh`의 smtp 프롬프트에서 묻지 않으므로 `config.py` 기본값이 사용됩니다. 필요하면 `notificationWorker.email.smtp.fromName`을 values에 추가합니다.

---

## 수동으로 values 고치기

`set-notification-provider.sh`를 쓰지 않고 `deployment/charts/llm-gateway/values-eks-fargate-<env>.yaml`를 직접 고치는 경우:

```yaml
notificationWorker:
  ...
  email:
    provider: "smtp"
    smtp:
      host: "smtp.example.com"
      port: 587
      startTls: true
      fromAddress: "no-reply@llm-gateway.local"
      credentialsSecretName: ""  # 필요시 K8s Secret 이름
```
