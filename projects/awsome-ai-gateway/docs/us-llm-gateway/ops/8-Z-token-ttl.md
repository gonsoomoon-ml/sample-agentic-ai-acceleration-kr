# 8-Z. 토큰 TTL 조절

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-Z**

인증 토큰 수명(기본값)과 **바꾸는 이유**는 [client-setup-explained.md 의 "만료 조건"](../client-setup-explained.md#언제-다시-인증해야-하나-만료-조건) 참고. 여기서는 **어떻게 바꾸나**만 다룬다. 둘은 위치·반영 방식이 다르다.


| 무엇                          | 기본      | 어디서                 | 반영            |
| --------------------------- | ------- | ------------------- | ------------- |
| **refresh_token** (재로그인 주기) | **7일**  | Cognito (terraform) | 새로 로그인하는 사람부터 |
| access/id_token             | 1시간     | Cognito (terraform) | 위와 동일         |
| **VK** (게이트웨이 열쇠)           | **1시간** | values `adminApi.oidc.vkTtlHours` | 다음 VK 발급부터    |


## ① Cognito 토큰 TTL (refresh 7일 · access/id 1시간)

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

## ② VK TTL (게이트웨이 열쇠, 기본 1시간) — 따라 하기

admin-api 환경변수 `OIDC_VK_TTL_HOURS`(`admin-api/src/app/core/config.py:124` 기본 1)다. chart 가 이 env 를 values `adminApi.oidc.vkTtlHours` 에서 렌더하므로(`templates/_helpers.tpl` `oidcEnv`) 그 키 한 줄을 고치고 재배포한다. 새로 발급되는 VK 부터 적용, admin-api 파드만 롤링(추론 무중단), 약 10분.

```
values-eks-fargate-dev.yaml (배포 EC2 유일본)   adminApi.oidc.vkTtlHours: 1 -> 24
        |  install-eks.sh dev  (helm upgrade --atomic, REVISION +1)
        v
admin-api env OIDC_VK_TTL_HOURS=24  ->  새 VK expires_at = now+24h (Redis TTL 동일)
```

> ⚠️ `adminApi.env: OIDC_VK_TTL_HOURS` 로 넣지 말 것 — 템플릿이 `oidcEnv`(1) 뒤에 `adminApi.env` 를 렌더해 같은 이름의 env 가 **2개** 생긴다. 마지막 값이 이겨 동작은 하지만 정식 키가 아니다.

▶ **실행** · 배포 EC2 — 위에서부터 그대로. 📋 = 기대 출력.

**1. 현재 상태**

```bash
cd ~/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
grep -n vkTtlHours $V
kubectl -n llm-gateway set env deploy --all --list | grep OIDC_VK_TTL
helm -n llm-gateway list
```

📋 `vkTtlHours: 1` · `OIDC_VK_TTL_HOURS=1` 1줄(admin-api 만) · REVISION N 을 적어 둔다.

**2. 백업** — 롤백 좌표 = 이 .bak + REVISION N

```bash
cp -n $V $V.bak-$(date +%Y%m%d)
```

**3. 변경 (한 줄)**

```bash
sed -i 's/^    vkTtlHours: 1$/    vkTtlHours: 24/' $V
grep -n vkTtlHours $V
```

📋 `vkTtlHours: 24`

**4. 배포 (3–5분)**

```bash
./deployment/scripts/install-eks.sh dev
```

📋 끝에 `deployed`. 실패하면 `--atomic` 이 REVISION N 으로 자동 복귀. "계속 진행 (y)/(N)" 이 뜨면 Secrets Manager 시크릿 문제 → N 으로 중단.

**5. 검증**

```bash
helm -n llm-gateway list
kubectl -n llm-gateway set env deploy --all --list | grep OIDC_VK_TTL
kubectl -n llm-gateway get pods | grep admin-api
```

📋 REVISION N+1 · `OIDC_VK_TTL_HOURS=24` **정확히 1줄** · admin-api Running.

클라이언트(아무 PC): `rm ~/.gateway-cli/vk-cache.json` → Claude Code 에 질문 1회 →

```bash
jq '(.expires_at-now)/3600' ~/.gateway-cli/vk-cache.json
```

📋 23.9 근처. 기존 사용자는 손 안 대도 된다 — helper 가 만료 5분 전 재발급하므로 1시간 안에 전원 24h.

**6. 롤백 (필요 시)**

```bash
helm -n llm-gateway rollback llm-gateway N
cp $V.bak-<날짜> $V
```

📋 .bak 복원을 빼먹으면 다음 배포에서 24 가 다시 올라간다.

> 짧을수록 유출 내성 ↑ · admin-api 재발급 부하 ↑. 길수록 반대. **24h 로 늘리면 달라지는 것**: Cognito 그룹(팀) 변경 반영 최대 ~25h · 재발급 뒤 옛 VK 는 Redis TTL 동안(≤24h) 통과. 그대로인 것: 관리자 폐기 즉시 · 비활성화 5분 내 차단 · 재로그인 주기(refresh 7일).
