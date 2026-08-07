# US LLM Gateway — 운영 참조 (§8 설치 후 운영 작업)

> **설치 중엔 이 문서를 볼 일이 없다.** 설치는 [README.md](README.md) → [install-guide.md](install-guide.md) 순서로 한다.
> 이 문서는 **설치가 끝난 뒤** 하는 **운영 작업**(업데이트·직원 온보딩·보안 하드닝·네트워크 경로·teardown·TTL·prod 승격·멀티계정)을 할 때 본다.
>
> 📌 본문의 `§0`**~`§6` 은 다른 문서의 절 번호**다 — `§0` = [README.md](README.md)의 범위, `§1`~`§6` = [install-guide.md](install-guide.md). (옛 §7 배포 후 보안은 이 문서 [§8-S](#8-s-배포-후-보안-하드닝-직원-오픈-전-필수) 로 옮겨왔다.)

---

## 8. 설치 후 운영 작업

> 순서 = **POC 사용 빈도순** — 자주(업데이트·온보딩·보안) → 가끔(네트워크 경로·teardown·TTL) → POC 이후(prod 승격·멀티계정).

---

### 8-U. 업데이트 (코드 변경 반영)

`git pull` 후 **바뀐 것에 따라** 아래 A·B·C 중 하나를 배포 EC2 에서 돌린다. 공통은 마지막 `install-eks.sh dev`, **서비스 코드가 바뀐 경우에만** 앞에 이미지 rebuild.

---

#### ⚠️ 0단계 — 시작 전 반드시 (건너뛰면 추론이 멈춘다)

**A·B·C 가 모두 `install-eks.sh` 로 끝나는데, 그것이 곧 `helm upgrade` 다.** helm 은 values 파일로부터 Ingress 를 다시 만들고, AWS Load Balancer Controller 가 그 Ingress 로부터 보안그룹을 다시 만든다. **values 에 없는 설정은 그때 사라진다.**

사라지면 곤란한 것이 둘 있다. 설치 후에 `kubectl annotate` 로 넣었거나 스크립트가 클러스터에만 걸어둔 값들이다.

| 어노테이션 | 없어지면 | 증상 |
|---|---|---|
| `security-group-prefix-lists` (gateway) | CloudFront 가 오리진에 못 닿음 | **모든 추론 요청이 502** — Cowork·Claude Code 전면 중단 |
| `inbound-cidrs` | 나중에 추가한 사무실·VPN 대역이 빠짐 | 해당 위치에서 접속 불가 |

먼저 dry-run 으로 무엇이 클러스터에만 있는지 본다. 아무것도 안 바꾼다.

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 06-persist-annotations.sh
```

`already matches` / `nothing to persist` 만 나오면 그대로 1단계로 간다. 옮길 것이 있다고 나오면 적용한다.

```bash
bash 06-persist-annotations.sh --apply
```

> **한 번 해두면 끝인가?** 아니다. `05-allow-client-ip.sh` 로 IP 를 추가하거나 `03-create-cloudfront.sh` 를 다시 돌린 뒤에는 다시 클러스터에만 있는 값이 생긴다. **업데이트할 때마다 dry-run 한 번**이 정답이다(읽기 전용, 수 초).

> **옛 차트를 쓰고 있다면** `06` 이 prefix-list 를 못 옮기고 경고만 한다(차트에 `ingress.gateway.annotations` 가 없는 경우). 그때는 업그레이드 **후에** 손으로 되살려야 한다 — 아래 3단계에 있다.

---

#### 1단계 — 무엇이 바뀌었는지 보고 A·B·C 를 고른다

`git pull` 하기 전에 무엇이 올 것인지 먼저 본다.

```bash
cd ~/awsome-ai-gateway && git fetch origin
git diff --stat HEAD origin/$(git rev-parse --abbrev-ref HEAD) | tail -20
```

바뀐 경로로 판정한다. 여러 개면 **가장 무거운 것**(A) 을 따른다.

| 바뀐 경로 | 따를 것 |
|---|---|
| `gateway-proxy/` `admin-api/` `admin-ui/` `*-worker/` 의 소스 | **A** (이미지 rebuild) |
| `db/versions/` `db/init/` | **B** |
| `deployment/charts/` `values-*.yaml` | **C** |
| `docs/` `update-scripts/` 만 | 배포 불필요 — `git pull` 로 끝 |

```bash
git pull --ff-only
```

**로컬 변경이 있어 `git pull` 이 거부하면** — 이 기계에는 커밋되지 않은 파일이 두 종류 쌓인다. 실제로 정상적인 상태다.

| 파일 | 정체 | 처리 |
|---|---|---|
| `values-eks-fargate-*.yaml` | **실 배포값** (계정 ID·IRSA ARN·엔드포인트). 이 기계에만 있는 원본 | **절대 버리지 말 것** |
| `.terraform.lock.hcl` | `terraform init` 이 플랫폼 해시를 채운 것 | 버려도 무방(다시 생성됨) |
| `docs/`·`update-scripts/` 아래 | 이 기계에서 손댄 스크립트 | 원격에 같은 내용이 있으면 버려도 됨 |

버리지 않고 밀어두는 편이 안전하다. 경로를 지정하면 `values-eks-fargate-*.yaml` 은 건드리지 않는다.

```bash
cd ~/awsome-ai-gateway
git stash push -u -m pre-pull -- docs deployment/terraform
git pull --ff-only
```

`git stash list` 로 남아 있으니 필요하면 `git stash pop` 으로 되살린다. 확인 후 `git stash drop`.

⚠️ 저장소 갱신에 **`git reset --hard` 를 쓰지 말 것.** `values-eks-fargate-*.yaml` 이 함께 날아가고, 그 파일은 어디에도 백업이 없다.

---

#### 2단계 — 적용

**A. 서비스 코드** (gateway-proxy·admin-api·admin-ui·worker 등) — rebuild 필요

```bash
cd ~/awsome-ai-gateway
./deployment/scripts/rebuild-image.sh gateway-proxy dev   # 바뀐 서비스마다 (인자 = <service> [env])
./deployment/scripts/install-eks.sh dev
```

> ℹ️ `install-eks.sh dev` **= 앱을 클러스터에 (재)배포하는 한 방 명령.** 인프라 값(주소·권한)을 알아서 읽어 게이트웨이 서비스(추론·관리 API·화면·워커)를 EKS 에 올리고, **DB 스키마 변경까지 같이 반영**한다 — 그래서 A·B·C 모두 이 줄로 끝난다.
> **작동 방식**: `terraform output`(엔드포인트·IRSA 역할·Cognito)을 helm `--set` 으로 주입 → 릴리스 `llm-gateway`(gateway-proxy·admin-api·admin-ui·scheduler·workers + pre-install **migration Job**)를 `helm upgrade --install --wait`. kubectl 컨텍스트 설정·네임스페이스·ExternalSecrets 확인까지 한 번에.

**B. DB 스키마** (새 migration `db/versions`·`db/init`) — rebuild 불필요, migration Job 이 자동 적용

```bash
cd ~/awsome-ai-gateway
./deployment/scripts/install-eks.sh dev
```

**C. values·chart·env** (이미지 그대로) — rebuild 불필요

```bash
cd ~/awsome-ai-gateway
./deployment/scripts/install-eks.sh dev
```

> ⚠️ **`helm upgrade` 를 직접 치지 말 것.** values 파일에는 `<RDS_PROXY_ENDPOINT>` 같은 placeholder 가 남아 있고, 실값은 `install-eks.sh` 가 `terraform output` 에서 읽어 `--set` 으로 주입한다. values 만 넘긴 업그레이드는 **DB·Redis·OIDC 주소를 placeholder 로 덮어써** 게이트웨이가 통째로 죽는다.

#### 「terraform output 실패」로 멈추면 — `terraform apply` 를 돌리지 말 것

```
✗  terraform output 실패. terraform apply 를 먼저 성공시켜야 합니다.
```

**이 메시지는 대개 사실이 아니다.** 인프라는 멀쩡하고 state 도 정상인데, 십중팔구 **provider 플러그인 체크섬이 `.terraform.lock.hcl` 과 안 맞는 것**이다. 시간이 지나 terraform 이 갱신되거나 캐시가 어긋나면 생긴다. 안내를 곧이곧대로 따라 `terraform apply` 를 돌리면 멀쩡한 인프라에 변경을 시도하게 되므로 **하지 말 것.**

먼저 진짜 오류를 본다.

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform output
```

`Required plugins are not installed` / `does not match any of the checksums recorded in the dependency lock file` 가 보이면 그 경우다. `terraform init` 이 lock 에 적힌 체크섬에 맞는 바이너리를 다시 받는다 — **인프라는 건드리지 않고 `.terraform/` 만 정리하며, state 는 읽기만 한다.**

```bash
terraform init
terraform output | head        # 실값이 나오면 복구된 것
```

lock 에 이 플랫폼용 해시가 아예 없어 그래도 실패하면, 버전은 그대로 두고 해시만 채운다.

```bash
terraform providers lock -platform=linux_amd64
```

> ⚠️ **`terraform init -upgrade` 는 쓰지 말 것.** provider 버전 자체를 올려 lock 파일이 바뀌고, 다음에 누군가 `terraform apply` 를 돌릴 때 의도하지 않은 인프라 변경이 딸려온다. 여기서 필요한 건 `terraform output` 뿐이다.

`terraform init` 은 **`.terraform.lock.hcl` 을 수정한다**(이 플랫폼용 해시 추가). 정상이지만 이 파일은 git 추적 대상이라 다음 `git pull` 을 막는다 — 1단계의 stash 명령이 `deployment/terraform` 을 포함하는 이유가 이것이다.

---

#### 3단계 — 확인

```bash
cd ~/awsome-ai-gateway
kubectl -n llm-gateway get pods                    # 전부 Running / 1-1
helm -n llm-gateway history llm-gateway | tail -3  # 새 revision 이 deployed 인가
```

**어노테이션이 살아남았는지 본다.** 0단계를 했다면 그대로 있어야 한다.

```bash
kubectl -n llm-gateway get ingress -o custom-columns=\
'NAME:.metadata.name,CIDRS:.metadata.annotations.alb\.ingress\.kubernetes\.io/inbound-cidrs,PL:.metadata.annotations.alb\.ingress\.kubernetes\.io/security-group-prefix-lists'
```

gateway 행의 `PL` 이 비었으면 **지금 CloudFront 경유 요청이 전부 502 다.** 즉시 되살린다(배포는 그대로 두고 어노테이션만 다시 걸어 수 초).

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 03-create-cloudfront.sh --allow-cloudfront
```

마지막으로 종단 호출.

```bash
bash 04-verify.sh                       # DB 상태 + 비용 기록까지
```

---

#### 4단계 — 안 되면 되돌린다

```bash
helm -n llm-gateway history llm-gateway            # 직전 revision 번호 확인
helm -n llm-gateway rollback llm-gateway <REV>     # 그 번호로
```

⚠️ **롤백 후에도 3단계의 어노테이션 확인을 다시 하라.** rollback 역시 Ingress 를 다시 만든다.

이미지만 되돌리고 싶다면(대시보드 등 한 서비스만 문제일 때) values 의 해당 `tag` 를 옛 값으로 되돌리고 `install-eks.sh` 를 다시 돌린다.

---

### 8-Y. 직원 온보딩 — Cognito 사용자 추가

§3-8 은 **관리자 한 명**만 만든다. 직원이 [§6](install-guide.md#6-클라이언트-설치-claude-code-awsome-gateway-cli) 의 `gateway-cli login` 을 하려면, 그 전에 **관리자가 직원을 Cognito 에 미리 등록**해둬야 한다. 방법은 두 가지.

**공통 — 어느 그룹에 넣나**

- **팀 그룹**(`Claude_default-department_default-team`) = **필수.** 없으면 로그인은 되지만 VK 발급이 **403**. 이 배포는 팀이 하나뿐이라 전원 이 그룹에 넣는다(§3-2 `cognito_groups`).
- `ClaudeAdmin` = **관리자에게만.** admin-ui(`/models`·예산 등)를 쓸 사람만. 일반 직원은 **넣지 않는다.**

#### 방법 A — admin-ui 화면 (권장, 소수)

admin-ui(`/models` 와 같은 사이트)의 **사용자 관리** 화면에서 초대·그룹 배정. 관리자 로그인 + `inbound-cidrs` 안에서. 몇 명이면 이게 제일 쉽다.

#### 방법 B — CLI (대량·자동화)

§3-8 과 같은 명령이다. 직원 이메일만 바꾸고 `ClaudeAdmin` **줄은 뺀다**:

▶ **실행** · 배포 EC2

```bash
POOL_ID=$(cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev \
  && terraform output -raw cognito_user_pool_id)
EMAIL="employee@your-org.com"                 # ← 직원 이메일
TEMP_PW='<임시비번 12자+ 대소문자·숫자·특수문자>'   # 직원이 첫 로그인 때 변경

aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$EMAIL" \
  --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --temporary-password "$TEMP_PW" --message-action SUPPRESS
# 팀 그룹만 (관리자 아님 → ClaudeAdmin 안 넣음)
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" --username "$EMAIL" \
  --group-name "Claude_default-department_default-team"
```

> `--message-action SUPPRESS` 는 Cognito 기본 초대 메일을 **안 보낸다**(SES 미설정 배포라). 이메일·임시비번을 관리자가 직원에게 **직접 전달**한다. 직원은 그걸로 §6 `gateway-cli login` 팝업에 로그인 → 첫 로그인 시 새 비번으로 변경.
>
> ⚠️ 위 방법은 **기존 팀에 사용자**를 넣는 것. **새 팀**을 나눌 거면 그룹 생성만으로 안 되니 아래 **새 팀 추가** 절을 본다. 이 배포는 팀 하나라 해당 없음.

#### 새 팀(부서) 추가 — 그룹 생성만으로는 안 된다

**새 팀**을 하나 만들려면 Cognito 그룹 생성 하나로 안 끝난다 — **이름 규칙 → terraform 그룹 → (멤버 첫 로그인) → 예산** 을 다 밟아야 admin-ui 에서 실제로 쓸 수 있다.

```text
  ①  이름 정하기 — Claude_<부서>_<팀>   ("Claude_" 없으면 매핑 실패)
        └ 예) Claude_AI-department_agent-team → 부서 AI-department · 팀 agent-team
        └ 밑줄 _ = 구분자 → 부서·팀 이름엔 하이픈만
                    │
                    ▼
  ②  그룹 생성   — tfvars cognito_groups 에 추가 → terraform apply
        └ 콘솔 수동 생성 X — 이 목록에서만 관리된다
                    │
                    ▼
  ③  사용자 배정 — 방법 A(admin-ui) / B(CLI) 로 그 그룹에 add-user
                    │
                    ▼
  ④  첫 로그인   — ⚡ 이 순간 팀이 DB 에 "자동 생성"(lazy) → admin-ui 에 등장
        └ 단, 예산 $0 · HARD_BLOCK 으로 생성 → 그 팀 요청 전부 429 (로그 없음)
                    │
                    ▼
  ⑤  예산 부여   — admin-ui /budgets 에서 그 팀에 한도 설정 → ✅ 사용 가능

  ──────────────────────────────────────────────────────────────
  ✕ 흔한 실패
        · 이름에 Claude_ 없음  → 로그인 시 "no group mapping found"
        · ⑤ 예산을 건너뜀      → "로그인은 되는데 그 팀 전부 429"
```

**②의 terraform 그룹** — tfvars 에 한 줄 더하고 apply:

```hcl
# §3-2 terraform.tfvars — cognito_groups (그룹은 이 목록에서만 생성·관리)
cognito_groups = [
  "Claude_default-department_default-team",
  "Claude_AI-department_agent-team",     # ← 새 팀 (밑줄=구분자, 이름엔 하이픈)
]
```

> 🔴 **가장 흔한 함정 = ⑤ 예산 누락.** 그룹만 만들고 예산을 안 주면 "로그인·토큰은 정상인데 그 팀 요청 전부 429, 로그도 없음" — 자동 생성 팀이 `$0`·`HARD_BLOCK` 이라 그렇다(§6 도입부 예산 🔴와 같은 원인). 근거: `oidc_service.py` 의 `_parse_group`(이름 매핑) · `_get_or_create_team`(첫 로그인 시 팀 + `$0 HARD_BLOCK` 자동 생성).

---

### 8-S. 배포 후 보안 하드닝 (직원 오픈 전 필수)

> [install-guide.md](install-guide.md) §1~§6 설치가 끝나면, **직원에게 열기 전** 여기서 하드닝한다. 지금까지는 **설치 편의로 입구가 넓게 열려 있다** — 입구 IP 를 직원 대역으로 정리하고 **admin 콘솔을 관리자 전용으로 가둬야** 직원 오픈이 가능하다. HTTPS 가 없으므로([§0](README.md#0-이번-배포의-범위-확정)) **IP 허용목록이 유일한 보호막**이다.

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
- **관리자/직원 IP** 를 프롬프트로 받는다 — 맨 IP(`1.2.3.4` → `/32`), **CIDR 대역**(`203.0.113.0/24` 그대로), 또는 **콤마로 여러 개**(`1.2.3.4,203.0.113.0/24`).
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
  > 🔴 단 **저장소를 갱신하지 않은 설치**에서는 아니다. 예전 버전은 `sed` 로 `inbound-cidrs:` 가 들어간 **모든 줄**을 같은 값으로 덮어, 좁혀 둔 관리자 대역이 아무 경고 없이 공통값으로 되돌아갔다. 이 하드닝을 하기 전에 [§8-U](#8-u-업데이트-코드-변경-반영) 로 저장소를 먼저 갱신할 것.
- POC 로 **allowlist 전체가 신뢰된 관리자/VPN** 이면 공유해도 된다 — dev-login 이 그 신뢰 경계 안에서만 노출된다.

> 🔴 admin-ui·admin-api 를 `**0.0.0.0/0` 이나 광범위 대역에 두지 말 것** — dev-login 이 **서명 없는 admin 토큰**을 즉시 내주므로 닿는 사람 = admin 이다.

---

### 8-N. Bedrock 을 NAT 대신 VPC Endpoint(PrivateLink)로

> **신규 설치는 할 일이 없다.** `deployment/terraform/modules/vpc/main.tf` 가 `bedrock-runtime`·`bedrock`·`sts` 인터페이스 엔드포인트를 조건 없이 선언하므로, 지금 `terraform apply` 로 만든 VPC 엔 처음부터 들어 있다.
>
> **그 선언이 추가되기 전에 만든 VPC** 만 이 절의 대상이다. 코드는 이미 저장소에 있고 apply 만 안 된 상태이며, **아무도 알려주지 않는다** — Bedrock 호출은 계속 성공하고, 다만 NAT 를 거쳐 퍼블릭 인터넷을 지날 뿐이다.

**무엇이 바뀌나**


| | 지금 (엔드포인트 없음) | 적용 후 |
| --- | --- | --- |
| Bedrock 호출 | 파드 → NAT GW → IGW → **퍼블릭 인터넷** → Bedrock | 파드 → 내 서브넷의 엔드포인트 ENI → PrivateLink |
| STS (IRSA 자격증명 갱신) | 위와 동일하게 NAT 경유 | 엔드포인트 경유 |
| ECR pull · Cognito · 타 리전 AgentCore web search | NAT | **NAT 그대로** (엔드포인트 없음) |
| 데이터 처리료 | $0.045/GB (NAT) | $0.01/GB + ENI 시간당 요금 |


> 🔴 **NAT 는 없어지지 않는다.** 위 표의 3행 때문에 NAT 게이트웨이는 그대로 필요하다. 따라서 이 변경은 **비용 절감이 아니라 순증**이다 — ENI 6개(서비스 3 × AZ 2) × 시간당 $0.01 ≈ **월 $44**(us-west-2). 얻는 것은 **Bedrock 트래픽이 퍼블릭 인터넷을 지나지 않는다**는 컴플라이언스 쪽 가치다. 그게 요구사항이 아니라면 안 해도 되는 변경이다.

**(1) 대상인지 확인** — 읽기 전용이다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/enable-bedrock-vpce.sh dev
```

세 엔드포인트의 존재 여부와 private 서브넷의 기본 경로를 찍고, 이어서 `terraform plan` 을 타깃 지정으로 떠서 무엇이 생기는지 보여준다. 여기까지 아무것도 바꾸지 않는다.

**(2) plan 이 `0 to change, 0 to destroy` 가 아니면 멈춘다**

스크립트가 이 조건을 강제하고, 어긋나면 진행을 거부한다. 실제로 이 배포에서 타깃 없이 plan 을 떴을 때 나온 것:

```
Plan: 5 to add, 0 to change, 1 to destroy.
  # module.aurora.aws_secretsmanager_secret_version.db[0] must be replaced
  ~ secret_string = (sensitive value) # forces replacement
```

VPC 엔드포인트와 아무 상관 없는 **DB 자격증명 시크릿**이다. 원인은 `modules/aurora-postgresql/secrets.tf` 가 Aurora 관리형 master secret 을 data source 로 읽어 `/llm-gateway/<env>/db` 의 `master_password` 키에 **복사**해 두는 구조인데, Aurora 쪽이 로테이트되면 그 복사본이 낡는다는 것이다. 모듈 주석은 *"자동 rotation 은 AWS default 가 아니므로 실질적 rotation 없음"* 이라고 단정하지만 실제로는 `RotationEnabled: true` 였다.

다만 **런타임은 이 복사본을 쓰지 않는다** — `fill-org-values.sh` 가 values 의 `masterPasswordRemoteKey` 를 RDS 관리형 시크릿(`rds!cluster-<uuid>`)으로 걸어두므로 ExternalSecrets 가 로테이션되는 원본을 직접 읽는다. 즉 이건 terraform state 상의 드리프트이지 장애 요인이 아니다. 그래도 **엔드포인트를 켜는 김에 DB 시크릿을 같이 건드리는 것은 다른 결정**이므로, 스크립트는 `-target` 으로 VPC 엔드포인트 4개만 잡는다.

> ℹ️ `-target` 은 terraform 이 "예외적 상황에서만 쓰라"고 경고하는 옵션이 맞다. 여기서 정당한 이유는 **오래 운영한 배포에 리소스를 추가**하는 작업이기 때문이다. 타깃 없이 돌리면 그동안 쌓인 무관한 드리프트가 같이 적용되고, 그게 평범한 변경을 장애로 만든다.

**(3) 적용**

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/enable-bedrock-vpce.sh dev --apply
```

> 🔴 **위험한 쪽은 Bedrock 이 아니라 STS 다.** 엔드포인트가 생기는 순간 VPC 전체에서 private DNS 가 뒤집히고, `sts.<region>.amazonaws.com` 도 같이 옮겨간다. **모든 파드가 IRSA 자격증명을 이 경로로 갱신**하므로, 여기가 막히면 Bedrock 호출이 전부 실패한다.
>
> 확인점은 하나다 — 엔드포인트 보안그룹이 **private 서브넷 CIDR 에서 443 을 허용**하는가. 차트의 terraform 이 그렇게 만들고 Fargate 파드는 전부 그 서브넷에 뜨므로 무중단이 기대값이다. `--apply` 전에 plan 출력의 `ingress` 블록에서 CIDR 이 실제 private 서브넷과 일치하는지 눈으로 확인하면 된다.

**(4) 커넥션 풀 비우기 — 검증 전에 반드시**

```bash
kubectl rollout restart deploy/llm-gateway-gateway-proxy -n llm-gateway
kubectl rollout status  deploy/llm-gateway-gateway-proxy -n llm-gateway --timeout=5m
```

> 🔴 **이 단계를 건너뛰면 멀쩡한 변경을 장애로 오진한다.** 경로 이전 자체엔 재시작이 필요 없지만(새 커넥션마다 DNS 를 다시 해석한다), botocore 풀에 **죽은 커넥션**이 남아 있으면 그걸 재사용하는 요청이 502 `ConnectionClosedError` 로 실패한다. idle 350초에 조용히 끊긴 소켓들이고, 풀은 호스트당 여러 개를 들고 있어서 **연속으로** 실패한다.
>
> 이 배포에서 실제로 겪었다 — 적용 후 종단 호출이 **2회 연속 502**, 예외는 둘 다 `ConnectionClosedError`. 엔드포인트가 요청을 거절하는 것처럼 보였지만, 파드를 새로 띄우자 **첫 호출에 200**이었다. 원인은 3일 전(마지막 트래픽) 이후 방치된 소켓들이었고 엔드포인트와 무관했다. 레플리카가 1개여도 기본 롤링 전략이 새 파드를 먼저 Ready 로 만들므로 무중단이다.

**(5) 검증**

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/enable-bedrock-vpce.sh dev --verify
```

네임스페이스 안에 일회용 파드를 띄워 **게이트웨이와 같은 DNS 경로로** `bedrock-runtime`·`sts` 를 조회하고, VPC 대역(`10.30.x`) 으로 해석되는지와 443 도달 여부를 찍는다. 파드는 끝나면 지운다.

추론이 멀쩡한지는 별도로:

```bash
cd ~/awsome-ai-gateway && ./deployment/scripts/smoke-test.sh --with-bedrock
```

**(6) 되돌리기**

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/enable-bedrock-vpce.sh dev --rollback
```

엔드포인트 3개와 보안그룹만 지운다. 새 커넥션은 즉시 NAT 로 복귀하고 그 밖에는 아무것도 건드리지 않는다.

**함정 3가지**

- **경로 이전에는 재시작이 필요 없지만, 검증 전에는 반드시 한다** — 위 (4). 이전 자체는 새 커넥션이 DNS 를 다시 해석하며 저절로 되지만, 풀에 남은 죽은 커넥션이 502 를 뿜어 **변경이 깨뜨린 것처럼 보인다.**
- **간헐적 502/504 는 이걸로 안 고쳐진다.** 원인인 idle timeout 이 인터페이스 엔드포인트에서도 **똑같이 350초**다(NLB 기반). 경로가 사설로 바뀔 뿐 죽은 커넥션 문제는 그대로다 — 그건 클라이언트 쪽에서 잡아야 한다.
- **타 리전 AgentCore web search 는 영향이 없다.** 서비스도(`bedrock-agentcore`) 리전도 다르므로 이 엔드포인트와 무관하고, 계속 NAT 를 쓴다.

---

### 8-T. teardown (과금 중단 · 초기화)

> 🔴 **되돌릴 수 없다.** 아래는 dev 스택 전체(EKS·Aurora·시크릿·웹서치)를 파기한다. 데이터가 필요하면 **먼저 Aurora 스냅샷**을 뜬다.

```bash
helm uninstall llm-gateway -n llm-gateway
kubectl delete namespace llm-gateway
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev && terraform destroy
# Terraform이 안 지우는 잔여물: ECR 이미지, Secrets Manager(/llm-gateway/dev/*),
#   AgentCore WebSearch gateway(provision_agentcore_websearch.py teardown), tfstate(S3/DynamoDB)
python3 ~/awsome-ai-gateway/deployment/scripts/provision_agentcore_websearch.py teardown  # (REGION/GW_NAME env 동일)
```

---

### 8-Z. 토큰 TTL 조절

인증 토큰 수명(기본값)과 **바꾸는 이유**는 [client-setup-explained.md 의 "만료 조건"](client-setup-explained.md#언제-다시-인증해야-하나-만료-조건) 참고. 여기서는 **어떻게 바꾸나**만 다룬다. 둘은 위치·반영 방식이 다르다.


| 무엇                          | 기본      | 어디서                 | 반영            |
| --------------------------- | ------- | ------------------- | ------------- |
| **refresh_token** (재로그인 주기) | **7일**  | Cognito (terraform) | 새로 로그인하는 사람부터 |
| access/id_token             | 1시간     | Cognito (terraform) | 위와 동일         |
| **VK** (게이트웨이 열쇠)           | **1시간** | admin-api env       | 다음 VK 발급부터    |


#### ① Cognito 토큰 TTL (refresh 7일 · access/id 1시간)

`cognito/main.tf` 에 **하드코딩**돼 있다(변수 아님 → tfvars 로는 못 바꾼다). 파일을 직접 고치고 apply:

```hcl
# deployment/terraform/modules/cognito/main.tf (line 121~123)
access_token_validity  = 1    # 시간
id_token_validity      = 1    # 시간
refresh_token_validity = 14   # ← 7 에서 변경 (일)
```

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform apply     # Cognito client 설정만 갱신 (리소스 재생성 아님, 즉시)
```

> ⚠️ **콘솔/**`aws cognito-idp update-user-pool-client` **로 바꾸지 말 것** — update 는 전체 덮어쓰기라 다른 설정을 빠뜨리면 리셋되고, 다음 `terraform apply` 가 **소스값(7일)으로 되돌린다.** terraform 이 정본이다.
>
> ℹ️ **이미 로그인한 직원에겐 즉시 적용 안 됨** — refresh_token 수명은 **발급 시점에 토큰에 박힌다.** 늘려도 그들은 다음 재로그인 때 새 수명을 받는다. (줄이는 경우도 마찬가지 — 이미 발급된 건 원래 수명대로 산다.)

#### ② VK TTL (게이트웨이 열쇠, 1시간)

admin-api 환경변수 `OIDC_VK_TTL_HOURS`(`config.py:90` 기본 1)다. values 로 오버라이드:

```yaml
# values-eks-fargate-dev.yaml — adminApi.env
OIDC_VK_TTL_HOURS: "2"     # 1 → 2시간
```

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
./deployment/scripts/install-eks.sh dev   # 파드 재시작 = env 반영
```

> 짧을수록 유출 내성 ↑ · admin-api 재발급 부하 ↑. 길수록 반대. 기본 1시간이면 대개 충분하다(helper 가 30분 전 미리 재발급하므로 요청이 끊기지 않는다).

---

### 8-P. dev → prod 승격 (검증 후 운영 전환)

> prod 는 dev 의 스위치가 아니라 **나란히 서는 별개 스택**(별도 tfstate·EKS·Aurora·Cognito, namespace 만 동일). 승격 = **§1~§6 을** `prod` **env 로 재실행** — `install-eks.sh prod` · `/llm-gateway/prod/`* · `values-*-prod.yaml` 만 `dev`→`prod`(이미지는 ECR 공유라 재빌드 불필요, 코드는 §2 브랜치 동일).
>
> **prod 에서만 다른 것**: ① **[§8-S 하드닝 먼저](#8-s-배포-후-보안-하드닝-직원-오픈-전-필수)**(prod values 도 `DEV_LOGIN_ENABLED=true` 선적재) · ② **직원 env 4줄(§6)을 prod 엔드포인트로 교체**(Cognito·admin-api·gateway 새로 생김) · ③ 웹서치 `GW_NAME=…-prod` 별도. 패치는 env 별 [§8-U](#8-u-업데이트-코드-변경-반영)로(dev→prod 자동 전파 없음).

---

### 8-X. 멀티계정 확장 — claude-code 를 별도 계정 Bedrock 으로

이 배포는 **단일 계정**이라 claude-code 가 이 계정 Bedrock 을 직접 쓴다(§4-3 에서 `account_role_arn=NULL` = in-account). 나중에 claude-code 트래픽을 **다른 AWS 계정**의 Bedrock 으로 보내려면(계정 분리·비용 격리·규제 등) 아래 3가지를 세팅하고 §4-3 SQL 을 **반대로** 돌리면 된다.

> **게이트웨이 코드 변경은 없다.** cross-account 분기는 이미 구현돼 있고(`BedrockAccountClientProvider` + `client_resolver`), **DB 의** `account_role_arn` **한 컬럼이 스위치**다 — 값이 있으면 그 역할을 AssumeRole, 비어 있으면 in-account(`main.py:181`). §4-3 이 그 스위치를 끈 것뿐이다.

**① 대상 계정에 역할 만들기** (terraform / 콘솔) — 게이트웨이 계정이 assume 할 수 있는 역할:

- **trust policy**: 게이트웨이 파드의 IRSA 역할(`llm-gateway-dev-gateway-proxy-bedrock`)이 `sts:AssumeRole` 하도록 허용 + `ExternalId` **조건**(confused-deputy 방지).
- **권한**: `bedrock:InvokeModel` 등 대상 계정 Bedrock 호출 권한.

**② 게이트웨이 파드 IRSA 에** `sts:AssumeRole` **추가** — 지금은 자기 계정 Bedrock 만 부른다. 다른 계정 역할을 assume 하려면 그 권한이 IRSA 정책에 있어야 한다(대상 = ①의 역할 ARN).

**③ DB 한 줄 — §4-3 을 되돌린다** (in-account → cross-account):

▶ **실행** · 배포 EC2 (§4-1 처럼 psql 파드로)

```sql
UPDATE model.routing_profiles
   SET account_role_arn = 'arn:aws:iam::<대상계정>:role/<역할>',   -- ①에서 만든 역할
       external_id      = '<ExternalId>',                        -- ①의 trust 조건과 동일
       region           = '<대상 리전>'                            -- 대상 계정 Bedrock 리전
 WHERE client = 'claude-code';
```

그다음 **Redis 캐시 플러시** 필수 — `routing_profiles` 를 psql 로 직접 고치면 캐시(`routing_profile:claude-code`, TTL 5분)가 안 지워져 최대 5분 지연된다. admin-api 경로(있으면)로 바꾸거나 캐시 키를 지운다.

> **롤백은 즉시**: `account_role_arn`·`external_id` 를 다시 `NULL` 로 → 다음 요청부터 in-account 복귀(무배포).
>
> **codex·cowork 도 같은 구조**다(원 설계: claude-code·codex·cowork 를 각각 다른 계정으로). 이 배포는 둘을 out-of-scope(§0)로 두고 [§4-2 (D)](install-guide.md#4-2-3모델-alias-를-us-geo-프로파일로-sonnet-5는-신규)에서 INACTIVE 처리했다. 그것들까지 멀티계정으로 살리려면 클라이언트마다 ①②③ 을 반복한다.
