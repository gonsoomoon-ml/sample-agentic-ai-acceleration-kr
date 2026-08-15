# 8-N. Bedrock 을 NAT 대신 VPC Endpoint(PrivateLink)로

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-N**

> 📒 **`US-04` · 등급 필수(컴플라이언스)** — [README.md 「최신 업데이트」](../README.md#2-최신-업데이트). 적용 여부는 `update-scripts/status.sh` 로 확인한다.

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


> 🔴 **NAT 는 없어지지 않는다.** 위 표의 3행 때문에 NAT 게이트웨이는 그대로 필요하다. 이 변경으로 얻는 것은 **Bedrock 트래픽이 퍼블릭 인터넷을 지나지 않는다**는 점이고, 이것이 **컴플라이언스 요건**이므로 **필수** 업데이트로 분류한다 — [README.md 「최신 업데이트」](../README.md#2-최신-업데이트) `US-04`.

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
