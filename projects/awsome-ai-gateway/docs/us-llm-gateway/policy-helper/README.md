# policyHelper — 사용자별 Claude Code 정책 (PoC)

Claude Code 가 시작할 때 `policy-helper.exe` 가 사용자의 VK 로 게이트웨이에 정책을 물어, 관리형 설정을
사용자·팀별로 계산하는 구조입니다. 지금은 모델 선택 목록을 대상으로 PoC 를 했습니다.

- 상태: **PoC 1~3단계 완료**(2026-09-26, US dev) · 4단계(설치기 경로) 남음 · **지금은 배포되어 있지 않음**(아래 「코드와 배포 위치」)

```
Claude Code --(start, every 5 min)--> policy-helper.exe --(VK)--> gateway GET /v1/policy
            <-- baseline + server fragment ----------------------   (per-user model list)
                if the gateway is unreachable: last cached policy -> else baseline (never refuses to start)
```

## 문서 — 읽는 순서

- [01-policy-helper-overview.md](01-policy-helper-overview.md) — 개념 입문(고객 전달용). policyHelper 가 무엇인지, 실행 흐름, apiKeyHelper 와의
  관계, Claude Desktop(Cowork·Chat)에 미치는 범위
- [02-policy-helper-gateway-design.md](02-policy-helper-gateway-design.md) — 게이트웨이에 붙이는 설계(고객 전달용,
  PoC 기록 없음). 설계 결정, 구성요소, 실패 상황별 동작, 남은 질문. PoC 결과는 이 README 와 단계별 가이드에
- [poc-step1-windows.md](poc-step1-windows.md) — 1단계: 기본값만 내보내는 helper 를 빌드·설치하고 연결 (Windows)
- [poc-step2-deploy-verify.md](poc-step2-deploy-verify.md) — 2단계: 게이트웨이 `/v1/policy` 를 US dev 에 배포하고
  팀 제한이 `/model` 에 반영되는지 확인
- [poc-step3-fallback-windows.md](poc-step3-fallback-windows.md) — 3단계: 게이트웨이에 닿지 않을 때 캐시·기본값으로
  시작하는지 확인
- `diagram-src.py` · `diagram-src-implementation.py` — 문서 그림 생성기(그림은 손으로 고치지 않고 다시 생성)

## PoC 결과 요약

- 관리 화면에서 팀 허용 모델을 바꾸면 곧바로 PC 의 `/model` 목록에 반영되고, 목록 밖 모델은 선택도 직접
  전환도 막힙니다.
- 게이트웨이에 닿지 않아도 Claude Code 는 시작합니다. 캐시가 있으면 마지막 정책으로, 없으면 기본값으로 뜹니다.
- 정책 조회는 사용량 기록(`usage_logs`)에 남지 않습니다.

## 코드와 배포 위치

- 코드: 이 repo 의 브랜치 `feat/policy-helper-poc` (`us/deploy-fixes` 에는 없음)
  - helper: `projects/awsome-ai-gateway/policy-helper/`
  - 게이트웨이: `projects/awsome-ai-gateway/gateway-proxy/` 의 `GET /v1/policy`
- 배포: 지금은 어디에도 배포되어 있지 않습니다. PoC 때 US dev 에 올린 gateway-proxy
  `1.0.83-policyhelper` 는 2026-10-06 다음 업데이트(`1.0.84-safeguards`)로 바뀌면서 `/v1/policy` 가 빠졌습니다.
- 다시 쓸 때: 옛 이미지로 되돌리지 말고, 브랜치를 최신 `us/deploy-fixes` 위로 리베이스한 뒤 새 태그로 빌드합니다.
- dev Windows: `C:\Program Files\PolicyHelper\`, 관리형 설정에 `policyHelper` 블록

## 남은 일

- 4단계 — 설치기의 `site-extra.json` 에 포인터를 넣어 재빌드했을 때 관리형 설정에 병합되는지
- 운영 전 — admin-api 가 저장 시점에 미리 계산하는 구조로 전환, 캐시 서명, 사내 CA(TLS 가로채기) 지원
