# 8-M. 모델 추가와 교체

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-M**

> 📒 모델 구성이 바뀔 때마다 쓰는 **범용 절차**다. 어떤 모델을 넣고 무엇을 내리는지는
> [README.md 「최신 업데이트」](../README.md#2-최신-업데이트)의 `US-NN` 행이 정하고(예: `US-13`
> Opus 5.5 추가 · `US-16` Sonnet 5.5 로 교체), 그 값은 아래 「현재 구성」 한 곳에 모여 있다.
> **Cowork 와 무관하며 Claude Code 만 쓰는 배포에도 해당**한다.

**어디부터 읽나** — 모두 「0」 을 먼저 하고, 「작업 고르기」 에서 고른 절을 위에서 아래로 따라 한다.

---

## 0. 할 일이 있는지 먼저 본다

설치한 시점에 따라 이미 최신 구성일 수 있다. 신규 설치는 install-guide §4-2 가 그때의 최신
모델을 처음부터 넣으므로 설치 직후에는 대개 할 일이 없다. "새로 설치했는가" 가 아니라
**지금 상태**로 판단한다.

**0-1) 저장소를 최신으로 맞춘다** — 판정 스크립트도 새 모델에 맞춰 갱신되므로 먼저 받는다.
이 브랜치는 리베이스되므로 `git pull` 이 아니라 `reset --hard` 이고, values 파일은 이 EC2 에만
있는 유일본이라 백업·복원한다(빠뜨리면 다음 배포에서 ALB 허용목록이 빠진다). prod 는 `V` 의
파일명이 `values-eks-fargate-prod.yaml` 이다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

**0-2) `config.env` 를 준비한다** — 스크립트는 전부 이 파일을 읽는다. `.gitignore` 대상이라
저장소를 갱신해도 남는다. 처음이면 만들고 `AWS_ACCOUNT_ID` 를 채운다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
[ -f config.env ] || cp config.env.example config.env
grep -E '^(AWS_ACCOUNT_ID|MODEL_ALIAS)=' config.env
```

**0-3) 상태를 본다**

▶ **실행** · 배포 EC2

```bash
bash status.sh
```

모델 줄(`US-13` · `US-16` …)을 본다.

- **전부 OK** — 할 일이 없다. 여기서 끝난다.
- **"미적용"** — 그 `US-NN` 행이 가리키는 작업을 한다(아래 「작업 고르기」).
- **"부분 적용"** — 새 모델 등록은 끝났고 옛 모델이 남아 있다. 「A2 첫 호출 확인」 부터 이어서 한다.
- **"단가 없음"** — 등록은 됐는데 비용이 $0 으로 쌓이는 중이다. 가장 먼저 고친다:
  `bash 08-set-model-pricing.sh --alias <별칭> --apply`

EC2 에 들어갈 수 없으면 관리 화면 **모델** 에서 ACTIVE 목록이 「현재 구성」 의 모델과 같은지,
각 모델의 단가 5칸이 0 이 아닌지 본다.

---

## 현재 구성 (2026-10 · `US-16` 기준)

새 모델이 나오면 이 절부터 고친다(아래 「부록」 체크리스트). 단가의 정본은
`update-scripts/pricing.tsv` 이고 여기는 사람이 읽기 위한 사본이다 — 둘이 다르면 `pricing.tsv`
가 맞다.

- **모델 목록** — Opus 5.5 · Sonnet 5.5 · Haiku 4.5
- **기본 모델** — `claude-sonnet-5-5`
- **폴백 규칙** — `claude-opus-5-5 → claude-sonnet-5-5` 하나
- **내린 모델** — Opus 5 · Sonnet 5 · Opus 4.8 (`US-16`). INACTIVE 라 과거 사용량·비용 기록은 남는다
- **클라이언트 최소 버전** — Opus 5.5 는 Claude Code 2.1.280 이상 · Sonnet 5.5 는 2.1.288 에서 확인(그보다 낮은 버전은 미확인)

### 모델별 값

단가는 1K 토큰당 USD, `us.` 지리 엔드포인트 기준(글로벌 정가 × 1.1)이다. 캐시는 5분 쓰기 /
1시간 쓰기 / 읽기 순서다. 스크립트(「A1-1」)는 같은 값을 `config.env.example` 의 블록에서 읽고,
관리 화면(「A1-2」)은 아래 값을 그대로 옮겨 적는다.

- **Sonnet 5.5** — 별칭 `claude-sonnet-5-5` · 모델 ID `us.anthropic.claude-sonnet-5-5` · 표시명 `Claude Sonnet 5.5`
  - 입력 `0.002200` · 출력 `0.011000` · 캐시 `0.002750` / `0.004400` / `0.000220`
- **Opus 5.5** — 별칭 `claude-opus-5-5` · 모델 ID `us.anthropic.claude-opus-5-5` · 표시명 `Claude Opus 5.5`
  - 입력 `0.004400` · 출력 `0.022000` · 캐시 `0.005500` / `0.008800` / `0.000220`
  - ⚠️ 캐시 읽기가 입력의 **0.05배**다(다른 모델은 0.1배).
- **Haiku 4.5** — 별칭 `claude-haiku-4-5-20251001` · 모델 ID `us.anthropic.claude-haiku-4-5-20251001-v1:0`
  - 입력 `0.001100` · 출력 `0.005500` · 캐시 `0.001375` / `0.002200` / `0.000110`
  - 기본 시드에 있는 모델이라 따로 등록하지 않는다.

---

## 작업 고르기

| 하려는 일 | 읽을 절 | 예 |
|---|---|---|
| 새 모델을 목록에 **추가만** 한다(기본 모델은 그대로) | 「A」 | `US-13` Opus 5.5 |
| **기본 모델을 새 모델로 바꾸고** 옛 모델을 정리한다 | 「A」 → 「B」 → 「C」 | `US-16` Sonnet 5.5 |
| 안 쓰는 모델만 **내린다** | 「C」 | Opus 4.8 정리 |
| **단가만** 고친다 | [8-R](8-R-pricing.md) | `US-11` |

교체(「A」 → 「B」 → 「C」)의 흐름은 다음과 같다. **④ 는 반드시 ③ 다음이다** — 옛 모델을 먼저
끄면 기본 모델이 옛 모델로 박힌 직원 PC 가 바로 실패한다.

📋 **참고** — 교체 흐름

```
① 등록 → ② 첫 호출 → ③ 기본 모델 전환 → ④ 옛 모델 내리기 → ⑤ 확인
 A1       A2          B1 · B2              C1 · C2 · C3        C4
