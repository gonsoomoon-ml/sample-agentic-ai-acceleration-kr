# 8-W-c. 내부 메일 API 발송 설정

`notification-worker`에서 사내 메일 API를 통해 이메일을 발송할 때의 설정입니다.

---

## 제공자 전환 (권장)

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/set-notification-provider.sh <env> internal_api
```

프롬프트:
- Internal API URL
- From address
- From name

---

## 요구사항

사내 메일 API는 HTTP POST로 발송하며, worker가 직접 연동할 수 있는 엔드포인트가 필요합니다.

- API endpoint URL
- 인증 방식 (API key, OAuth, mTLS 등)
- 요청 payload 형식
- 발신 주소/이름
- 속도 제한 및 타임아웃

내부 API 연동이 필요하면 `internal_api_sender.py`를 기준으로 사내 API 스펙에 맞게 요청을 수정해야 합니다.

---

## 수동으로 values 고치기

`set-notification-provider.sh`를 쓰지 않고 `deployment/charts/llm-gateway/values-eks-fargate-<env>.yaml`를 직접 고치는 경우:

```yaml
notificationWorker:
  ...
  email:
    provider: "internal_api"
    internalApi:
      url: "http://mail-api.internal/send"
      fromAddress: "no-reply@llm-gateway.local"
      fromName: "LLM Gateway"
```
