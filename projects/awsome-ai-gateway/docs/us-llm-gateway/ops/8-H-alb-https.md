# 8-H. ALB HTTPS — 커스텀 도메인 + ACM 인증서 (방식 A → B)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-H** · 업데이트 ID **US-06** · 등급 **선택**(POC) — **운영(prod)이면 강력 권장** · 도메인이 있거나 확보할 수 있을 때 · 소요: 준비 1시간(도메인 등록 대기 포함) + 전환 30분

> **한 줄**: 지금은 ALB 가 준 임시 주소로 http 접속(방식 A). 도메인 + ACM 인증서를 붙여 `https://gateway-{{env}}.{{도메인}}` 으로 바꾼다(방식 B). Cowork 용 CloudFront(US-02 `03`)는 필요 없어져 함께 정리한다(4단계).
> 도메인·DNS·인증서가 처음이면 → [부록 A 그림](#부록-a-그림으로-보는-도메인--dns--인증서--alb) 먼저.

## 왜 하는가 · 무엇이 바뀌나

- 방식 A(지금): ALB 3개 모두 **http:80**, ALB 가 준 임시 주소로 접근, 보호막 = `inbound-cidrs` IP 허용목록뿐. Cowork 용 https 는 CloudFront(US-02 `03`)로 gateway 만 우회.
- 방식 B(전환 후): ALB 3개 전부 **ACM 인증서로 https:443 종료**, 고정 도메인(ALB 가 재생성돼도 URL 불변), IP 허용목록에 더해 전송 암호화. `install-eks.sh`·차트는 이미 방식 B 를 지원 — **코드 변경 없이 values 만** 바꾼다.

```
 0. 준비       ── 도메인 등록(또는 NS 위임) → ACM(ALB 리전) 발급·DNS 검증   ← 저장소와 무관, 먼저
 1. 시작 전    ── 1-1 저장소 최신화(새 스크립트 받기) · 1-2 값 준비 (source https-env.sh {{DOMAIN}})
 2. 전환       ── 10-switch-https.sh → install-eks.sh → 11-route53-cname.sh → https 확인
 3. 클라이언트   ── 07-client-values.sh 로 새 URL 배포 (Cowork 는 2 직후 끊기므로 바로)
 4. 폐기       ── CloudFront disable→delete · gateway SG 슬롯 회수
 5. 검증·마무리  ── curl · smoke-test · status.sh US-06 · 종단
```


| #   | 결정                     | 권장                                                                  | 이유                                                                                                               |
| --- | ---------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 1   | 도메인 출처                 | (a) 이 계정 Route 53 Domains 신규 등록 (b) 기존/타 계정 도메인 → **NS 위임**(0-2-보충) | 어느 쪽이든 이후 절차 동일                                                                                                  |
| 2   | CloudFront(US-02 `03`) | **4단계에서 폐기**                                                        | 방식 B 가 살면 존재 이유가 없다. 유지하면 SG 슬롯 55·이중 경로가 남음. Cowork 는 ALB 직행이 되므로 **PC IP 를 gateway** `inbound-cidrs` **에도** 등록 |




## 0. 준비 — 도메인 등록 → ACM 인증서 (저장소·클러스터와 무관, 먼저 해 둔다)

> **한 줄**: 이름(도메인)과 자물쇠(인증서)를 만든다. ALB 는 건드리지 않으므로 서비스 무영향.



### 0-1. 가용성 확인 (Route 53 Domains API 는 us-east-1 고정)

> **한 줄**: 원하는 이름이 아직 비어 있는지 Route 53 에 묻는다.

▶ **실행** · 배포 EC2  · **⚠ 바꿀 것:** `{{DOMAIN}}` ****`{{ALB 리전}}`

```bash
DOMAIN={{DOMAIN}}      # TLD 포함 전체. 예: DOMAIN=mygw.click  (mygw 만 쓰면 오류)
CERT_REGION={{ALB 리전}}    # 예: CERT_REGION=us-west-2 (terraform.tfvars 의 aws_region)
aws route53domains check-domain-availability --region us-east-1 \
  --domain-name "$DOMAIN" --query Availability --output text
```



### 0-2. 등록

> **한 줄**: 도메인을 사서(연 단위) 이 계정에 hosted zone 이 생기게 한다.

**콘솔** Route 53 → Registered domains → Register domain. 연락처 이메일은 실제 수신 가능(ICANN 확인 메일) · 개인정보 보호 ON. 소요 수 분~1시간 · 비용 `.click` ~$3/yr + zone $0.50/mo · 환불 불가.

**확인** — hosted zone 이 생겼는지:

▶ **실행** · 배포 EC2

```bash
# 같은 셸 (0-1 의 DOMAIN·CERT_REGION, 0-2 의 ZONE_ID, 0-3 의 CERT_ARN)
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" \
  --query "HostedZones[?Name=='$DOMAIN.'].Id | [0]" --output text \
  | sed 's#/hostedzone/##')
echo "ZONE_ID=$ZONE_ID"   # Z… 가 나오면 OK ("None" = 등록 전 또는 0-2-보충)
```



### 0-2-보충. NS 위임 — 다른 계정/레지스트라에서 등록했을 때

> **한 줄**: 등록은 다른 곳에서 했을 때, DNS 권한만 이 계정 zone 으로 넘긴다.

> **한 줄**: 등록은 어디서 했든 **DNS 는 이 계정 zone 이 답하게** NS 를 넘기면 이후 절차는 그대로다.

```
등록한 곳 (개인 계정·회사 DNS·레지스트라)   ── NS 4개 교체 ──▶   이 계정 Hosted Zone $DOMAIN
                                                                 (ACM 검증 CNAME · ALB CNAME 3개는 여기)
```

▶ **실행** · 배포 EC2 — 이 계정에 zone 생성 + NS 4개

```bash
# 같은 셸 (0-1 의 DOMAIN·CERT_REGION, 0-2 의 ZONE_ID, 0-3 의 CERT_ARN)
aws route53 create-hosted-zone --name "$DOMAIN" \
  --caller-reference "$DOMAIN-$(date +%s)" \
  --query '{ZoneId:HostedZone.Id,NS:DelegationSet.NameServers}' --output json
```

등록한 쪽에서 Name servers 를 위 4개로 교체(Route 53: Registered domains → 도메인 → Name servers → Edit). 그쪽에 자동 생성된 zone 은 지워도 된다. **확인**: `dig +short NS "$DOMAIN"` 이 위 4개를 돌려주면 1-3 으로.

### 0-3. ACM 와일드카드 인증서

> **한 줄**: ALB 에 붙일 와일드카드 인증서를 ALB 와 같은 리전에 요청한다.

인증서 리전은 **ALB 와 같은** `$CERT_REGION`(0-1 에서 넣은 값)이어야 ALB 가 붙일 수 있다.

▶ **실행** · 배포 EC2

```bash
# 같은 셸 (0-1 의 DOMAIN·CERT_REGION, 0-2 의 ZONE_ID, 0-3 의 CERT_ARN)
CERT_ARN=$(aws acm request-certificate --region "$CERT_REGION" \
  --domain-name "*.$DOMAIN" --subject-alternative-names "$DOMAIN" \
  --validation-method DNS --query CertificateArn --output text)
echo "CERT_ARN=$CERT_ARN"
```



### 0-4. DNS 검증

> **한 줄**: "도메인 소유 증명" 레코드를 zone 에 넣어 인증서를 ISSUED 로 만든다.

콘솔 ACM → 인증서 → **Create records in Route 53** 버튼 1개. CLI 는:

▶ **실행** · 배포 EC2

```bash
# 같은 셸 (0-1 의 DOMAIN·CERT_REGION, 0-2 의 ZONE_ID, 0-3 의 CERT_ARN)
sleep 20
RR=$(aws acm describe-certificate --region "$CERT_REGION" --certificate-arn "$CERT_ARN" \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord')
NAME=$(jq -r .Name <<<"$RR"); VALUE=$(jq -r .Value <<<"$RR")
RS="{\"Name\":\"$NAME\",\"Type\":\"CNAME\",\"TTL\":300,"
RS="$RS\"ResourceRecords\":[{\"Value\":\"$VALUE\"}]}"
BATCH="{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":$RS}]}"
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --change-batch "$BATCH" --query ChangeInfo.Status --output text
```

**확인** — `ISSUED` 까지 5~30분:

```bash
# 같은 셸 (0-1 의 DOMAIN·CERT_REGION, 0-2 의 ZONE_ID, 0-3 의 CERT_ARN)
aws acm describe-certificate --region "$CERT_REGION" \
  --certificate-arn "$CERT_ARN" --query Certificate.Status --output text
```

> ⚠️ 인증서를 **us-east-1 에 만들면 ALB 가 못 쓴다**(CloudFront 용 리전) — `10-switch-https.sh` 가 리전 불일치를 거부한다.
> ⚠️ 검증 CNAME 은 지우지 말 것 — 자동 갱신(13개월)에 쓴다.
> 🧯 `PENDING_VALIDATION` 1시간 초과: `dig +short "$NAME"` 으로 레코드가 보이는지, 위임(0-2-보충)했다면 NS 가 바뀌었는지.



## 1. 시작 전 — 새 스크립트 받기 · 값 준비



### 1-1. 저장소 최신화 (새 파일 받기)

> **한 줄**: 이 절이 쓰는 스크립트(https-env·10·11·도메인 인지 07/status)를 배포 EC2 로 받는다.

이 절이 쓰는 `https-env.sh`·`10-switch-https.sh`·`11-route53-cname.sh` 와 도메인을 인지하는 `07-client-values.sh`·`status.sh` 는 fork 브랜치 `[us/deploy-fixes](https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr/tree/us/deploy-fixes/projects/awsome-ai-gateway/docs/us-llm-gateway/update-scripts)` 에 있다. 배포 EC2 저장소를 [README §3 ①](../README.md#3-적용하기-배포-ec2-에서) 절차로 최신화한다 — 리베이스 브랜치라 `git pull` 이 아니라 아래다(values 백업 포함).

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak
git fetch origin && git reset --hard origin/us/deploy-fixes
cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
ls docs/us-llm-gateway/update-scripts/{https-env.sh,10-*.sh,11-*.sh}
```

> `values restored OK` 와 새로운 파일 보여야 한다. `RESTORE FAILED` 면 `cp ~/values.bak $V` 를 다시.
> 🧯 `reset --hard` 는 `.terraform.lock.hcl` 도 되돌리므로 다음 `install-eks.sh` 가 `terraform output 실패` 로 멈출 수 있다 → provider 재조정만 하면 된다(apply 아님):
> `cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-{{env}} && terraform init -input=false`



### 1-2. 값 준비 — 도메인 하나만 입력하면 나머지는 시스템에서 읽는다

> **한 줄**: 이후 명령이 쓰는 변수 12개를 config.env·클러스터·AWS 에서 읽어 export 한다.

▶ **실행** · 배포 EC2  · **⚠ 바꿀 것:** `{{DOMAIN}}` (본인 도메인, TLD 포함)

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
source https-env.sh {{DOMAIN}}          # 예: source https-env.sh mygw.click
```

`https-env.sh` 가 export 하는 것(아래 명령은 전부 이 변수만 쓴다):


| 변수                                                         | 뜻                                                 | 출처                                    |
| ---------------------------------------------------------- | ------------------------------------------------- | ------------------------------------- |
| `DOMAIN`                                                   | 도메인(apex)                                         | **입력** (또는 config.env `HTTPS_DOMAIN`) |
| `GW_ENV` · `GW_NS` · `GW_REL` · `GW_REGION` · `ACCOUNT_ID` | 배포 환경 · 네임스페이스 · helm release · 리전 · 계정           | `config.env`                          |
| `ZONE_ID`                                                  | Route 53 hosted zone                              | 계정에서 조회 — **1-2 후 생김**                |
| `CERT_ARN`                                                 | ACM 인증서(`*.$DOMAIN`, ISSUED 여부 함께 표시)             | 계정에서 조회 — **1-3 후 생김**                |
| `GW_DNS` · `GW_SG` · `GW_HOST`                             | gateway ALB DNS · SG(inbound 규칙 수) · Ingress host | 클러스터에서 조회                             |
| `*_HOST_TARGET`                                            | `gateway-$GW_ENV.$DOMAIN` 등 이름 3개                 | 계산                                    |


> `(none yet)` 이 ZONE_ID/CERT_ARN 에 뜨면 0단계가 덜 끝난 것이다. 도메인은 `config.env` 에 `HTTPS_DOMAIN` 으로 저장되므로, 이후 블록(새 셸 포함)은 `source https-env.sh {{DOMAIN}}` 으로 값을 다시 읽고 표로 확인한다(저장돼 있으면 `{{DOMAIN}}` 생략 가능, `-q` 는 표 생략).



## 2. 전환 — values 방식 B → install-eks.sh → CNAME 3개

> **한 줄**: ALB 3개를 https:443 + 인증서로 바꾸고 이름 3개를 연결한다. **이 순간부터** `http://{{ALB DNS}}` **와 CloudFront 경로는 끊긴다** — 클라이언트 URL 은 3단계에서 바꾼다.



### 2-1. 사전 점검

> **한 줄**: gateway SG 에 규칙 여유가 있고(동시 IP 추가 없음) Ingress 에 진행 중인 에러 이벤트가 없는지 본다 — 이 둘이 아니면 전환이 조용히 실패한다.

▶ **실행** · 배포 EC2

```bash
kubectl get events -n "$GW_NS" --sort-by=.lastTimestamp | tail -3
```

> 기대: `No resources found …`(최근 1시간 이벤트 없음) 또는 `Normal SuccessfullyReconciled ingress/…` 몇 줄 — 둘 다 OK.
> 멈춤: `Warning` 에 `FailedDeployModel`·`FailedBuildModel`·`RulesPerSecurityGroupLimitExceeded` 가 반복되면 진행 중인 문제가 있는 것 — 전환 전에 원인부터.
> 1-2 의 `source https-env.sh` 출력에서 `CERT_ARN … (ISSUED)` 와 `GW_SG … (inbound rules: N)` 을 이미 확인했으면 그대로 진행. 새 셸이면 `source https-env.sh {{DOMAIN}}` 먼저.



### 2-2. values 방식 B 로 편집

> **한 줄**: values 파일의 Ingress 블록만 방식 B(443·인증서·host 3개)로 바꾼다 — 파일만 바뀌고 클러스터는 아직 그대로.

dry-run 으로 diff·렌더 확인 후 `--apply`

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 10-switch-https.sh --drop-cloudfront
bash 10-switch-https.sh --drop-cloudfront --apply
```

> diff 에 있어야 할 것: `listen-ports` 443 · `certificate-arn` · `ssl-policy` · host 3개 · `tls.enabled: true` · gateway `security-group-prefix-lists` 삭제. `inbound-cidrs` **는 안 바뀌어야** 한다. 렌더 체크에 `NEXTAUTH_URL https://admin-…` 가 보이면 정상.



### 2-3. helm 반영

> **한 줄**: 바뀐 values 를 helm 으로 적용해 ALB 리스너를 실제로 80→443 으로 교체한다(여기서 접속 경로가 바뀐다).

tmux 안에서, 다른 창으로 이벤트 감시

▶ **실행** · 배포 EC2 — tmux 안에서 (5분 안팎, 끊겨도 계속 돌게)  · **⚠ 바꿀 것:** `{{DOMAIN}}`

```bash
tmux new -s https          # 이미 있으면: tmux attach -t https  (새 셸이므로 변수는 아래서 다시)
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && source https-env.sh {{DOMAIN}}   # 예: mygw.click — 표 확인
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-$GW_ENV && terraform init -input=false | grep -m1 -i "successfully\|error"
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh "$GW_ENV"
```

> `terraform init` 은 1-1 의 `reset --hard` 가 되돌린 lock 파일과 provider 캐시를 다시 맞추는 것(apply 아님, 인프라 무변경). 빼면 `install-eks.sh` 가 `terraform output 실패` 로 멈춘다.

▶ **실행** · 배포 EC2  · **⚠ 바꿀 것:** `{{DOMAIN}}`

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && source https-env.sh {{DOMAIN}}   # 예: mygw.click — 표에서 GW_HOST 가 채워졌는지 확인
kubectl get ingress -n "$GW_NS"
for a in $(aws elbv2 describe-load-balancers \
    --query "LoadBalancers[?contains(LoadBalancerName,'k8s-')].LoadBalancerArn" \
    --output text); do
  aws elbv2 describe-listeners --load-balancer-arn "$a" \
    --query 'Listeners[].[Port,Certificates[0].CertificateArn]' --output text
done
aws ec2 describe-security-group-rules --filters Name=group-id,Values="$GW_SG" \
  --query 'SecurityGroupRules[?IsEgress==`false`].[FromPort,CidrIpv4,PrefixListId]' \
  --output text
kubectl get deploy "$GW_REL-admin-ui" -n "$GW_NS" -o yaml | grep -A1 'name: NEXTAUTH_URL'
bash 06-persist-annotations.sh | tail -3
```

> 기대: HOSTS 열에 이름 3개(ADDRESS 그대로 — 리스너 교체는 in-place) · 리스너 **443 + cert 만**(80 없음) · gateway SG 규칙 전부 443, prefix-list 없음 · `NEXTAUTH_URL=https://admin-$GW_ENV.$DOMAIN` · 06 은 "already matches".
> ⚠️ `install-eks.sh` 로그에 `NEXTAUTH_URL 이 chart 에서 결정됨 … 스킵` 이 찍혀야 정상. admin-ui 는 env 변경으로 롤링 1회.
> 🧯 `RulesPerSecurityGroupLimitExceeded` → 컨트롤러 재시도 루프. 동시 IP 추가가 원인이거나 한도 초과. §R 로 되돌리거나 한도 상향 후 재시도.



### 2-4. CNAME 3개

> **한 줄**: 이름 3개를 각 ALB 주소로 연결한다(DNS CNAME).

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 11-route53-cname.sh
bash 11-route53-cname.sh --apply
```



### 2-5. 첫 https 확인

> **한 줄**: 이름·인증서·IP 허용목록이 함께 동작하는지 종단으로 확인한다.

배포 EC2 IP 는 허용목록에 있으므로 여기서

▶ **실행** · 배포 EC2  · **⚠ 바꿀 것:** `{{DOMAIN}}`

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
source https-env.sh {{DOMAIN}}       # 예: source https-env.sh mygw.click — 표의 값을 눈으로 확인
for h in "$GW_HOST_TARGET" "$API_HOST_TARGET"; do
  printf '%-40s ' "$h"; curl -s -o /dev/null -w '%{http_code}\n' "https://$h/health"
done
printf '%-40s ' "$UI_HOST_TARGET"; curl -s -o /dev/null -w '%{http_code}\n' "https://$UI_HOST_TARGET/api/health"
echo | openssl s_client -connect "$GW_HOST_TARGET:443" \
  -servername "$GW_HOST_TARGET" 2>/dev/null | openssl x509 -noout -subject -issuer
```

> 기대: `200` ×3 · subject `CN=*.{{도메인}}` · issuer Amazon. (`curl -I` 를 쓰면 gateway/admin-api 가 `405` 를 낸다 — HEAD 미지원일 뿐 실패 아님.) `http://{{ALB DNS}}` 는 연결 거부(정상). CloudFront URL 은 502(4단계에서 폐기).



## 3. 클라이언트 — 새 https URL 배포

> **한 줄**: Claude Code·Cowork 가 부르는 주소 **2개**(`ANTHROPIC_BASE_URL` · `ADMIN_API_URL`)를 https 도메인으로 바꾼다 — `OIDC_ISSUER_URL`·`OIDC_CLIENT_ID` 는 Cognito 값이라 그대로. CloudFront 경로는 이미 끊겨 있으므로 Cowork 는 바로.



### 3-1. 값 뽑기

> **한 줄**: `07-client-values.sh` 가 Ingress host 를 보고 https 도메인 값을 출력한다(방식 B 자동 인지).

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 07-client-values.sh                 # Cowork 용
bash 07-client-values.sh --claude-code   # Claude Code 용 (지금은 둘 다 같은 https URL)
```

> 기대: `ANTHROPIC_BASE_URL="https://gateway-{{env}}.{{DOMAIN}}"` · `ADMIN_API_URL="https://admin-api-{{env}}.{{DOMAIN}}"` · "US-06: https … no CloudFront in the path" 안내.



### 3-2. 직원 PC 허용목록 (새 PC/IP 가 있을 때만)

> **한 줄**: Cowork·Claude Code 가 ALB 로 직접 오므로 직원 PC IP 가 **gateway 와 admin-api 양쪽** 허용목록에 있어야 한다(CloudFront 때는 admin-api 만이었음). 이미 양쪽에 있는 PC 는 할 일 없음 — `--show` 로 확인만.

▶ **실행** · 배포 EC2  · **⚠ 바꿀 것:** `{{PC_IP}}`

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 05-allow-client-ip.sh --show
bash 05-allow-client-ip.sh --add {{PC_IP}}/32 --targets gateway,admin-api --apply   # 예: 203.0.113.42/32
bash 06-persist-annotations.sh --apply
```



### 3-3. 클라이언트 재설정

> **한 줄**: 직원 PC 의 URL 을 새 값으로 바꾸고 한 번씩 호출해 본다.

- **Claude Code**: [client-install.md](../client-install.md) 의 setup 을 새 값으로 다시(managed-settings 의 `ANTHROPIC_BASE_URL`, helper env `ADMIN_API_URL`).
- **Cowork(Windows)**: 설치 가이드의 URL 값을 새 https 도메인으로 — CloudFront URL 은 더 이상 동작하지 않는다.
- 확인: Claude Code 에서 `hi` 1회 · Cowork 질의 1회 → 응답. `status.sh` 는 5단계에서.



## 4. CloudFront 폐기 (US-02 `03` 으로 만든 배포가 있을 때만)

> **한 줄**: 도메인 https 가 살았으므로 Cowork 용 CloudFront 는 역할이 없다. disable → delete 하고, gateway SG 의 prefix-list 슬롯(55)이 회수됐는지 본다.



### 4-1. 대상 확인

> **한 줄**: 이 배포의 gateway ALB 를 origin 으로 둔 배포판만 고른다(계정에 다른 CloudFront 가 있을 수 있다).

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 03-create-cloudfront.sh --status
```

> 기대: `Id · DomainName · Status · Enabled` 한 줄. 없으면(`03` 을 쓴 적 없음) 4단계 전체 건너뜀.



### 4-2. disable → delete

> **한 줄**: CloudFront 는 먼저 비활성(전파 5~15분) 한 뒤에만 삭제할 수 있다. 삭제는 되돌릴 수 없다.

▶ **실행** · 배포 EC2  · **⚠ 바꿀 것:** `{{CF_ID}}`

```bash
CF_ID={{CF_ID}}                                   # 예: E1A2B3C4D5E6F7 (4-1 의 Id)
ETAG=$(aws cloudfront get-distribution-config --id "$CF_ID" --query ETag --output text)
aws cloudfront get-distribution-config --id "$CF_ID" --query DistributionConfig \
  | jq '.Enabled=false' > /tmp/cf-disabled.json
aws cloudfront update-distribution --id "$CF_ID" --if-match "$ETAG" \
  --distribution-config file:///tmp/cf-disabled.json --query 'Distribution.Status' --output text
```

**확인** — `Deployed  False` 가 될 때까지(5~15분):

```bash
aws cloudfront get-distribution --id "$CF_ID" \
  --query 'Distribution.[Status,DistributionConfig.Enabled]' --output text
```

▶ **실행** · 배포 EC2 — `Deployed  False` 확인 후

```bash
ETAG=$(aws cloudfront get-distribution-config --id "$CF_ID" --query ETag --output text)
aws cloudfront delete-distribution --id "$CF_ID" --if-match "$ETAG" && echo "deleted $CF_ID"
```



### 4-3. 뒷정리 확인

> **한 줄**: prefix-list 는 2-2 에서 이미 뺐다 — SG 에 남아 있지 않은지 본다. `status.sh` 의 US-02 줄은 "CloudFront 불필요 (US-06 HTTPS)" 로 표시된다(5단계).

▶ **실행** · 배포 EC2  · **⚠ 바꿀 것:** `{{DOMAIN}}`

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && source https-env.sh {{DOMAIN}}   # 예: awsome-ai-gw.click
aws ec2 describe-security-group-rules --filters Name=group-id,Values="$GW_SG" \
  --query 'SecurityGroupRules[?IsEgress==`false`].PrefixListId' --output text
```

> 기대: 빈 출력(prefix-list 없음).



## 5. 검증

> **한 줄**: https 종단(2-5)과 클라이언트(3-3)는 이미 확인했다 — 여기서는 판정기(status.sh)와 관리자 웹으로 마무리한다.



### 5-1. 판정기 — [status.sh](http://status.sh)

> **한 줄**: US-06 줄이 OK 이고, US-02 줄이 CloudFront 를 더 요구하지 않는지.

▶ **실행** · 배포 EC2

```bash
 cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash status.sh
```

> 기대:
>
> ```
>    OK   US-02  Cowork 연결 + Opus 5 등록
>             routing=invoke · claude-opus-5 ACTIVE · CloudFront 불필요 (US-06 HTTPS)
>    OK   US-06  ALB HTTPS (커스텀 도메인)
>             https://gateway-{{env}}.{{DOMAIN}} · 리스너 443 · cert …
> ```
>
> `!! US-06 — 일부 적용`(Ingress 엔 host·cert 인데 리스너에 443 없음)이면 2-3 의 helm 반영이 덜 끝났거나 SG 규칙 초과 — `kubectl get events`.
> 도메인 없이 방식 A 로 남긴 배포에선 `--  US-06 … 미적용 (선택)` 이 정상이며 다음 작업 목록에도 안 들어간다.



### 5-2. 관리자 웹

> **한 줄**: `https://admin-{{env}}.{{DOMAIN}}` 에 브라우저로 로그인(허용목록 IP 에서). 자물쇠 → 인증서 `*.{{DOMAIN}}`. 방식 A 때의 secure-cookie 로그인 문제는 https 라 사라진다. Usage 화면에 3-3 의 요청이 보이면 끝.



## 부록 A. 그림으로 보는 도메인 · DNS · 인증서 · ALB

현재(방식 A): ALB 가 자동 부여한 주소(`k8s-….elb.amazonaws.com`)로 http:80 직접 접속, 인증서 없음.
전환(방식 B): 도메인 + DNS 레코드 + ACM 인증서를 더해 https:443 으로 접속. 예시 도메인 `awsome-ai-gw.click`.
박스 안은 글꼴에 관계없이 선이 맞도록 영문으로 두고, 뜻은 각 그림 아래에 한글로 적었다.

```
■ 준비 (0단계) — 만드는 것 3개와 그 관계

┌─ Route 53 Domains ─────┐        ┌─ Route 53 Hosted Zone  awsome-ai-gw.click ─────────────────┐
│ register domain (0-2)  │        │ record (relative)    type   value                          │
│ awsome-ai-gw.click     ├─(0-2)─▶│ NS / SOA             (auto on register)                    │
│ ~$3/yr, once           │        │ gateway-dev          CNAME  k8s-...gw...elb.amazonaws.com  │
└────────────────────────┘        │ admin-dev            CNAME  k8s-...ui...elb.amazonaws.com  │
                                  │ admin-api-dev        CNAME  k8s-...api...elb.amazonaws.com │
┌─ ACM  (ALB region) ────┐        │                                                            │
│ request cert (0-3)     ├─(0-4)─▶│ _abc123 (ownership)  CNAME  _xyz.acm-validations.aws       │
│ *.awsome-ai-gw.click   │        └────────────────────────────────────────────────────────────┘
│ + apex                 │
│ -> ISSUED -> ARN       │
└────────────────────────┘

  (0-2) 도메인을 등록하면 hosted zone 이 자동으로 생긴다.
        이름 → ALB 의 CNAME 3개는 2-4 에서 우리가 넣는다.
  (0-4) ACM 이 준 CNAME 한 줄(_abc123…)을 zone 에 넣어 소유를 증명한다 → 5~30분 뒤 ISSUED.
  record (relative): zone 안의 이름은 상대 표기 —
        실제 FQDN 은 gateway-dev.awsome-ai-gw.click 처럼 도메인이 붙는다.

■ 적용 (2단계) — values(방식 B) 를 helm 으로 반영하면 ALB 3개가 이렇게 바뀐다

┌─ values-eks-fargate-dev.yaml  <- 10-switch-https.sh (2-2) ─┐
│ listen-ports: [{"HTTPS":443}]                              │
│ certificate-arn: arn:aws:acm:...      <- ARN from 0-3      │
│ host: gateway-dev.awsome-ai-gw.click  (each of 3 ingress)  │
│ inbound-cidrs: unchanged                                   │
│ gateway prefix-list: removed (CloudFront retired)          │
└─────────────────────────────────────────────────┬──────────┘
                                                  │ helm — install-eks.sh (2-3)
                                                  ▼
┌─ ALB x3  (gateway / admin-ui / admin-api) ─────────────────┐
│ listener :443  <- ACM cert attached                        │
│ listener :80   removed (http refused)                      │
│ rule: Host = gateway-dev.awsome-ai-gw.click                │
│ SG: inbound-cidrs same, port 80 -> 443                     │
│ ALB DNS unchanged -> CNAME once (2-4)                      │
└────────────────────────────────────────────────────────────┘

  listener 443 에 ACM 인증서가 붙고 80 은 사라진다(http 접속 거부) · Host 규칙 = 이름 3개
  SG 는 포트만 80→443 (규칙 수 그대로)
  ALB 의 DNS 주소는 그대로라 CNAME 은 한 번만 만든다(2-4)

■ 접속 (실행 시) — 사용자 PC 에서 게이트웨이까지

┌─ PC  (Claude Code / Cowork) ─────────────┐                  ┌─ Route 53 Hosted Zone ─────────┐
│ ANTHROPIC_BASE_URL =                     ├──(1) DNS query──▶│ gateway-dev.awsome-ai-gw.click │
│ https://gateway-dev.awsome-ai-gw.click   │                  │ = CNAME -> k8s-...gw...elb     │
│                                          │◀──(2) ALB addr───┤ = A     -> ALB IP              │
└─────────────────────────────┬────────────┘                  └────────────────────────────────┘
                              │ (3) (2)의 ALB IP:443 으로 연결 — 요청 URL 은
                              │     https://gateway-dev.awsome-ai-gw.click  (SNI/Host = 이 이름)
                              │     TLS: 서버 인증서 *.awsome-ai-gw.click 검증 → 암호화
                              ▼
┌─ ALB :443  (gateway) ────────────────────┐
│ (4) SG inbound-cidrs: source IP check    │
│ (5) match rule by Host header            │
│ (6) TLS termination                      │
└─────────────────────────────┬────────────┘
                              │ (7) http — VPC 내부
                              ▼
┌─ gateway-proxy pod ──────────────────────┐
│ (8) VK auth -> budget / rate limit       │
│     -> call Bedrock                      │
└─────────────────────────────┬────────────┘
                              │
                              ▼
┌─ Amazon Bedrock ─────────────────────────┐
│ (9) model inference                      │
└──────────────────────────────────────────┘

  (1)(2) 이름 질의/응답 · (4) 허용목록 IP 검사 · (5) Host 헤더로 규칙 매칭 · (6) TLS 종료
  (8) VK 인증 → 예산·레이트리밋 · (9) 모델 추론

■ 이름 3개와 쓰임

gateway-dev.awsome-ai-gw.click     Claude Code / Cowork 의 ANTHROPIC_BASE_URL (데이터 플레인)
admin-dev.awsome-ai-gw.click       관리자 웹 (NEXTAUTH_URL 은 차트가 https 로 자동 파생)
admin-api-dev.awsome-ai-gw.click   VK 발급 API — api-key-helper 의 ADMIN_API_URL
```

| 용어 | 뜻 |
|---|---|
| 도메인 | 내가 소유한 이름. 연 단위 요금(`.click` ~$3 · `.com` ~$14). Route 53 Domains 에서 등록 |
| hosted zone | 그 도메인의 DNS 레코드를 두는 곳. Route 53 에서 등록하면 자동 생성(월 $0.50) |
| CNAME | "이 이름 → 저 주소" 레코드. ALB 주소가 바뀌어도 이름은 그대로 |
| ACM 인증서 | AWS 무료 TLS 인증서. 와일드카드 `*.awsome-ai-gw.click` 하나로 세 이름 커버. **ALB 와 같은 리전** 필수 |
| DNS 검증 | ACM 이 준 CNAME 한 줄을 hosted zone 에 넣어 소유를 증명. Route 53 이면 콘솔 버튼 1개, 5~30분 |
| ALB 리스너 | ALB 가 받는 포트. 방식 B 는 443 만 두고 80 은 없앤다(http 접속은 거부) |

> 이 그림은 생성기로 만든 것이다 — 손으로 고치지 말 것(폭 계산이 깨진다). 생성기는 운영자 내부 repo 에 있다.