```

작업마다 배포 EC2 · 관리 화면 · 직원 PC 를 오간다. 명령 앞의 `▶ 실행 · …` 표시가 어디서
실행하는지 알려 준다. dev 와 prod 가 있으면 **dev 에서 끝까지 한 뒤** prod 에서 반복한다.

---

## A. 모델 추가

새 모델을 게이트웨이 목록에 넣는다. 기본 모델은 바꾸지 않는다. 넣을 값은 「현재 구성 › 모델별
값」 에 있다.

### A1. 등록 — 둘 중 하나

- **A1-1 스크립트 (권장)** — 배포 EC2 에 들어갈 수 있는 운영자. 미리보기·사전 점검이 있고,
  dev·prod 에 같은 값을 그대로 반복할 수 있다.
- **A1-2 관리 화면** — EC2 없이 관리 화면만 쓸 수 있을 때. 저장 즉시 반영되지만 실수를 막는
  장치가 없으니 체크리스트를 지킨다.

#### A1-1. 스크립트 — 배포 EC2

「0」 에서 저장소 갱신과 `config.env` 준비를 마쳤다고 본다.

**1) `config.env` 의 모델 값을 바꾼다.** `config.env.example` 에서 넣을 모델의 블록을 띄워 두고,
`config.env` 의 `MODEL_ALIAS` · `MODEL_PROVIDER_ID` · `MODEL_DISPLAY_NAME` · `MODEL_DESCRIPTION` ·
단가 5줄(`MODEL_PRICE_*`) · `MODEL_PRICE_ASOF` 를 그 값으로 고친다(블록 앞의 `#` 는 지운다).
예시 파일에 블록이 없는 모델이면 저장소 관리자에게 「부록」 반영을 먼저 요청한다.

▶ **실행** · 배포 EC2 — 예: Sonnet 5.5

```bash
grep -A12 'Sonnet 5.5' config.env.example
vi config.env
```

