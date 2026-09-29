# 8-Q. Bedrock Marketplace 구독 — "AccessDeniedException … aws-marketplace:Subscribe"

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-Q** · 소요 ~10분(권한 부여 + 구독 + 전파 대기 2분)

> **한 줄**: Anthropic 신형 모델은 Bedrock **Model access 신청이 아니라 AWS Marketplace 구독**이다.
> 모델을 게이트웨이에 등록(§8-M)해도 **계정에 구독이 없으면** 호출이 AccessDenied로 떨어진다.
> 2026-09-21 dev 배포 계정에서 Claude Opus 5로 실측·재현한 절차다.

---

## 증상

클라이언트(Claude Code 등) 또는 Bedrock 콘솔 playground에서:

```text
AccessDeniedException — Model access is denied due to IAM user or service role is not
authorized to perform the required AWS Marketplace actions
(aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe) to enable access to this
model. … Your AWS Marketplace subscription for this model cannot be completed at this time.
```

특징:

- **다른 모델은 잘 되는데 새 모델만** 이 에러 — 게이트웨이/네트워크 문제가 아니다.
- Bedrock 콘솔 **Model catalog 페이지에 구독 버튼이 안 보인다** — 정상이다. 버튼이 없는 게 아니라
  콘솔 IAM 주체에 마켓플레이스 권한이 없어 숨겨져 있거나, 아래 경로(②)로 가야 한다.

## 원인

Anthropic 모델은 **계정별 Marketplace 구독**이 필요하다(예전 Sonnet/Haiku 세대의 "Model access
신청서" 방식이 아니다). Bedrock은 **구독이 없는 모델의 첫 invoke에서 자동 구독을 시도**하는데,
호출 IAM 주체가 `aws-marketplace:ViewSubscriptions`/`aws-marketplace:Subscribe`를 못 가지면
위 에러로 실패한다.

실측(dev, 2026-09-21):

```bash
# 같은 계정·같은 역할로 — 모델별로 결과가 갈림
aws bedrock-runtime invoke-model --region ap-south-1 \
  --model-id global.anthropic.claude-sonnet-5 --body fileb://body.json out.json
# → 200 OK (구독 있음)

aws bedrock-runtime invoke-model --region ap-south-1 \
  --model-id global.anthropic.claude-opus-5 --body fileb://body.json out.json
# → AccessDeniedException (Marketplace 구독 없음)
```

## 어느 계정에서 구독해야 하나

**Bedrock을 실제로 호출하는 계정** — `model.routing_profiles.account_role_arn`이 가리키는 곳:

- 이 배포(단일 계정): `account_role_arn = NULL` → **배포를 진행한 계정 자체**
- 멀티계정(§8-X): **cross-account 대상 계정**에서 구독한다

```sql
SELECT client, backend, default_model, account_role_arn
  FROM model.routing_profiles ORDER BY client;
-- account_role_arn 이 NULL 이면 in-account, ARN 이 있으면 그 계정에서 구독
```

## 해결 절차

**① 콘솔 IAM 주체에 마켓플레이스 권한 부여** — 관리형 정책 `AWSMarketplaceManageSubscriptions`
(또는 이번 실측에서 쓴 `AWSMarketplaceFullAccess`). 최소 액션:
`aws-marketplace:ViewSubscriptions` + `aws-marketplace:Subscribe`.

**② 구독** — 둘 중 하나:

- **AWS Marketplace 콘솔** → "Claude Opus 5"(Sold by Anthropic) → **Subscribe** ← 가장 확실
- **Bedrock 콘솔 → playground에서 한 번 호출** — 권한 있는 주체의 첫 invoke가 자동 구독을
  완료한다(위 에러가 "권한이 있었더라면" 이 경로였다)

권한 반영 직후라도 콘솔 세션이 캐시될 수 있으니 **로그아웃/로그인 후** 시도한다.

**③ ~2분 대기 후 검증** — 구독 전파까지 잠시 걸린다. 배포 EC2(또는 invoke 권한 있는 어떤
자격)에서 위 CLI 재현 명령을 다시 돌려 200이면 끝.

```bash
echo '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,
 "messages":[{"role":"user","content":"hi"}]}' > /tmp/body.json
aws bedrock-runtime invoke-model --region ap-south-1 \
  --model-id global.anthropic.claude-opus-5 \
  --body fileb:///tmp/body.json /tmp/out.json && cat /tmp/out.json | head -3
```

그다음 게이트웨이 경유로 재시도 — alias resolve는 이미 돼 있으므로 바로 동작한다.

## 주의

- **게이트웨이 역할(IRSA)에 `aws-marketplace:*`를 주지 않는다.** 주면 첫 호출이 자동 구독을
  완료해버려 묵시적 빌링 승인이 된다. 구독은 콘솔에서 **수동·1회**로 하는 게 정석이다.
- **Terraform으로 자동화 불가** — Marketplace 구독은 관리되는 리소스가 아니다. prod 신설
  (§8-P) 때 **콘솔 수동 사전 작업**으로 이 절을 따라야 한다 — prod 계정에서 모델마다 반복.
- **멀티계정이면 대상 계정마다** 구독이 필요하다(구독은 계정 단위, 리전 공통).
- `usage_logs`의 `model_alias` 기록과 무관하게 이 에러는 과금이 발생하지 않는다 — Bedrock이
  호출을 거절한 것이므로 게이트웨이 과금 경로에도 도달하지 않는다.

## 관련 — `[1m]` alias (Claude Code "Default (recommended)")

Claude Code의 모델 피커에서 Default/1M 항목은 `claude-opus-5[1m]` 같은 **`[1m]` 접미 와이어
이름**을 보낸다. 게이트웨이에서는 별도 alias로 등록한다(선례: `claude-sonnet-4-6[1m]` →
`global.anthropic.claude-sonnet-4-6`). 단, Bedrock은 `anthropic_beta` 헤더를 받지 않아
게이트웨이가 제거하므로 **현재 [1m] 요청은 일반 모델 호출과 동일**하다 — 진짜 1M 컨텍스트는
Bedrock 측 지원 방식이 확인되면 별도 작업이다.

## 참고 — 이 에러와 무관한 비슷한 403

| 에러 | 원인 |
|---|---|
| `AccessDeniedException … aws-marketplace` | 이 절 — Marketplace 구독 없음 |
| `not_found_error` (HTTP 404, 게이트웨이 응답) | alias 미등록 — §8-M 또는 /models 에서 등록. invoke-backend는 `default_model` 폴백(2026-09 추가)으로 대체 가능 |
| `ValidationException … model identifier` | `provider_model_id` 오타 — `global.`/`us.` 접두사 확인(§8-M ⓒ) |
