# 8-E. EKS 버전 업그레이드 (1.31 → 1.34)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-E**

> 📒 **`US-05` · 등급 필수(지원 만료·비용)** — [README.md 「최신 업데이트」](../README.md#2-최신-업데이트). 적용 여부는 `update-scripts/status.sh` 로 확인한다.

> **신규 설치는 할 일이 없다.** terraform 기본값이 1.34 이고, `terraform.tfvars.example` 에도 `eks_cluster_version = "1.34"` 가 **보이게 명시**돼 있다 — 지금 만든 클러스터는 처음부터 1.34 이고, tfvars 에 버전 pin 도 설치 시점부터 존재한다(그래서 아래 「pull 직후 함정」은 신규 설치에는 해당 없다).
>
> **이 절의 대상은 그 전에 만든 클러스터**다. EKS 는 만들어 둔다고 최신을 따라가지 않는데 Kubernetes 는 해마다 마이너를 세 번 올린다. 1.31 은 2025-11-26 에 표준 지원이 끝나 **연장 지원 요금(클러스터당 월 ~$365 추가)이 이미 붙고 있고**, 최종 지원 종료(1.31 은 2026-11-26)가 지나면 **AWS 가 강제로 자동 업그레이드해 버린다** — 그 전에 우리 손으로, 단계마다 검증하며 올리는 것이 이 절이다.

**제약 3가지가 절차를 결정한다**

- 마이너는 **1단계씩만** 올릴 수 있다 → 1.31→1.34 는 apply 3번(1.32→1.33→1.34).
- **다운그레이드는 불가**하다 → 각 단계의 `terraform plan` 확인이 유일한 안전장치다.
- add-on(coredns 등)은 클러스터 버전과 **짝이 맞아야** 한다 → 단계마다 둘을 같이 올린다.

> 🔴 **pull 직후 함정 — 업그레이드를 시작하기 전이라도, 이 업데이트를 받았으면 tfvars 에 현재 버전을 pin 한다.** 이 업데이트로 terraform 기본값이 1.34 로 올라갔다. 클러스터가 1.31 인 채 tfvars 에 `eks_cluster_version` 이 없으면 **다른 목적의 apply 도** 1.31→1.34 점프를 시도하다 실패한다. pin 명령은 아래 (0) 다음의 「pin」 블록에 있다.

> ℹ️ **시작 전에 저장소부터 최신화한다** — [README §3 ①](../README.md#3-적용하기). 이 절의 절차와 terraform 변경(`eks_addon_versions` 변수 포함) 자체가 US-05 로 배포되므로, 옛 체크아웃에는 아래에서 쓰는 변수가 없다.

**진행 체크리스트** — 복사해 두고 하나씩 지우며 진행한다. 각 항목의 상세는 아래 (0)~(2).

- [ ] **(0) 사전 점검** — insights 에 `ERROR` 없음 · add-on 실측값 기록
- [ ] **pin** — tfvars 에 현재 버전·add-on 명시 → `terraform plan` = `No changes`
- [ ] **1.32** — tfvars → plan(4건만) → apply → 파드 재시작 → `get nodes` 확인
- [ ] **1.33** — tfvars → plan(4건만) → apply → 파드 재시작 → `get nodes` 확인
- [ ] **1.34** — tfvars → plan(4건만) → apply → 파드 재시작 → `get nodes` 확인
- [ ] **(2) 마무리** — smoke-test · `status.sh` US-05 OK · add-on pin 제거 후 plan = `No changes`

**(0) 사전 점검** — 읽기 전용이다.

▶ **실행** · 배포 EC2

```bash
# 현재 클러스터 버전과 건강 상태
aws eks describe-cluster --name llm-gateway-dev \
  --query 'cluster.[version,health.issues]' --output json
# AWS 의 업그레이드 사전 진단 (deprecated API 사용·버전 skew 등을 자동 점검)
aws eks list-insights --cluster-name llm-gateway-dev \
  --query 'insights[].[name,insightStatus.status]' --output table
# add-on 의 실제 버전 — terraform 코드의 값과 다를 수 있다
for a in coredns kube-proxy vpc-cni; do
  aws eks describe-addon --cluster-name llm-gateway-dev --addon-name $a \
    --query 'addon.[addonName,addonVersion]' --output text
done
```

`list-insights` 에 `ERROR` 가 있으면 그 항목부터 해결한다 — EKS 가 업그레이드를 거부할 수 있다. `PASSING`·`WARNING` 이면 진행.

**(pin) 현재 버전을 tfvars 에 고정** — 위 「pull 직후 함정」의 처방이다.

먼저 pin 이 이미 있는지 확인한다 — 같은 변수를 두 번 적으면 terraform 이 중복 정의 에러를 낸다:

```bash
grep -n "eks_cluster_version" \
  ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev/terraform.tfvars
```

이미 있으면 그 줄의 값을 고치고, 없으면 아래 블록을 파일 끝에 추가한다.

▶ **수정** · 배포 EC2 · `~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev/terraform.tfvars`

```hcl
eks_cluster_version = "1.31"   # ← (0) 에서 확인한 현재 클러스터 버전
eks_addon_versions = {         # ← 값은 (0) 의 describe-addon 실측 그대로
  coredns    = "<실측값>"
  kube_proxy = "<실측값>"
  vpc_cni    = "<실측값>"
}
```

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform plan   # 기대: No changes
```

> ℹ️ `Error: Required plugins are not installed` 가 나오면 `terraform init` 한 번 뒤 재시도한다. §3 ① 의 `reset --hard` 가 `.terraform.lock.hcl` 을 커밋본으로 되돌려 캐시된 프로바이더와 체크섬이 어긋난 것뿐이며, init 은 인프라를 건드리지 않는다.

`No changes` 면 pin 이 실제와 일치하는 것이다 — (1) 로 진행한다. 다른 diff 가 나오면 **멈춰서 분류한다**:

- **pin 오타** — 값이 (0) 실측과 다른 것. 고치고 plan 재실행.
- **무관한 드리프트** — 업그레이드와 같은 apply 에 싣지 않는다. 항목을 전부 읽어 **무해함을 확인했으면 전용 `terraform apply` 한 번으로 정리**하고, 다시 `terraform plan` = `No changes` 를 만든 뒤 (1) 로 간다. 판단이 안 서는 diff 는 적용하지 말고 원인부터 확인한다.

> 실제 사례(2026-08): state 가 과거 콘솔 수동 업그레이드(1.30→1.31)를 모르고 있어 `time_sleep` 교체 + DB 시크릿 사본([§8-N (2)](8-N-vpc-endpoint.md) 의 기지 드리프트) + ALB IRSA 재계산이 나왔다. 전용 apply 로 정리하니 IRSA 는 실변경 0 이었고, 직후 plan 은 `No changes`.

**(1) 단계 반복 — 1.32 → 1.33 → 1.34, 한 단계씩**

모든 단계가 같은 4수다: **tfvars 수정 → plan 확인 → apply → 파드 재시작**. 버전과 add-on 짝은 아래 표를 쓴다(2026-08 시점 각 버전의 기본 add-on. 시일이 많이 지났으면 `aws eks describe-addon-versions --kubernetes-version <버전> --addon-name <이름>` 으로 다시 조회).

| 단계 | `eks_cluster_version` | coredns | kube_proxy | vpc_cni |
|---|---|---|---|---|
| 1 | `"1.32"` | `v1.11.4-eksbuild.40` | `v1.32.13-eksbuild.21` | `v1.22.4-eksbuild.3` |
| 2 | `"1.33"` | `v1.12.4-eksbuild.18` | `v1.33.10-eksbuild.18` | `v1.22.4-eksbuild.3` |
| 3 | `"1.34"` | `v1.12.4-eksbuild.18` | `v1.34.6-eksbuild.18` | `v1.22.4-eksbuild.3` |

▶ **수정** · 배포 EC2 · `~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev/terraform.tfvars`

```hcl
eks_cluster_version = "1.32" # ← 단계마다 표의 다음 행으로
eks_addon_versions = {
  coredns    = "v1.11.4-eksbuild.40"
  kube_proxy = "v1.32.13-eksbuild.21"
  vpc_cni    = "v1.22.4-eksbuild.3"
}
```

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform plan -out=tfplan
```

> 🔴 **plan 에서 멈춰 읽는다.** 바뀌는 것이 **클러스터 버전 1건 + add-on 3건**뿐이어야 한다. 다른 리소스가 섞여 있으면 apply 하지 않는다 — 오래 운영한 배포일수록 무관한 드리프트가 쌓여 있고([§8-N (2)](8-N-vpc-endpoint.md) 에서 실제로 겪었다), 그것을 업그레이드와 한 apply 에 실으면 평범한 변경이 장애가 된다.

```bash
terraform apply tfplan   # 컨트롤 플레인 ~10분 + add-on 수 분. 서비스 무중단
```

apply 가 끝나도 새 버전이 된 것은 **컨트롤 플레인뿐**이다. Fargate 는 상주 노드가 없고 **파드가 곧 노드**라서, 파드를 새로 띄워야 데이터 플레인이 새 버전 kubelet 을 받는다. 대상은 **모든 네임스페이스**다 — 애플리케이션만 돌리면 ALB controller·external-secrets·관측 스택이 옛 kubelet 에 남는다:

```bash
kubectl rollout restart deployment -n llm-gateway
kubectl rollout restart deployment -n kube-system
kubectl rollout restart deployment -n external-secrets
kubectl rollout restart deployment -n observability
kubectl rollout restart statefulset -n observability   # prometheus
kubectl rollout status deployment -n llm-gateway --timeout=10m
kubectl get nodes   # 2~3분 뒤: 모든 노드 VERSION 이 방금 올린 버전인지
```

> ℹ️ 업그레이드 **직후 첫 재시작에선 일부 파드가 이전 버전 kubelet 을 받을 수 있다** — Fargate 데이터 플레인에 새 버전이 전파되는 데 몇 분 걸린다. `get nodes` 에 이전 버전 노드가 남아 있으면, 몇 분 뒤 그 노드에 있는 deployment 만 한 번 더 restart 한다.

> ℹ️ `external-secrets-cert-controller` 가 재시작 후 `0/1` 로 남는 것은 **무해**하다 — 설치 스크립트가 지운 웹훅 설정(VWC) 부재로 readiness 만 실패할 뿐, 시크릿 동기화에는 영향이 없다. `kubectl scale deploy external-secrets-cert-controller -n external-secrets --replicas=0` 뒤 `--replicas=1` 로 정리한다.

여기까지 확인한 뒤 다음 단계로 넘어간다.

**(2) 마무리 — 1.34 도달 후**

```bash
cd ~/awsome-ai-gateway && ./deployment/scripts/smoke-test.sh
bash docs/us-llm-gateway/update-scripts/status.sh   # US-05 가 OK 인지
```

tfvars 의 `eks_addon_versions` 블록은 지워도 된다 — 이제 기본값(1.34 호환)과 같다. `eks_cluster_version = "1.34"` 는 **남겨 둔다**. 다음에 기본값이 또 올라갔을 때, 위 「pull 직후 함정」이 재발하지 않는 방어가 된다.

```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform plan   # 기대: No changes — 정리 후에도 선언과 실제가 일치
```

**함정 3가지**

- **apply 3번을 몰아 하고 재시작을 한 번만 하면 안 된다.** 파드(kubelet)가 1.31 인 채 컨트롤 플레인만 1.34 가 되면 허용 skew(3 마이너)의 경계에 걸리고, 그 사이 파드가 어떤 이유로든 재기동되면 중간 버전 노드가 섞인다. 단계마다 재시작이 정석이다.
- **재시작 범위는 전 네임스페이스다.** 애플리케이션 ns 만 돌리면 coredns·ALB controller·external-secrets·관측 스택이 옛 버전 노드에 남는다 — 이 배포에서 실제로 **22일 묵은 1.30 kubelet** 이 발견됐다(과거 수동 업그레이드 때 재시작 누락). `kubectl get deploy -A` 로 빠진 곳이 없는지 본다.
- **버전을 두 단계 이상 적으면 plan 이 아니라 apply 에서 터진다.** plan 은 1.31→1.34 를 그대로 보여주며 통과하고, apply 시점에 EKS API 가 거부한다. plan 의 버전 diff 가 정확히 +1 인지 눈으로 확인한다.