**2) 사전 점검** — 읽기 전용. `team_allowed_models has 0 rows` 가 나와야 한다. 행이 있으면 그
팀은 허용 목록에 있는 모델만 쓸 수 있으니, 3) 에서 `--team-id <uuid>` 를 붙여 그 팀 행도 함께
넣는다.

▶ **실행** · 배포 EC2

```bash
bash 00-preflight-check.sh
```

**3) 미리보기 → 적용** — 스크립트 이름에 `opus5` 가 붙어 있지만 `config.env` 의 어떤 모델이든
등록한다. 미리보기 출력에서 별칭·모델 ID·단가를 확인한 뒤 적용한다.

▶ **실행** · 배포 EC2 — 미리보기

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 02-add-opus5-model.sh
```

▶ **실행** · 배포 EC2 — 적용

```bash
bash 02-add-opus5-model.sh --apply
```

**4) 5분 기다린 뒤 확인한다** — 스크립트는 DB 를 직접 고치므로 모델 목록 캐시(300초, 외부
ElastiCache)가 그대로 남는다. 파드를 재시작해도 소용없다.

▶ **실행** · 배포 EC2

```bash
bash 04-verify.sh
```

#### A1-2. 관리 화면

**모델 관리 → + 모델 추가** 에서 아래처럼 넣는다. 값은 「현재 구성 › 모델별 값」 에서 옮긴다.

- **별칭** — 예: `claude-sonnet-5-5`
- **Provider** — `BEDROCK`
- **Model ID** — `us.` 로 시작하는 값(예: `us.anthropic.claude-sonnet-5-5`). 입력란의 예시처럼
  접두사 없이 넣으면 호출이 실패할 수 있다.
- **Endpoint URL** — 비워 둔다.
- **표시 이름 · 설명** — 예: `Claude Sonnet 5.5` · `Claude Sonnet 5.5 (US Geo)`
- **단가 5칸** — 입력 · 출력 · 캐시 생성 5min · 캐시 생성 1h · 캐시 읽기를 **모두** 채운다. 캐시
  3칸은 기본값이 `0` 이라, 비워 두면 에러 없이 캐시 비용이 $0 으로 기록된다.

저장하면 바로 목록에 반영된다(기다릴 필요가 없다). 그리고 두 가지를 지킨다.

- **「AWS 단가 동기화」 버튼은 누르지 않는다** — AWS Price List 에 신모델이 없고, 있어도 `us.`
  지리 단가(× 1.1)와 다르다.
- **팀별 허용 모델을 쓰는 팀이 있으면** **사용자/팀** 화면에서 그 팀의 허용 모델에 새 모델을
  더한다. 안 하면 그 팀은 새 모델을 부를 때 400 이 난다.

⚠️ 나중에 단가를 관리 화면에서 고쳤으면 `pricing.tsv` 도 같이 고친다. 그러지 않으면 다음
`08-set-model-pricing.sh` 실행이 화면에서 고친 값을 표의 값으로 되돌린다.

### A2. 첫 호출 확인 — 사용자 PC 1대

모든 PC 에 알리기 전에 한 대로 시험한다. 새 모델은 Claude Code 최소 버전을 요구할 수 있다.

▶ **실행** · 사용자 PC — 예: Sonnet 5.5

```bash
claude update
claude --model claude-sonnet-5-5 -p "hi"
```

- **답이 온다** — 다음으로 간다.
- **400 `version … or newer is required`** — 그 버전 이상으로 올리고 다시 한다. 그 버전을
  「현재 구성」 의 최소 버전에 적는다. Cowork 는 앱 안의 Claude Code 를 쓰므로 새 offline
  `.msix` 로 앱을 올린다.
- **모델을 찾지 못한다는 오류** — 스크립트 등록 뒤 5분이 안 지났거나 등록이 안 된 것이다.
  「A1」 의 확인으로 돌아간다.
- **`403` 또는 `AccessDenied`** — 계정에서 모델이 안 켜졌거나 IAM 이 그 모델을 허용하지 않는다.
  저장소 관리자에게 알린다(「부록」 1) · 3) 의 IAM 확인).
- **로그인(`gateway-cli login`)이 HTTP 500** — 관리 API 가 끊긴 DB 연결을 꺼내 쓴 일시 오류다.
  한두 번 다시 시도하면 된다.

### A3. 클라이언트에 보이게 하기

- **Claude Code** — 게이트웨이가 내려주는 목록을 따르므로 할 일이 없다(관리 화면 등록은 즉시,
  스크립트 등록은 5분 뒤 보인다). 사용자는 `claude update` 만 하면 된다. 설치 때
  `--available-models` 로 목록을 PC 에 고정한 곳만 `gateway-cli setup` 을 다시 돌린다.
- **Cowork** — PC 마다 모델 목록을 다시 써야 선택기에 보인다. 레지스트리를 직접 고치지 말고
  설치기 CLI 를 쓴다. `$m` 에는 「현재 구성」 의 모델 목록을, `--model` 에는 「현재 구성」 의 기본
  모델을 넣는다(추가만 할 때는 기본 모델이 그대로다). 앱은 자동으로 재시작된다.

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell (정책이 HKLM 이면 관리자 권한이 필요하다)

```powershell
$m = "claude-sonnet-5-5,claude-opus-5-5,claude-haiku-4-5-20251001"
gateway-cli-cowork setup --model claude-sonnet-5-5 --available-models $m
```

확인은 `gateway-cli-cowork verify` 와 Cowork 의 모델 선택기다. Mac 은 설치 때 만든
`.mobileconfig` 의 `inferenceModels` 를 같은 목록으로 고쳐 다시 설치한다(같은 프로파일이
교체된다). GPO 로 관리하는 조직은 같은 `inferenceModels` 값(JSON 배열 문자열)을 정책으로
배포하고 앱을 재시작한다.

Cowork 가 `Credential helper exited with code 4` · `Session expired — re-login required. Run:
gateway-cli login` 을 내면 Cowork 쪽 로그인이 만료된 것이다. 안내 문구와 달리
**`gateway-cli-cowork login`** 을 실행한다 — Cowork 는 Claude Code(`gateway-cli`)와 토큰을 따로
저장한다(`%LOCALAPPDATA%\gateway-cli-cowork`). 로그인한 뒤 Claude Desktop 을 껐다 켠다.

### A4. (선택) 폴백 규칙

장애(5xx)나 예산 초과 때 다른 모델로 내려가게 하려면 관리 화면 **예산 관리 → 자동
다운그레이드** 에서 규칙을 더한다. 화면은 목록 전체를 저장하므로 **기존 규칙을 지우지 말고**
새 행을 더해 저장한다. 지금 규칙은 「현재 구성」 에 있다.

규칙은 gateway-proxy 가 기동할 때 읽는다(활성 행 전부로 전역 체인을 만든다). 그래서 저장한 뒤
재시작해야 적용된다. 확인은 기동 로그의 `fallback_chain_loaded entries=N` 이다. EC2 에 들어갈
수 없으면 운영자에게 재시작을 요청한다.

▶ **실행** · 배포 EC2

```bash
kubectl -n llm-gateway rollout restart deploy/llm-gateway-gateway-proxy
```

⚠️ web search 가 켜진 앱은 이 fork 의 결함으로 폴백이 걸리지 않는다. 검증은 웹서치를 끈 앱으로
한다.

### A5. 단가 검산 — 하루 뒤

하루 뒤 Cost Explorer 의 실제 청구 단가와 맞춰 본다. 어긋나면 `pricing.tsv` 를 고치고
`08-set-model-pricing.sh` 로 갱신한다(절차는 [8-R](8-R-pricing.md)).

여기까지가 「추가」 다. 기본 모델까지 바꾸려면 「B」 로 이어간다.

---

## B. 기본 모델 교체

「A」 로 새 모델을 등록하고 첫 호출까지 확인한 뒤 이어서 한다. 직원 PC 의 기본 모델과 폴백
규칙을 새 모델로 바꾼다. **옛 모델은 아직 끄지 않는다** — 끄는 것은 이 절을 끝낸 다음
「C」 에서 한다. 명령에 들어가는 모델 값은 「현재 구성」 의 기본 모델과 모델 목록이다(예시는
`US-16`: Sonnet 5 → Sonnet 5.5).

### B1. 기본 모델이 고정된 PC 를 바꾼다

PC 에 기본 모델을 고정하는 것은 **Windows 설치 파일로 깐 Claude Code** 뿐이다(`setup --model` 이
관리형 설정의 `model` 에 쓴다).

▶ **실행** · 사용자 PC — 🔴 관리자 PowerShell

```powershell
gateway-cli setup --model claude-sonnet-5-5
```

⚠️ 같은 PC 의 Cowork 가 다른 배포를 보면 `--no-persist-env` 를 붙인다.

나머지는 할 일이 없다 — Cowork 는 「A3」 에서 목록 첫 항목(기본 모델)이 이미 바뀌었고, Mac ·
Linux 수동 설치는 모델을 고정하지 않는다. 빠진 PC 가 있는지는 「C1」 의 사용량 확인에서
드러난다.

### B2. 폴백 규칙을 바꾼다

관리 화면 **예산 관리 → 자동 다운그레이드** 에서 다음처럼 고친다. 결과가 「현재 구성」 의
폴백 규칙과 같아야 한다.

- 옛 기본 모델(`claude-sonnet-5`)로 내려가는 행 → 새 기본 모델(`claude-sonnet-5-5`)로 고친다.
- 출발 모델이 옛 모델(`claude-opus-5` 등)인 행 → 지운다.

화면은 목록 전체를 저장하므로, 남길 행이 모두 보이는지 확인하고 저장한다. 규칙은 gateway-proxy
가 기동할 때 읽으므로 재시작한다.

▶ **실행** · 배포 EC2

```bash
kubectl -n llm-gateway rollout restart deploy/llm-gateway-gateway-proxy
```

여기까지 했으면 「C. 모델 내리기」 로 이어간다.

### 되돌리기

새 기본 모델에 문제가 생기면 다음 순서로 되돌린다(「C」 까지 끝낸 뒤라도 같다).

1. 옛 모델을 이미 내렸다면 관리 화면 **모델 관리** 에서 그 별칭을 **활성화** 한다(반영은 5분 뒤).
2. 「B1」 의 명령을 옛 모델(`claude-sonnet-5`)로 바꿔 다시 실행한다. Cowork 는 목록에도 옛
   모델을 넣는다.
3. 폴백 규칙을 원래대로 돌리고 gateway-proxy 를 재시작한다.

새 모델을 목록에서 아예 빼려면 「C」 대로 비활성화한다(삭제는 안 된다).

---

## C. 모델 내리기

안 쓰는 모델을 선택 목록에서 치운다. 교체(「A」 → 「B」 → 「C」) 중이라면 **「B」 를 끝낸 뒤에만**
한다. 내릴 모델은 「현재 구성」 의 "내린 모델" 이다.

### C1. 아무도 안 쓰는지 확인한다 — 관리 화면

**분석** 에서 기간을 최근 7일쯤으로, 묶음을 **모델별** 로 두고 내릴 모델의 사용이 0 인지 본다.
배포 EC2 에서는 `bash 16-usage-recent.sh --hours 168` 의 `model` 열로도 볼 수 있다.

- **0 이다** — 「C2」 로 간다.
- **남아 있다** — 누군가 아직 그 모델을 쓴다. 교체 중이면 그 사용자의 PC 를 「B1」 대로 바꾸고,
  아니면 사용자에게 알린 뒤 내린다.

내릴 모델이 폴백 규칙에 들어 있으면 먼저 규칙에서 뺀다(**예산 관리 → 자동 다운그레이드** 저장 →
gateway-proxy 재시작, 「A4」 와 같다). 없는 모델로 내려가는 규칙은 장애 때 실패한다.

### C2. 비활성화한다 — 관리 화면

**모델 관리** 에서 내릴 별칭마다 **비활성화** 를 누른다. 저장하면 목록 캐시를 지우므로 바로
반영된다.

- **삭제는 안 된다** — `model_aliases` 를 참조하는 FK 가 여럿이고 `ON DELETE` 가 없어 실패한다.
- 비활성으로 바꿔도 과거 사용량·비용 기록은 그대로 남는다.
- 되돌리려면 같은 자리에서 **활성화** 를 누른다.

### C3. Cowork 목록에서 뺀다 — 사용자 PC

Claude Code 는 서버 목록을 따르므로 할 일이 없다. Cowork 는 PC 에 저장된 목록을 보여 주므로
서버에서 내려도 선택기에 남는다(고르면 실패한다). 「A3」 의 Cowork 명령을 「현재 구성」 의
목록으로 다시 실행한다. 교체 중에 「A3」 를 이미 그 목록으로 실행했다면 건너뛴다.

### C4. 확인한다 — 배포 EC2

▶ **실행** · 배포 EC2

```bash
bash status.sh
```

모델 줄이 전부 OK 면 끝이다. 교체 중이라면 옛 모델이 하나라도 ACTIVE 일 때 "부분 적용" 으로
나온다. EC2 에 들어갈 수 없으면 관리 화면 **모델 관리** 의 ACTIVE 목록이 「현재 구성」 의 모델
목록과 같은지 본다.

---

## 부록 — 새 모델이 나왔을 때 저장소에 반영할 곳

> 운영자는 읽지 않아도 된다. 저장소 관리자가 새 모델을 문서·스크립트에 넣을 때 쓴다. 이것을
> 끝내 두면 운영자는 「현재 구성」 의 값만 따라 하면 된다.

**1) 쓸 수 있는 모델인지 확인한다** — 배포 계정에서. `us.anthropic.…` 이 `ACTIVE` 이고
`authorizationStatus` 가 `AUTHORIZED` 여야 한다. 모델이 `INFERENCE_PROFILE` 전용이면 별칭의 모델
ID 에 `us.` 접두사가 필수다.

▶ **실행** · 배포 EC2 (`sonnet-5-5` 자리에 새 모델 이름)

```bash
aws bedrock list-inference-profiles --region us-west-2 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId,'sonnet-5-5')]"
aws bedrock get-foundation-model-availability --region us-west-2 \
  --model-id anthropic.claude-sonnet-5-5
