# 8-W-b. SES 발송 설정

`notification-worker`에서 AWS SES를 통해 이메일을 발송할 때의 설정입니다.

---

## 제공자 전환 (권장)

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/set-notification-provider.sh <env> ses
```

프롬프트:
- AWS SES region (기본 us-east-1)
- From address
- From name

---

## SES 전용 준비: IRSA

`ses`를 쓰려면 `notification-worker` Pod에 `ses:SendEmail`/`ses:SendRawEmail` 권한이 있어야 합니다. Fargate 노드 역할에 붙여도 되지만, 최소 권한을 원하면 **IRSA**로 `notificationWorker.serviceAccount`에 역할 ARN을 어노테이션합니다.

### 자동 설정 (권장)

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 08-setup-notification-ses-irsa.sh          # dry-run
bash 08-setup-notification-ses-irsa.sh --apply  # IAM + values 반영
```

`--apply` 후 차트를 다시 적용해야 ServiceAccount 어노테이션이 Pod에 전달됩니다:

```bash
bash deployment/scripts/install-eks.sh dev
```

### 수동 설정 (참고)

`08-setup-notification-ses-irsa.sh`를 쓰지 않고 직접 만드는 경우:

**1. 신뢰 정책(Trust policy)** — EKS OIDC provider가 `llm-gateway/notification-worker` SA만 위임합니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/oidc.eks.<AWS_REGION>.amazonaws.com/id/<OIDC_PROVIDER_ID>"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.<AWS_REGION>.amazonaws.com/id/<OIDC_PROVIDER_ID>:sub": "system:serviceaccount:llm-gateway:notification-worker",
          "oidc.eks.<AWS_REGION>.amazonaws.com/id/<OIDC_PROVIDER_ID>:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
```

> `OIDC_PROVIDER_ID`는 `aws eks describe-cluster --name llm-gateway-dev --query 'cluster.identity.oidc.issuer'`에서 `/id/` 뒤 값입니다.

**2. 권한 정책** — SES Verified Identity로 제한하는 것이 안전합니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail",
        "ses:SendRawEmail"
      ],
      "Resource": "arn:aws:ses:<AWS_REGION>:<ACCOUNT_ID>:identity/*"
    }
  ]
}
```

> `Resource`를 `...:identity/<도메인>`으로 좁히는 것을 권장합니다.

**3. `values-eks-fargate-<env>.yaml`에 역할 ARN 추가:**

```yaml
notificationWorker:
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: "arn:aws:iam::<ACCOUNT_ID>:role/<ROLE_NAME>"
```

### SES 사전 설정

AWS SES 콘솔에서 아래를 먼저 마칩니다.

- `fromAddress` 도메인/주소를 **Verified Identity**로 등록
- Sandbox 해제 또는 수신자 도메인/주소를 SES Identity/Configuration Set으로 허용
- `region`은 Verified Identity가 있는 리전으로 설정(예: `us-east-1`)

---

## 수동으로 values 고치기

`set-notification-provider.sh`를 쓰지 않고 `deployment/charts/llm-gateway/values-eks-fargate-<env>.yaml`를 직접 고치는 경우:

```yaml
notificationWorker:
  ...
  email:
    provider: "ses"
    ses:
      region: "us-east-1"
      fromAddress: "no-reply@llm-gateway.local"
      fromName: "LLM Gateway"
```
