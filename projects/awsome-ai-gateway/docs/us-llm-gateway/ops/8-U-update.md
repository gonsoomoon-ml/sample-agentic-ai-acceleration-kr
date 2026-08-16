# 8-U. 업데이트 (코드 변경 반영)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-U**

`git pull` 후 **바뀐 것에 따라** 아래 A·B·C 중 하나를 배포 EC2 에서 돌린다. 공통은 마지막 `install-eks.sh dev`, **서비스 코드가 바뀐 경우에만** 앞에 이미지 rebuild.

---

## ⚠️ 0단계 — 시작 전 반드시 (건너뛰면 추론이 멈춘다)

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

## 1단계 — 무엇이 바뀌었는지 보고 A·B·C 를 고른다

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

⚠️ **백업 없이** `git reset --hard` 를 쓰지 말 것 — `values-eks-fargate-*.yaml` 이 함께 날아가고, 그 파일은 어디에도 백업이 없다. 다만 이 브랜치는 리베이스되므로 위 `git pull --ff-only` 가 실패하는 경우가 있다. 그때는 **백업을 뜨고 `reset --hard` 로 원격에 맞추는** 정본 절차를 쓴다 — [README.md 「3. 적용하기」](../README.md#3-적용하기-배포-ec2-에서).

---

## 2단계 — 적용

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

## 「terraform output 실패」로 멈추면 — `terraform apply` 를 돌리지 말 것

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

## 3단계 — 확인

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

## 4단계 — 안 되면 되돌린다

```bash
helm -n llm-gateway history llm-gateway            # 직전 revision 번호 확인
helm -n llm-gateway rollback llm-gateway <REV>     # 그 번호로
```

⚠️ **롤백 후에도 3단계의 어노테이션 확인을 다시 하라.** rollback 역시 Ingress 를 다시 만든다.

이미지만 되돌리고 싶다면(대시보드 등 한 서비스만 문제일 때) values 의 해당 `tag` 를 옛 값으로 되돌리고 `install-eks.sh` 를 다시 돌린다.
