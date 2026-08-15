# 8-Z. 토큰 TTL 조절

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-Z**

인증 토큰 수명(기본값)과 **바꾸는 이유**는 [client-setup-explained.md 의 "만료 조건"](../client-setup-explained.md#언제-다시-인증해야-하나-만료-조건) 참고. 여기서는 **어떻게 바꾸나**만 다룬다. 둘은 위치·반영 방식이 다르다.


| 무엇                          | 기본      | 어디서                 | 반영            |
| --------------------------- | ------- | ------------------- | ------------- |
| **refresh_token** (재로그인 주기) | **7일**  | Cognito (terraform) | 새로 로그인하는 사람부터 |
| access/id_token             | 1시간     | Cognito (terraform) | 위와 동일         |
| **VK** (게이트웨이 열쇠)           | **1시간** | admin-api env       | 다음 VK 발급부터    |


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

## ② VK TTL (게이트웨이 열쇠, 1시간)

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
