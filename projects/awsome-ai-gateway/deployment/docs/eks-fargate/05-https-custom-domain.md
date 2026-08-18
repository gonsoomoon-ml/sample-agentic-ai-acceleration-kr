# 05. HTTPS Custom Domain — ALB + ACM + Route53

**목적**: EKS Fargate에 배포된 LLM Gateway 서비스(gateway-proxy, admin-ui, admin-api)에 실제 도메인과 ACM 인증서를 연결해 HTTPS로 노출합니다.

**소요**: 15~20분 (DNS 전파 대기 포함)

**전제**: [04. Helm Install](./04-helm-install.md)이 완료되어 Ingress/ALB가 이미 생성되어 있어야 합니다.

---

## 1. 도메인 및 Route53 Hosted Zone 준비

- 기존에 보유한 도메인 하위 서브도메인을 사용하거나, 도메인을 구매합니다.
- 이 도메인의 DNS 네임서버를 Route53 Hosted Zone에서 관리해야 합니다.

Route53 Hosted Zone 생성:

```bash
aws route53 create-hosted-zone \
  --name llm-gateway.mycompany.com \
  --caller-reference "$(date +%s)"
```

생성된 `HostedZoneId`를 기록합니다.

```bash
aws route53 list-hosted-zones-by-name \
  --dns-name llm-gateway.mycompany.com
```

---

## 2. ACM 인증서 발급

ALB와 인증서는 반드시 **동일 리전**(`ap-northeast-2`)에 있어야 합니다.

```bash
CERT_ARN=$(aws acm request-certificate \
  --region ap-northeast-2 \
  --domain-name "*.llm-gateway.mycompany.com" \
  --validation-method DNS \
  --subject-alternative-names "llm-gateway.mycompany.com" \
  --query CertificateArn --output text)

echo "Certificate ARN: $CERT_ARN"
```

---

## 3. DNS 검증

### 3.1 ACM 콘솔에서 자동 검증 (권장)

ACM 콘솔 → 방금 요청한 인증서 선택 → **"Create records in Route 53"** 버튼 클릭

Route53에 CNAME 검증 레코드가 자동 추가됩니다.

### 3.2 수동 검증

검증용 CNAME 이름/값 확인:

```bash
aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" \
  --region ap-northeast-2 \
  --query 'Certificate.DomainValidationOptions[].ResourceRecord'
```

Route53에 해당 CNAME 레코드를 수동 추가합니다.

### 3.3 발급 완료 확인

```bash
aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" \
  --region ap-northeast-2 \
  --query 'Certificate.Status'
```

`ISSUED` 상태가 될 때까지 대기합니다 (수 초~수 분).

---

## 4. Helm values 수정

파일: `deployment/charts/llm-gateway/values-eks-fargate-dev.yaml`

### 4.1 방식 A(HTTP) 주석 처리

```yaml
# === 방식 A (활성): 도메인 없음, HTTP 전용 ===
# ingress:
#   enabled: true
#   className: "alb"
#   annotations:
#     ...
#   gateway:
#     host: ""
#     tls:
#       enabled: false
#   ...
```

### 4.2 방식 B(HTTPS) 주석 해제 및 값 입력

```yaml
ingress:
  enabled: true
  className: "alb"
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/ssl-policy: ELBSecurityPolicy-TLS13-1-2-2021-06
    alb.ingress.kubernetes.io/ssl-redirect: "443"
    alb.ingress.kubernetes.io/healthcheck-path: /health
    alb.ingress.kubernetes.io/healthcheck-protocol: HTTP
    alb.ingress.kubernetes.io/backend-protocol: HTTP
    alb.ingress.kubernetes.io/load-balancer-attributes: idle_timeout.timeout_seconds=300
    alb.ingress.kubernetes.io/certificate-arn: "arn:aws:acm:ap-northeast-2:<ACCOUNT_ID>:certificate/<CERT_ID>"
  gateway:
    host: "gateway-dev.llm-gateway.mycompany.com"
    tls:
      enabled: true
  adminUi:
    host: "admin-dev.llm-gateway.mycompany.com"
    tls:
      enabled: true
  adminApi:
    host: "admin-api-dev.llm-gateway.mycompany.com"
    tls:
      enabled: true
```

