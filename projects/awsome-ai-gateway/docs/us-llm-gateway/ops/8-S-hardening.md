# 8-S. 배포 후 보안 하드닝 (직원 오픈 전 필수)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-S**

> [install-guide.md](../install-guide.md) §1~§6 설치가 끝나면, **직원에게 열기 전** 여기서 하드닝한다. 지금까지는 **설치 편의로 입구가 넓게 열려 있다** — 입구 IP 를 직원 대역으로 정리하고 **admin 콘솔을 관리자 전용으로 가둬야** 직원 오픈이 가능하다. HTTPS 가 없으므로([§0](../install-overview.md#0-이번-배포의-범위-확정)) **IP 허용목록이 유일한 보호막**이다.

> - **입구 IP 확대(직원 대역) · admin 콘솔 IP 좁히기 · ALB 잠금 검증** → 명령만 돌리면 되는 것들(절차 (1)~(3) 아래). 아직 안 함.
> - **입구 대역** → (1) "네트워크팀에 딱 하나 묻는다" 가 선결. **답에 따라 §0(HTTPS 미사용)을 재검토해야 할 수도 있다.**

**해야 할 것 (게이트):**


| 항목          | 조치                                                                                                                                                               |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| admin 콘솔 보호 | admin-ui 엔 실제 OIDC 로그인이 없어 `DEV_LOGIN_ENABLED=false` 로 하면 **GUI 가 잠긴다**. 그대로 두고 **admin-ui·admin-api 를 관리자 IP/VPN 전용**으로 좁혀 네트워크로 보호한다(아래 "admin 콘솔은 네트워크로 보호"). |
| 입구 접근제한     | values `inbound-cidrs` 를 **직원 출구 대역**으로 확대하고 **설치용 관리자** `/32` **는 제거**. HTTPS 대신 이 IP제한이 보호막(§0 결정). 절차 = (1)(2) 아래.                                            |
| 허용 모델 목록    | §4에서 등록한 3모델 외 불필요 alias 정리.                                                                                                                                     |


**절차:**

**(1) 입구 대역 확보 — 네트워크팀에 질의 필요.**

받은 대역으로 §3-6 의 `inbound-cidrs` 를 교체 → `install-eks.sh dev` 재적용. 답이 "그런 대역 없음(재택·각자 ISP·동적 IP)"이면 `0.0.0.0/0` 으로 열지 말 것 — HTTP+IP제한이라는 §0 전제가 성립하지 않으므로 **NAT/프록시로 출구 고정**, 

>

IP 허용목록은 ALB 앞단 **보안 그룹의 ingress** 인데, `aws ec2 authorize-security-group-ingress` 로 **직접 넣지 말 것** — AWS Load Balancer Controller 가 ingress annotation(`alb.ingress.kubernetes.io/inbound-cidrs`)에서 SG 를 **재조정**하므로 손으로 넣은 규칙은 다음 `install-eks.sh` 때 사라진다. 정답은 **values 의** `inbound-cidrs` **를 바꾸고 재적용**하는 것이고, 설치 때 쓴 `fill-org-values.sh` 가 그걸 해준다(멱등 — IP 넓힐 때마다 다시 실행).

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/fill-org-values.sh dev
```

스크립트가 하는 일:

- **배포 EC2 IP** 는 `checkip.amazonaws.com` 로 자동 감지(→ `/32`).
- **관리자/직원 IP** 를 프롬프트로 받는다 — 맨 IP(`1.2.3.4` → `/32`), **CIDR 대역**(`52.94.133.0/24` 그대로), 또는 **콤마로 여러 개**(`1.2.3.4,52.94.133.0/24`).
- 요약 확인 후 `y` → values 의 `alb.ingress.kubernetes.io/inbound-cidrs` 에 그 값을 쓴다.

그다음 **재적용해야 SG 에 반영**된다:

```bash
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```

> 🔴 **덮어쓴다 = 이전 IP 가 사라진다.** 스크립트는 `inbound-cidrs` 를 **입력값으로 통째로 교체**한다(EC2 IP + 이번에 넣은 것만). 기존 허용 IP 를 유지하며 **추가**하려면 프롬프트에 **원하는 전체 목록을 콤마로** 다 넣는다. (또는 values 파일의 `inbound-cidrs:` 줄을 직접 편집 → `install-eks.sh dev`.)
>
> ℹ️ 반영은 즉시가 아니다 — `install-eks.sh` 뒤 ALB Controller 가 SG 를 갱신하는 데 수십 초. 아래 (3) 으로 확인한다.

**(3) ALB 잠금 검증**

▶ **실행** · 배포 EC2

```bash
VPC_ID=$(cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev && terraform output -raw vpc_id)
for LB in $(aws elbv2 describe-load-balancers --query "LoadBalancers[?VpcId=='$VPC_ID'].LoadBalancerArn" --output text); do
  for SG in $(aws elbv2 describe-load-balancers --load-balancer-arns "$LB" --query 'LoadBalancers[0].SecurityGroups' --output text); do
    aws ec2 describe-security-groups --group-ids "$SG" \
      --query 'SecurityGroups[0].IpPermissions[].{Port:FromPort,CIDRs:IpRanges[].CidrIp}' --output json
  done
done
```

→ 허용 CIDR이 위에서 확보한 대역과 일치하고 `0.0.0.0/0` 이 없으면 잠긴 것.

**admin 콘솔은 네트워크로 보호 (dev-login 유지)**

admin-ui 에는 **실제 로그인(OIDC)이 없다** — 유일한 경로가 dev-login 이고 `DEV_LOGIN_ENABLED=false` 면 **404 로 아무도 못 들어간다**(admin-api 도 dev 토큰 거부, `auth.py:100`). 그래서 이 배포는 **dev-login 을 켠 채, admin 콘솔을 네트워크로 가둔다**:

- **admin-ui·admin-api 는 관리자 IP/VPN 대역만** 닿게 한다. 데이터 플레인(gateway)은 직원 대역으로 넓혀도 컨트롤 플레인은 관리자만.
- ⚠️ **차트 기본값은 3 ALB(gateway·admin-ui·admin-api)가 `inbound-cidrs` 를 공유**한다 — 그냥 두면 직원 대역이 admin 콘솔에도 닿아 dev-login 우회를 누구나 쓸 수 있다.
- ✅ **Ingress 별로 따로 좁힐 수 있다.** `templates/common/ingress.yaml` 이 어노테이션을 두 겹으로 읽는다 — 우선순위는 **Ingress 전용 > 템플릿 기본값 > 공통**. 공통 맵엔 직원 대역을 두고(gateway 가 상속), admin 두 개만 관리자 대역으로 덮는다:

  ```yaml
  ingress:
    annotations:
      alb.ingress.kubernetes.io/inbound-cidrs: "<직원 대역>"        # 공통 → gateway 가 상속
    adminUi:
      annotations:
        alb.ingress.kubernetes.io/inbound-cidrs: "<관리자 대역>"     # 공통값을 덮어씀
    adminApi:
      annotations:
        alb.ingress.kubernetes.io/inbound-cidrs: "<관리자 대역>"
  ```

  적용은 `./deployment/scripts/install-eks.sh <env>`.

  > ℹ️ `fill-org-values.sh` 는 **공통 줄만** 고치므로(`fill-org-values.sh:106`) 나중에 IP 를 넓히려고 다시 돌려도 위 전용 값은 그대로 남는다. 순서는 상관없다.
  >
  > 🔴 단 **저장소를 갱신하지 않은 설치**에서는 아니다. 예전 버전은 `sed` 로 `inbound-cidrs:` 가 들어간 **모든 줄**을 같은 값으로 덮어, 좁혀 둔 관리자 대역이 아무 경고 없이 공통값으로 되돌아갔다. 이 하드닝을 하기 전에 [§8-U](8-U-update.md) 로 저장소를 먼저 갱신할 것.
- POC 로 **allowlist 전체가 신뢰된 관리자/VPN** 이면 공유해도 된다 — dev-login 이 그 신뢰 경계 안에서만 노출된다.

> 🔴 admin-ui·admin-api 를 `**0.0.0.0/0` 이나 광범위 대역에 두지 말 것** — dev-login 이 **서명 없는 admin 토큰**을 즉시 내주므로 닿는 사람 = admin 이다.