```

**2) 단가를 계산한다** (1K 토큰당 USD)

- **입력 · 출력** = Anthropic 정가(1M 토큰당) ÷ 1000 × **1.1** — `us.` 지리 엔드포인트는 글로벌
  정가의 1.1배로 청구된다.
- **캐시 5분 쓰기** = 입력 × 1.25 · **1시간 쓰기** = 입력 × 2 · **읽기** = 입력 × 0.1
- **예외** — 캐시 읽기 배수는 모델마다 다를 수 있다(Opus 5.5 는 × 0.05, Fable 5.1 은 × 0.025).
  공식 가격표의 각주를 확인한다.

AWS Pricing API 에는 신모델이 없어 단가는 수동이고, 조용히 낡는다. `asof` 날짜를 반드시 적는다.

**3) 반영할 곳**

- `update-scripts/pricing.tsv` — 한 줄 추가(`asof` · 출처 포함), 헤더 주석에 계산 근거.
- `update-scripts/config.env.example` — 그 모델의 주석 블록(별칭 · 모델 ID · 표시명 · 설명 ·
  단가 5종 · `MODEL_PRICE_ASOF`).
- 이 문서 — 「현재 구성」 전체와, 명령 예시의 모델 값(「A1-1」 · 「A2」 · 「A3」 · 「B1」).
- `install-guide.md` §4-2 와 `ops/8-P-prod.md` ②-b 의 SQL — (B) 등록 · (C) 단가 · (D) `NOT IN`
  목록. (C) 는 `bash 08-set-model-pricing.sh --print-sql --alias <별칭> …` 로 뽑는다. **두 블록은
  글자 하나까지 같아야 한다** — 한쪽만 고치면 dev 와 prod 설치 결과가 갈라진다.
- `update-scripts/status.sh` — 그 `US-NN` 의 판정.
- README · updates (한국어 · 영어) — `US-NN` 행과 설명, 그리고 모델 목록 문장(README 1절 ·
  `install-overview.md` · `architecture.md`).
- 클라이언트 문서의 모델 목록 — Cowork(Windows 수동 · 자동 · 설치기 빌드 · macOS)와 Claude Code
  설치기의 `--model`.
- `deployment/scripts/bootstrap-ec2.sh` — 배포 EC2 의 Claude Code 고정 모델.
- IAM — `terraform.tfvars` 의 `bedrock_model_arns` 와일드카드가 새 모델 이름을 잡는지 본다.
  비-Claude 모델이면 ARN 을 더하고 `terraform apply` 한다.

**4) dev 에서 실제로 돌려 본다** — 「0」 → 「A」 를 따라 해 `status.sh` 가 OK, 첫 호출(「A2」)이
성공하는지 확인한다. 클라이언트 최소 버전이 나오면 「현재 구성」 에 적는다. 하루 뒤 Cost
Explorer 로 단가를 검산한다.
