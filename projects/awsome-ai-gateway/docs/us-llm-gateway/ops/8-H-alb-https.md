# 8-H. ALB HTTPS — 커스텀 도메인 + ACM 인증서 (방식 A → B)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-H** · 업데이트 ID **US-06** · 등급 **선택**(도메인이 있거나 확보할 수 있을 때) · 소요 준비 1시간(도메인 등록 대기 포함) + 전환 30분

> **한 줄**: 지금은 ALB 가 준 임시 주소로 http 접속(방식 A). 도메인 + ACM 인증서를 붙여 **`https://gateway-<env>.<도메인>`** 으로 바꾼다(방식 B). Cowork 용 CloudFront(US-02 `03`)는 필요 없어져 함께 정리한다.
> 도메인·DNS·인증서가 처음이면 → [부록 A 그림](#부록-a-그림으로-보는-도메인--dns--인증서--alb) 먼저.

## 왜 하는가 · 무엇이 바뀌나

- 방식 A(지금): ALB 3개 모두 **http:80**, ALB 가 준 임시 주소로 접근, 보호막 = `inbound-cidrs` IP 허용목록뿐. Cowork 용 https 는 CloudFront(US-02 `03`)로 gateway 만 우회.
- 방식 B(전환 후): ALB 3개 전부 **ACM 인증서로 https:443 종료**, 고정 도메인(ALB 가 재생성돼도 URL 불변), IP 허용목록에 더해 전송 암호화. `install-eks.sh`·차트는 이미 방식 B 를 지원 — **코드 변경 없이 values 만** 바꾼다.

```
 0. 값 준비    ── source https-env.sh <도메인>   (도메인 하나만 입력)
 1. 준비       ── 도메인 등록(또는 NS 위임) → ACM(ALB 리전) 발급·DNS 검증
 2. 저장소     ── README §3 ① 로 배포 EC2 최신화
 3. 전환       ── 10-switch-https.sh → install-eks.sh → 11-route53-cname.sh → https 확인
 4. 클라이언트   ── 07-client-values.sh 로 새 URL 배포 (Cowork 는 3 직후 끊기므로 바로)
 5. 폐기       ── CloudFront disable→delete · gateway SG 슬롯 회수
 6. 검증·마무리  ── curl · smoke-test · status.sh US-06 · 종단
 R. 롤백       ── 10-switch-https.sh --revert → install-eks.sh · CNAME 삭제 · URL 복귀
```

| # | 결정 | 권장 | 이유 |
|---|---|---|---|
| 1 | 도메인 출처 | (a) 이 계정 Route 53 Domains 신규 등록 (b) 기존/타 계정 도메인 → **NS 위임**(1-2-보충) | 어느 쪽이든 이후 절차 동일 |
| 2 | CloudFront(US-02 `03`) | **5단계에서 폐기** | 방식 B 가 살면 존재 이유가 없다. 유지하면 SG 슬롯 55·이중 경로가 남음. Cowork 는 ALB 직행이 되므로 **PC IP 를 gateway `inbound-cidrs` 에도** 등록 |

## 0. 값 준비 — 도메인 하나만 입력하면 나머지는 시스템에서 읽는다

▶ **실행** · 배포 EC2 — 예시 `mygw.click` 자리에 **본인 도메인** (새 셸을 열 때마다 다시)

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
source https-env.sh mygw.click
```

`https-env.sh` 가 export 하는 것(아래 명령은 전부 이 변수만 쓴다):

| 변수 | 뜻 | 출처 |
|---|---|---|
| `DOMAIN` | 도메인(apex) | **입력** (또는 config.env `HTTPS_DOMAIN`) |
| `ENV` · `NS` · `REL` · `REGION` · `ACCOUNT_ID` | 배포 환경 · 네임스페이스 · helm release · 리전 · 계정 | `config.env` |
| `ZONE_ID` | Route 53 hosted zone | 계정에서 조회 — **1-2 후 생김** |
| `CERT_ARN` | ACM 인증서(`*.$DOMAIN`, ISSUED 여부 함께 표시) | 계정에서 조회 — **1-3 후 생김** |
| `GW_DNS` · `GW_SG` · `GW_HOST` | gateway ALB DNS · SG(inbound 규칙 수) · Ingress host | 클러스터에서 조회 |
| `*_HOST_TARGET` | `gateway-$ENV.$DOMAIN` 등 이름 3개 | 계산 |

> `(none yet)` 은 아직 그 단계 전이라는 뜻이다. 1단계를 끝낸 뒤 `source https-env.sh` 를 다시 하면 채워진다.

## 1. 준비 — 도메인 등록 → ACM 인증서

> **한 줄**: 이름(도메인)과 자물쇠(인증서)를 만든다. ALB 는 건드리지 않으므로 서비스 무영향.

### 1-1. 가용성 확인 (Route 53 Domains API 는 us-east-1 고정)

▶ **실행** · 배포 EC2

```bash
aws route53domains check-domain-availability --region us-east-1 --domain-name "$DOMAIN" --query Availability --output text
```

### 1-2. 등록

**콘솔** Route 53 → Registered domains → Register domain. 연락처 이메일은 실제 수신 가능(ICANN 확인 메일) · 개인정보 보호 ON. 소요 수 분~1시간 · 비용 `.click` ~$3/yr + zone $0.50/mo · 환불 불가.

**확인** — hosted zone 이 생겼는지:

▶ **실행** · 배포 EC2

```bash
source https-env.sh        # ZONE_ID 가 채워지면 OK
```

### 1-2-보충. NS 위임 — 다른 계정/레지스트라에서 등록했을 때

(Amazon 내부 계정처럼 Route 53 Domains 등록이 정책상 막힌 경우도 이 경로다.)

> **한 줄**: 등록은 어디서 했든 **DNS 는 이 계정 zone 이 답하게** NS 를 넘기면 이후 절차는 그대로다.

```
등록한 곳 (개인 계정·회사 DNS·레지스트라)   ── NS 4개 교체 ──▶   이 계정 Hosted Zone $DOMAIN
                                                                 (ACM 검증 CNAME · ALB CNAME 3개는 여기)
```

▶ **실행** · 배포 EC2 — 이 계정에 zone 생성 + NS 4개

```bash
aws route53 create-hosted-zone --name "$DOMAIN" --caller-reference "$DOMAIN-$(date +%s)" --query '{ZoneId:HostedZone.Id,NS:DelegationSet.NameServers}' --output json
```

등록한 쪽에서 Name servers 를 위 4개로 교체(Route 53: Registered domains → 도메인 → Name servers → Edit). 그쪽에 자동 생성된 zone 은 지워도 된다. **확인**: `dig +short NS "$DOMAIN"` 이 위 4개를 돌려주면 1-3 으로.

### 1-3. ACM 와일드카드 인증서

리전은 ALB 와 같은 `$REGION`

▶ **실행** · 배포 EC2

```bash
aws acm request-certificate --region "$REGION" --domain-name "*.$DOMAIN" --subject-alternative-names "$DOMAIN" --validation-method DNS --query CertificateArn --output text
source https-env.sh        # CERT_ARN (PENDING_VALIDATION) 확인
```

### 1-4. DNS 검증

콘솔 ACM → 인증서 → **Create records in Route 53** 버튼 1개. CLI 는:

▶ **실행** · 배포 EC2

```bash
sleep 20; RR=$(aws acm describe-certificate --region "$REGION" --certificate-arn "$CERT_ARN" --query 'Certificate.DomainValidationOptions[0].ResourceRecord')
NAME=$(jq -r .Name <<<"$RR"); VALUE=$(jq -r .Value <<<"$RR")
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"$NAME\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"$VALUE\"}]}}]}"
```

**확인** — `ISSUED` 까지 5~30분:

```bash
aws acm describe-certificate --region "$REGION" --certificate-arn "$CERT_ARN" --query Certificate.Status --output text
```

> ⚠️ 인증서를 **us-east-1 에 만들면 ALB 가 못 쓴다**(CloudFront 용 리전) — `10-switch-https.sh` 가 리전 불일치를 거부한다.
> ⚠️ 검증 CNAME 은 지우지 말 것 — 자동 갱신(13개월)에 쓴다.
> 🧯 `PENDING_VALIDATION` 1시간 초과: `dig +short "$NAME"` 으로 레코드가 보이는지, 위임(1-2-보충)했다면 NS 가 바뀌었는지.

## 2. 저장소 최신화

`10-switch-https.sh`·`11-route53-cname.sh`·`https-env.sh` 와 도메인을 인지하는 `07-client-values.sh`·`status.sh` 는 fork 에 있다 → [README §3 2-1](../README.md#3-적용하기) 절차로 배포 EC2 저장소를 최신으로(values 백업 포함).

## 3. 전환 — values 방식 B → install-eks.sh → CNAME 3개

> **한 줄**: ALB 3개를 https:443 + 인증서로 바꾸고 이름 3개를 연결한다. **이 순간부터 `http://<ALB DNS>` 와 CloudFront 경로는 끊긴다** — 클라이언트 URL 은 4단계에서 바꾼다.

### 3-1. 사전 점검

▶ **실행** · 배포 EC2

```bash
source https-env.sh        # CERT_ARN (ISSUED) · GW_SG (inbound rules: N) 확인
kubectl get events -n "$NS" --sort-by=.lastTimestamp | tail -3
```

> `GW_SG` 의 inbound 규칙 수 + 1 이 계정 SG 규칙 한도(기본 60, CloudFront prefix-list 는 55 로 센다) 이하인지. **IP 추가(05)와 같은 시점에 하지 않는다** — 교체와 추가가 겹치면 순간 초과.

### 3-2. values 방식 B 로 편집

dry-run 으로 diff·렌더 확인 후 `--apply`

▶ **실행** · 배포 EC2

```bash
bash 10-switch-https.sh --drop-cloudfront
bash 10-switch-https.sh --drop-cloudfront --apply
```

> diff 에 있어야 할 것: `listen-ports` 443 · `certificate-arn` · `ssl-policy` · host 3개 · `tls.enabled: true` · gateway `security-group-prefix-lists` 삭제. **`inbound-cidrs` 는 안 바뀌어야** 한다. 렌더 체크에 `NEXTAUTH_URL https://admin-…` 가 보이면 정상.

### 3-3. helm 반영

tmux 안에서, 다른 창으로 이벤트 감시

▶ **실행** · 배포 EC2 — tmux

```bash
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh "$ENV"
```

▶ **실행** · 배포 EC2 — 다른 셸

```bash
kubectl get events -n "$NS" --sort-by=.lastTimestamp | grep -iE 'ingress|RulesPer|error|fail' | tail -5
```

**확인** — 리스너·인증서·SG·NEXTAUTH

▶ **실행** · 배포 EC2

```bash
source https-env.sh        # GW_HOST 가 채워졌는지
kubectl get ingress -n "$NS"
for a in $(aws elbv2 describe-load-balancers --query "LoadBalancers[?contains(LoadBalancerName,'k8s-')].LoadBalancerArn" --output text); do aws elbv2 describe-listeners --load-balancer-arn "$a" --query 'Listeners[].[Port,Certificates[0].CertificateArn]' --output text; done
aws ec2 describe-security-group-rules --filters Name=group-id,Values="$GW_SG" --query 'SecurityGroupRules[?IsEgress==`false`].[FromPort,CidrIpv4,PrefixListId]' --output text
kubectl get deploy "$REL-admin-ui" -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="NEXTAUTH_URL")].value}'; echo
bash 06-persist-annotations.sh | tail -3
```

> 기대: HOSTS 열에 이름 3개(ADDRESS 그대로 — 리스너 교체는 in-place) · 리스너 **443 + cert 만**(80 없음) · gateway SG 규칙 전부 443, prefix-list 없음 · `NEXTAUTH_URL=https://admin-$ENV.$DOMAIN` · 06 은 "already matches".
> ⚠️ `install-eks.sh` 로그에 `NEXTAUTH_URL 이 chart 에서 결정됨 … 스킵` 이 찍혀야 정상. admin-ui 는 env 변경으로 롤링 1회.
> 🧯 `RulesPerSecurityGroupLimitExceeded` → 컨트롤러 재시도 루프. 동시 IP 추가가 원인이거나 한도 초과. §R 로 되돌리거나 한도 상향 후 재시도.

### 3-4. CNAME 3개

▶ **실행** · 배포 EC2

```bash
bash 11-route53-cname.sh
bash 11-route53-cname.sh --apply
```

### 3-5. 첫 https 확인

배포 EC2 IP 는 허용목록에 있으므로 여기서

▶ **실행** · 배포 EC2

```bash
for h in "$GW_HOST_TARGET" "$API_HOST_TARGET"; do curl -sI "https://$h/health" | head -1; done
curl -sI "https://$UI_HOST_TARGET/api/health" | head -1
echo | openssl s_client -connect "$GW_HOST_TARGET:443" -servername "$GW_HOST_TARGET" 2>/dev/null | openssl x509 -noout -subject -issuer
```

> 기대: `HTTP/2 200` ×3 · subject `CN=*.<도메인>` · issuer Amazon. `http://<ALB DNS>` 는 연결 거부(정상). CloudFront URL 은 502(5단계에서 폐기).

## 4. 클라이언트 · 5. CloudFront 폐기 · 6. 검증 · R. 롤백

*(실측 후 작성 — 이 배포에서 3단계까지 검증되면 채운다)*

## 함정 (미리 아는 것)

- **리스너 80→443 전환 시 SG 규칙 초과 위험** — ALB controller v2.8.x 는 SG 를 **Revoke→Authorize** 순서로 맞추므로 순수 "교체" 는 한도를 넘지 않는다. 넘는 경우는 **같은 시점에 IP 추가(05)를 섞을 때** — 하지 말 것. 감시: `kubectl get events -n $NS --sort-by=.lastTimestamp` 에 `RulesPerSecurityGroupLimitExceeded`.
- **CloudFront origin 은 http-only** — 3단계 직후 CloudFront 경로는 502(정상). 4단계에서 URL 을 바꾸고 5단계에서 폐기한다.
- **values 는 배포 EC2 유일본** — `10-switch-https.sh` 는 스냅샷 후 텍스트 편집만 하고 `inbound-cidrs`·per-Ingress 겹은 손대지 않는다. 손편집 금지.
- **ACM 은 ALB 와 같은 리전** — us-east-1 인증서는 CloudFront 용. `10-switch-https.sh` 가 리전 불일치를 거부한다.
- **Cognito callback(`localhost:8090-8092`)·admin-ui→admin-api(클러스터 내부 URL)는 무관** — 건드리지 않는다.
- **`spec.tls[].secretName: ""`** — ALB 는 인증서를 어노테이션에서 받으므로 secret 이 비어 있는 것이 정상.

## 부록 A. 그림으로 보는 도메인 · DNS · 인증서 · ALB

현재(방식 A): ALB 가 자동 부여한 주소(`k8s-….elb.amazonaws.com`)로 http:80 직접 접속, 인증서 없음.
전환(방식 B): 도메인 + DNS 레코드 + ACM 인증서를 더해 https:443 으로 접속. 아래 순서로 만든다.

```
■ 준비 — 만드는 것 3개와 그 관계 (예시 도메인 mygw.click)

┌──────────────────┐     ┌──────────────────────────────────────────────────────────────┐     ┌─────────────────┐
│ Route 53 Domains │     │ Route 53 Hosted Zone  mygw.click  (등록 시 자동 생성)        │     │ ACM (us-west-2) │
│                  │     ├──────────────────────────────────────────────────────────────┤     │                 │
│ 도메인 등록      │ ──▶ │ NS / SOA                                          (자동)     │ ◀── │ 인증서 요청     │
│ mygw.click       │     │ _abc123.mygw.click        CNAME  _xyz.acm-validations.aws    │     │ *.mygw.click    │
│ (연 ~$3, 1회)    │     │ gateway-dev.mygw.click    CNAME  k8s-…gw….elb.amazonaws.com  │     │ + mygw.click    │
└──────────────────┘     │ admin-dev.mygw.click      CNAME  k8s-…ui….elb.amazonaws.com  │     │ → ISSUED → ARN  │
                         │ admin-api-dev.mygw.click  CNAME  k8s-…api….elb.amazonaws.com │     └─────────────────┘
                         └──────────────────────────────────────────────────────────────┘

  ──▶ 등록: 도메인을 사면 hosted zone 이 자동으로 생긴다. 그 안에 CNAME 3개(이름 → ALB 주소)는 우리가 넣는다.
  ◀── 검증: ACM 이 준 CNAME 한 줄(_abc123…)을 hosted zone 에 넣어 소유를 증명한다.
           Route 53 이면 콘솔 버튼 1개(Create records in Route 53) → 5~30분 뒤 ISSUED.

■ 적용 — values(방식 B) 를 helm 으로 반영하면 ALB 3개가 이렇게 바뀐다

┌───────────────────────────────────────────────────┐         ┌──────────────────────────────────────────┐
│ values-eks-fargate-dev.yaml                       │         │ ALB ×3  (gateway / admin-ui / admin-api) │
├───────────────────────────────────────────────────┤         ├──────────────────────────────────────────┤
│ listen-ports: [{"HTTPS":443}]                     │ ─helm─▶ │ 리스너 :443  ← ACM 인증서 부착           │
│ certificate-arn: arn:aws:acm:…   ← ACM ARN        │         │ 리스너 :80   (사라짐 — http 접속 거부)   │
│ host: gateway-dev.mygw.click     (3 ingress 각각) │         │ 규칙: Host = gateway-dev.mygw.click      │
│ inbound-cidrs: (그대로)                           │         │ SG:  inbound-cidrs 그대로, 포트만 80→443 │
└───────────────────────────────────────────────────┘         └──────────────────────────────────────────┘

■ 접속 — 사용자 PC 에서 게이트웨이까지

PC (Claude Code / Cowork)
 │ (1) DNS 질의: gateway-dev.mygw.click ?
 ▼
Route 53 Hosted Zone
 │ (2) 응답: CNAME → k8s-…gw….us-west-2.elb.amazonaws.com → ALB 의 IP
 ▼
PC
 │ (3) https://gateway-dev.mygw.click  (TLS: 서버 인증서 *.mygw.click 검증 → 암호화)
 ▼
ALB :443
 │ (4) SG inbound-cidrs 로 출발지 IP 확인 → (5) Host 헤더로 규칙 매칭 → (6) TLS 종료
 ▼
gateway-proxy 파드  (VPC 내부, http)
 │ (7) VK 인증 → 예산·레이트리밋 → Bedrock
 ▼
Amazon Bedrock

■ 이름 3개와 쓰임

gateway-dev.<DOMAIN>     Claude Code / Cowork 의 ANTHROPIC_BASE_URL (데이터 플레인)
admin-dev.<DOMAIN>       관리자 웹 (NEXTAUTH_URL 은 차트가 https 로 자동 파생)
admin-api-dev.<DOMAIN>   VK 발급 API — api-key-helper 의 ADMIN_API_URL
```

| 용어 | 뜻 |
|---|---|
| 도메인 | 내가 소유한 이름. 연 단위 요금(`.click` ~$3 · `.com` ~$14). Route 53 Domains 에서 등록 |
| hosted zone | 그 도메인의 DNS 레코드를 두는 곳. Route 53 에서 등록하면 자동 생성(월 $0.50) |
| CNAME | "이 이름 → 저 주소" 레코드. ALB 주소가 바뀌어도 이름은 그대로 |
| ACM 인증서 | AWS 무료 TLS 인증서. 와일드카드 `*.mygw.click` 하나로 세 이름 커버. **ALB 와 같은 리전(us-west-2)** 필수 |
| DNS 검증 | ACM 이 준 CNAME 한 줄을 hosted zone 에 넣어 소유를 증명. Route 53 이면 콘솔 버튼 1개, 5~30분 |
| ALB 리스너 | ALB 가 받는 포트. 방식 B 는 443 만 두고 80 은 없앤다(http 접속은 거부) |