- `certificate-arn`은 [3단계](#3-dns-검증)에서 발급받은 ACM ARN
- `host` 3개는 실제 사용할 서브도메인

---

## 5. Helm 배포

`install-eks.sh`를 사용하면 Terraform outputs과 Helm upgrade가 함께 처리됩니다.

```bash
cd /path/to/LLM-Gateway-Vanilla
./deployment/scripts/install-eks.sh dev
```

⚠️ **`values-eks-fargate-dev.yaml`을 직접 `-f`로 주는 `helm upgrade`는 사용하지 마세요.**
`install-eks.sh`는 Terraform output(DB/Redis host, IRSA role ARN, OIDC 값 등)을
`--set`으로 주입하는데, values 파일에는 이 값들이 `<RDS_PROXY_ENDPOINT>` 같은
placeholder로만 남아 있습니다. `-f`만 쓰면 이 placeholder가 그대로 적용되어
스택이 깨집니다(관련 경고: [07. 업그레이드/롤백](./07-upgrade-rollback.md)).
반드시 위처럼 `install-eks.sh`를 쓰거나, 부득이하게 직접 `helm upgrade`를 써야
한다면 기존 릴리스 값을 보존하도록 `--reuse-values`를 명시하세요.

배포 후 ALB DNS를 확인합니다.

```bash
kubectl get ingress -n llm-gateway
```

---

## 6. Route53 CNAME 연결

ALB DNS를 실제 도메인에 연결합니다.

```bash
HOSTED_ZONE_ID="Z123..."  # 1단계에서 확인한 Hosted Zone ID

GATEWAY_ALB=$(kubectl get ingress llm-gateway-gateway -n llm-gateway \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
ADMIN_UI_ALB=$(kubectl get ingress llm-gateway-admin-ui -n llm-gateway \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
ADMIN_API_ALB=$(kubectl get ingress llm-gateway-admin-api -n llm-gateway \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

for host in gateway-dev admin-dev admin-api-dev; do
  case $host in
    gateway-dev)  ALB_DNS=$GATEWAY_ALB ;;
    admin-dev)    ALB_DNS=$ADMIN_UI_ALB ;;
    admin-api-dev) ALB_DNS=$ADMIN_API_ALB ;;
  esac

  aws route53 change-resource-record-sets \
    --hosted-zone-id "$HOSTED_ZONE_ID" \
    --change-batch "{
      \"Changes\": [{
        \"Action\": \"UPSERT\",
        \"ResourceRecordSet\": {
          \"Name\": \"$host.llm-gateway.mycompany.com\",
          \"Type\": \"CNAME\",
          \"TTL\": 300,
          \"ResourceRecords\": [{\"Value\": \"$ALB_DNS\"}]
        }
      }]
    }"
done
```

> **참고**: Ingress마다 별도 ALB가 생성되므로, 각 서브도메인을 해당 ALB DNS로 연결해야 합니다.

---

## 7. 확인

DNS 전파(1~5분) 후 HTTPS health check를 실행합니다.

```bash
curl -i https://gateway-dev.llm-gateway.mycompany.com/health
curl -i https://admin-dev.llm-gateway.mycompany.com/api/health
curl -i https://admin-api-dev.llm-gateway.mycompany.com/health
```

모두 `HTTP/1.1 200 OK` 응답이 나오면 완료입니다.

---

## 체크리스트

- [ ] Route53 Hosted Zone 준비
- [ ] ACM 인증서 `ISSUED` 상태 확인
- [ ] `values-eks-fargate-dev.yaml`에서 방식 A 주석 / 방식 B 해제
- [ ] `certificate-arn`을 실제 ACM ARN으로 교체
- [ ] `host` 3개를 실제 도메인으로 교체
- [ ] `install-eks.sh dev` 성공 (직접 `helm upgrade` 시 `--reuse-values` 필수)
- [ ] Route53에 CNAME 3개 추가
- [ ] HTTPS health check 200 확인

---

## 문제 해결

### ACM 인증서가 `PENDING_VALIDATION`

Route53 검증 레코드가 누락되었는지 확인합니다.

```bash
aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" \
  --region ap-northeast-2 \
  --query 'Certificate.DomainValidationOptions[].ResourceRecord'
```

수동으로 Route53에 CNAME 레코드를 추가합니다.

### ALB가 생성되지 않음

```bash
kubectl get ingress -n llm-gateway
kubectl logs -n kube-system deployment/aws-load-balancer-controller
```

`ADDRESS`가 비어 있으면 AWS Load Balancer Controller 로그에서 원인을 확인합니다.

### 503 Service Unavailable

Target Group health check 실패 시 Pod 상태를 확인합니다.

```bash
kubectl get pods -n llm-gateway
kubectl describe pod <pod-name> -n llm-gateway
```

---

[👈 04-helm-install.md](./04-helm-install.md) | [다음: 06-smoke-test.md 👉](./06-smoke-test.md)
