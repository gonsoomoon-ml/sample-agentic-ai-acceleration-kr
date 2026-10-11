# 8-D. upstream 동기화 배포 — 코드·스키마·단가를 한 번에

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-D** · 소요 ~1.5h(빌드 15분 · helm+migration 10분 · 대기 포함) · **트래픽 적은 시간**에
>
> **한 줄**: 코드·DB·단가 업데이트를 배포 EC2 에서 **위에서 아래로 명령만 치는** 순서.
> 업데이트마다 다른 점은 (0) 에만 있고, (1) 부터는 어느 업데이트든 같다. 이유·함정·상세는 링크.
> 서비스 하나 고친 일상 업데이트는 [8-U](8-U-update.md).

```
(0) 이번 업데이트에서 다른 점(읽기만)
→ (1) 저장소 최신화 → (2) 사전 점검·설정 적용 → (3) DB 스냅샷
→ (4) terraform plan(확인만) → (5) 태그 올림(13) → (6) 바뀐 이미지만 빌드
→ (7) install-eks.sh(migration+롤아웃) → (8) 업데이트별 작업·단가(08)
→ (9) 사후 점검(14)·24h 관찰
```

전제: 배포 EC2 의 `~/awsome-ai-gateway`, `update-scripts/config.env` 가 이 환경(`DEPLOY_ENV=dev`). prod 는 §(10).

📋 용어: **마이그레이션** = DB 스키마를 한 단계씩 바꾸는 번호 붙은 스크립트(`db/versions/0034_….py` 식). 이번 업데이트에 새 번호가 있으면 `install-eks.sh` 안에서 순서대로 돈다(몇 개인지는 (0)).

## (0) 이번 업데이트에서 다른 점 — 먼저 확인

여기에는 명령이 없습니다. 따라 하는 업데이트의 블록을 읽고, **할 일 있음** 항목은 그 단계의
해당 업데이트 이름표(예: (8) 의 「▶ US-19」) 아래에서 명령을 칩니다. (1) 부터는 어느 업데이트든
같고, prod((10))도 같습니다.

### US-19 로 따라 할 때 달라지는 것

- **바뀌는 것** — 이미지 4개(migration · gateway-proxy · admin-api · admin-ui) · 단가 열 소수
  자릿수(6 → 8) · Haiku 5.5 등록. 마이그레이션 번호는 늘지 않습니다(`0039` 그대로) — 단가 열은
  migration Job 이 먼저 실행하는 init SQL 이 넓힙니다. US-18 을 아직 안 했다면 US-18 부터.
- **(2)** — `14` 에 `XX` 가 없고, `!!` 에 `claude-haiku-5-5: no open price row` 한 줄이 더
  나오면 정상입니다(아직 등록 전).
- **(8)** — **할 일 있음**: (8) 의 「▶ US-19 — Haiku 5.5 등록」(새 모델). 그다음 공통 `08` 이
  100K 초과 구간 단가까지 넣습니다.
- **(9)** — **확인 추가**: (9) 의 「▶ US-19」.
- **되돌리기** — `helm rollback` 만으로 됩니다(넓힌 단가 열은 옛 이미지와도 맞습니다). Haiku 5.5
  를 등록했다면 관리 화면에서 INACTIVE 로(옛 gateway-proxy 는 Haiku 5.5 에 400).

### US-18 로 따라 할 때 달라지는 것

- **바뀌는 것** — 이미지 5개(migration · gateway-proxy · admin-api · admin-ui ·
  cost-recorder-worker) · DB 열 추가 3개(0037·0038·0039) · Sonnet 5.5 캐시 읽기 단가 1개.
- **(2)** — `14` 의 `XX` 2개(스키마 `0036` · Sonnet 5.5 단가)면 정상입니다.
- **(8)** — 할 일 없음. `08` 의 차이 표에 `claude-sonnet-5-5` 캐시 읽기 `0.000220 → 0.000110`
  한 줄이면 정상입니다.
- **(9)** — **확인 추가**: (9) 의 「▶ US-18」.

### US-10 로 따라 할 때 달라지는 것

- **바뀌는 것** — 이미지 6개 · DB 마이그레이션 11개(0026~0036) · 단가.
- **(1)** — 이번 upstream 이 추가한 값(스트리밍 타임아웃·감사 로그 env)은 chart 기본값으로
  충분합니다. DB 마스터 비밀번호 참조 2줄은 (2) 의 `15` 가 확인·삽입합니다.
- **(2)** — `14` 의 `XX` 3~4개(DB 가 옛 마이그레이션 0025 · 단가 · system_settings 표 없음)가
  정상입니다.
- **(7)** — migration 과 롤아웃 사이 수 분간 옛 파드가 모델 목록 조회에 실패할 수 있습니다
  (0032 가 옛 코드가 모르는 provider 값을 심음) — 그 창에서 rollback 하지 않습니다. Job 이
  0034 에서 죽었으면 (2) 의 alias 대소문자 중복입니다.
- **(8)** — **할 일 있음**: (8) 의 「US-10 — `08` 뒤에」. 마이그레이션(0027·0030)이 모델·단가
  시드를 넣으므로 `08` 은 꼭 배포 뒤입니다.
- **되돌리기** — 코드만 되돌리면 옛 이미지가 0032 가 심은 행을 못 읽어 모델 목록이 깨집니다 —
  스냅샷 복원과 함께 합니다.

## (1) 저장소 최신화 — [README §3 의 「저장소 최신화」](../README.md#3-적용하기-배포-ec2-에서)와 같음

▶ 실행
```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
ls docs/us-llm-gateway/update-scripts/1[34]-*.sh
```
기대: `values restored OK` · 마지막 줄에 `13-bump-image-tags.sh` `14-postdeploy-check.sh`. `RESTORE FAILED` 면 멈춘다(values 는 이 EC2 유일본).

## (2) 사전 점검 · 설정 적용 — values 파일까지만, 15분

▶ 실행
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 00-preflight-check.sh
bash 14-postdeploy-check.sh --save pre
bash 06-persist-annotations.sh
bash 15-set-master-secret-ref.sh
bash 17-set-websearch-caps.sh
bash 17-set-websearch-caps.sh --apply
```
기대:
- `00`·`14` 는 읽기 전용, `06`·`15`·`17` 은 **values 파일만** 고친다(클러스터 반영은 7단계).
- `00` 의 **「4. Migration pre-check」가 전부 OK**. `XX` 가 하나라도 있으면 진행 금지 — alias 대소문자 중복은 마이그레이션 0034(alias 를 대소문자 구분 없이 유일하게 만드는 인덱스)를, backend 값은 0032(라우팅 backend 허용 목록 갱신)를 실패시킨다.
- `14` 의 `XX` 는 **(0) 블록에 적힌 것만 정상**이다(배포 전이라 새 스키마·단가가 아직 없음). 목적은 배포 전 숫자를 `snapshots/pre.numbers` 에 남기는 것.
- `06` 은 `already matches`. 아니면 `--apply`([8-U 0단계](8-U-update.md)).
- `15` 는 표 3행(masterPasswordRemoteKey · masterPasswordRemoteProperty · masterUser)에 `<- change` 없이 `OK … nothing to do`. 새 차트의 migration Job 은 init SQL·권한 부여를 **DB 마스터 사용자**로 실행하므로, values 가 그 비밀번호를 RDS 가 직접 로테이션하는 시크릿(`rds!cluster-<uuid>`)에서 가져오게 돼 있어야 한다(`database.external.masterPasswordRemoteKey`·`…RemoteProperty`). `<- change` 가 있으면 `bash 15-set-master-secret-ref.sh --apply`(백업 후 삽입, helm 렌더로 검증) — 없이 (7) 을 돌리면 migration Job 이 5분 타임아웃으로 죽는다.
- `17` 은 기존 배포에선 표의 행(web search 상한 4개 — 결과 크기·결과 수·턴당 검색 수·반복 수 — 와 동작 스위치)이 `<- change` 로 나온다 — 그래서 `--apply` 까지 두 줄(`yes` 입력, 백업 후 values 에 삽입, helm 렌더로 검증). 이미 들어 있으면 `nothing to do` 로 지나간다. (7) 의 롤아웃에서 적용되고, **(7) 을 이미 끝낸 뒤라면 `install-eks.sh` 를 한 번 더** 돌린다. **9/19 부터(gateway-proxy 1.0.80 이상) 이 목표값이 코드 기본값과 같다** — `rendered now` 열에 `(unset -> code default …)` 로 보이는 행은 넣지 않아도 동작이 같고, `--apply` 는 같은 값을 values 에 명시해 둘 뿐이다(값을 바꾸거나 스위치를 끌 때가 `17` 의 용도). 검색 결과가 다음 턴 입력으로 되돌아와 반복마다 과금되는 구조라, **1.0.80 이전 이미지의** 기본값(60,000자·10개·4회·5회)으로는 검색 질문 1건이 $3 를 넘긴다(2026-09-16 실측 $3.62) — 12,000자·5개·3회·2회(요청당 최대 6회)면 약 $1.5 이하. 값은 `config.env` 의 `WEB_SEARCH_*`.
- 단가의 정본은 **파일 하나** — `docs/us-llm-gateway/update-scripts/pricing.tsv`(alias 별 입력·출력·캐시 단가, /1K, US `us.` Standard 티어). `14` 와 `08` 은 이 파일과 DB 를 비교한다. 다른 리전·티어로 청구받는 배포라면 **(8) 전에** 이 파일을 자기 청구 단가로 고친다(`asof`·`source` 열 포함) — 그러면 `08` 은 "차이 없음", `14` 는 OK.

## (3) DB 스냅샷 — 되돌리기의 기준점

helm rollback 은 코드만 되돌린다. 마이그레이션은 되돌리지 않으므로 DB 는 이 스냅샷으로만 되돌린다(§롤백).

▶ 실행
```bash
SNAP=llm-gateway-dev-pre-sync-$(date +%Y%m%d)
aws rds create-db-cluster-snapshot --db-cluster-identifier llm-gateway-dev \
  --db-cluster-snapshot-identifier $SNAP --query DBClusterSnapshot.Status
aws rds wait db-cluster-snapshot-available --db-cluster-snapshot-identifier $SNAP
aws rds describe-db-cluster-snapshots --db-cluster-snapshot-identifier $SNAP \
  --query 'DBClusterSnapshots[0].[DBClusterSnapshotIdentifier,Status]' --output text
```
기대: `"creating"` → wait 가 조용히 끝남(수 분) → 마지막 줄 `llm-gateway-dev-pre-sync-<날짜>  available`. 이 이름을 §롤백에서 쓴다.

## (4) terraform — 인프라 변경 확인 (필요할 때만 apply)

▶ 실행
```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform init
terraform plan -no-color -detailed-exitcode > ~/plan.txt 2>&1; echo "exit=$?"
grep -E '# .* (will be|must be)|^Plan:|No changes|Error' ~/plan.txt
```
`exit=` 값으로 판정합니다.
- `0` — 변경 없음. apply 하지 않고 (5) 로 넘어갑니다.
- `1` — plan 오류. `Error` 줄을 보고 멈춥니다(출력이 비면 terraform 폴더 밖에서 친 것입니다).
- `2` — 변경 있음. 아래 「멈출 때」에 해당하지 않으면 `terraform apply`(`yes` 입력)로 적용하고,
  위 plan 두 줄을 다시 쳐서 `exit=0` 을 확인합니다.

멈출 때(apply 하지 않습니다):
- 이유를 모르는 destroy·replace
- EKS 버전·애드온 변경 — tfvars 의 버전 고정을 확인합니다([8-E](8-E-eks-upgrade.md)).
- `external-secrets` 줄 — fork 의 ESO OFF 설정이 덮인 것입니다. `modules/external-secrets/main.tf`
  의 3줄을 복원한 뒤 다시 plan 합니다(켜면 cert-controller 가 0/1 로 멈춥니다).

## (5) 이미지 태그 올림 — 새 코드는 새 태그로

같은 태그로 rebuild 하면 옛 이미지가 덮여 helm rollback 이 무의미해진다. 표의 `template` 열이 기대값이다(숫자를 외우지 않는다).

▶ 실행
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 13-bump-image-tags.sh dev
bash 13-bump-image-tags.sh dev --apply
```
기대: (0) 블록의 「바뀌는 것」에 적힌 이미지만 `<- change`(처음 맞추는 values 면 `<- pin explicitly`) → `--apply` 후 `OK values updated` 와 바뀐 줄 목록. 백업 = `snapshots/<ts>-values-dev.yaml.bak`.

📋 참고(동기화 담당자용): upstream 이 코드를 바꾸고도 태그를 안 올린 서비스는 13 이 "변경 없음" 으로 본다. 리베이스 뒤 서비스마다 `git log <이전 배포 base>..HEAD -- <서비스 디렉터리>` 로 코드 변경을 보고, 변경됐는데 템플릿 태그가 그대로면 **fork 템플릿(dev·prod)에서 새 태그를 매긴 뒤** (5) 를 돈다. 같은 태그로 rebuild 하면 helm 이 변화를 못 봐 롤아웃이 없고(옛 코드 계속 실행) 옛 이미지만 덮인다(2026-09-15: notification-worker·cost-recorder-worker 가 그 경우). upstream 이 태그 이후 한 번도 빌드하지 않은 커밋은 우리가 첫 실행이 되므로 (9) 의 기능 확인을 생략하지 않는다(같은 날 admin-ui 미들웨어 500 이 그렇게 잡혔다).

## (6) 이미지 빌드·push — 태그가 바뀐 것만, 15분

▶ 실행
```bash
cd ~/awsome-ai-gateway
for s in migration gateway-proxy admin-api admin-ui notification-worker \
  cost-recorder-worker; do ./deployment/scripts/rebuild-image.sh $s dev || break; done
```
기대: (5) 에서 `<- change` 가 붙은 서비스만 `✅ push 완료`, 나머지는 `⏭️ 건너뜀 — ECR 에 이미 있음`(`rebuild-image.sh` 는 태그가 그대로인 이미지를 다시 빌드하지 않아 옛 이미지가 덮이지 않는다). 스크립트가 끝에 `rollout restart` 를 안내하지만 **하지 않는다** — (7) 이 한 번에 올린다. ECR 확인은 [8-P §2-5](8-P-prod.md#2-5-컨테이너-이미지-빌드--ecr-install-guide-3-5) 의 목록 명령.

## (7) 배포 — migration + 롤아웃, 10분

▶ 실행
```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-dev
terraform init | tail -3
terraform output -json >/dev/null && echo "output OK"
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh dev
```
기대: `output OK` → migration Job Completed → **태그가 바뀐 서비스의** Deployment 롤아웃 → `deployed`.
- `terraform init` 을 다시 하는 이유: (1) 의 `git reset --hard` 가 `.terraform.lock.hcl` 을 커밋본으로 되돌려, 그대로 두면 `install-eks.sh` 가 `terraform output 실패` 로 멈춘다(인프라·state 무관 — [8-U](8-U-update.md#terraform-output-실패로-멈추면--terraform-apply-를-돌리지-말-것)). init 은 lock 만 고쳐 쓴다.
- 순서는 **migration 먼저, 롤아웃 나중**(pre-upgrade hook). 그 사이 수 분간 옛 파드가 새 스키마와 어긋날 수 있다 — **그 창에서 rollback 하지 않는다**. 새 파드 Ready 로 끝난다.
- Job 실패: `kubectl -n llm-gateway logs job/llm-gateway-migration-<rev>`(`helm history` 최신 rev).

## (8) 업데이트별 작업 · 단가 — 5분 + 캐시 5분

### 업데이트별 작업 — 따라 하는 업데이트 것만, `08` 보다 먼저

**▶ US-19 — Haiku 5.5 등록 (새 모델)**

▶ 실행
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
cp config.env ~/config.env.bak-us19
sed -n '/^# Claude Haiku 5.5/,/CACHE_READ/s/^#   //p' config.env.example >> config.env
echo 'MODEL_PRICE_ASOF="2026-10-11"' >> config.env
bash 02-add-opus5-model.sh
bash 02-add-opus5-model.sh --apply
```
기대: 미리보기에 `claude-haiku-5-5` · `us.anthropic.claude-haiku-5-5` · 단가 `0.000110 /
0.000550 / 0.0001375 / 0.000220 / 0.000011` → `--apply` 뒤 등록 완료. 100K 초과 구간 단가는
아래 `08` 이 넣는다.

**US-18 — 할 일 없음.** 아래 `08` 의 차이 표에 `claude-sonnet-5-5` 캐시 읽기 `0.000220 →
0.000110` 한 줄이면 정상.

**US-10 — `08` 뒤에** 관리 화면에서 새로 ACTIVE 로 보이는 시드 alias(`global.anthropic.claude-opus-5`
· `…-sonnet-5` · `gpt-5.6-*` · `llama-3-70b`)를 INACTIVE 로.

### 단가 맞춤 — 모든 업데이트

▶ 실행
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 08-set-model-pricing.sh
bash 08-set-model-pricing.sh --apply
```
기대: 차이 표(바뀌는 alias 만) → `--apply` 후 검증 행이 표와 일치. 바뀌는 것이 없으면
`nothing to change`. 위에서 새 모델을 등록했으면 그 모델이 CHANGE 로 나온다 — US-19 는
`claude-haiku-5-5` 의 `long: none → prompt > 100000 tokens: …`. 왜 배포 **뒤**인가: 마이그레이션이 단가 행을 건드릴 수 있어서([US-11](../updates.md)). 단가 표 = `update-scripts/pricing.tsv` 가 정본이다 — 청구 단가가 다르면 `--apply` 전에 이 파일부터 고친다(2단계). 단가만 따로 다룬 문서 = [8-R](8-R-pricing.md).

(9) 의 `14` 가 ACTIVE 로 남은 시드 alias 를 알려 준다 — 있으면 관리 화면에서 INACTIVE 로 둔다.

## (9) 사후 점검 — 14 + 종단 1건, 그리고 24h

### 업데이트별 확인 — 따라 하는 업데이트 것만 (아래 공통 점검과 함께)

**▶ US-19** — `bash status.sh` 의 US-19 가 OK(구간 단가 포함) · 등록 5분 뒤 Claude Code 로
`claude --model claude-haiku-5-5 -p "hi"` 가 답함(thinking 400 이면 gateway-proxy 가 옛 이미지).

**▶ US-18** — 예산 알림 기준(50 저장 → 6분 뒤에도 50) · 분석 화면(30초 안에 두 번 열어도
정상, 앱 필터를 바꾸면 숫자가 바뀜).

### 공통 점검 — 모든 업데이트

▶ 실행 · EC2 — 자동 점검과 접속값
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 14-postdeploy-check.sh --compare snapshots/pre.numbers
bash 07-client-values.sh
```
기대: 14 는 `OK no failures`. 07 출력의 `macOS / Linux` 아래 `export …` 4줄을 이 EC2 셸에
붙여 넣습니다.

▶ 실행 · Mac — 터널 (새 터미널 탭. 멈춘 것처럼 보이는 것이 정상이니 로그인이 끝날 때까지 둡니다)
```bash
ssh -N -L 8090:localhost:8090 -i ~/.ssh/<키>.pem ubuntu@<배포 EC2 공인 IP>
```
로그인 뒤 브라우저가 돌아오는 `localhost:8090` 을 EC2 로 넘겨 줍니다. `<배포 EC2 공인 IP>` 는
지금 SSH 로 접속할 때 쓰는 주소입니다. VS Code·Cursor Remote-SSH 로 붙었다면 자동으로 넘겨 주므로
건너뜁니다.

▶ 실행 · EC2 — 로그인과 VK (출력된 URL 을 Mac 브라우저로 엽니다)
```bash
gateway-cli login --redirect-port 8090
export GATEWAY_KEY=$(api-key-helper | grep -m1 '^vk-')
echo ${GATEWAY_KEY:0:3}
```
기대: `Login successful` → `vk-`(키 자체는 화면에 나오지 않습니다). 끝에 `Next: run gateway-cli setup`
이 나와도 setup 은 하지 않습니다. 이제 Mac 의 터널 탭을 `Ctrl+C` 로 닫습니다.
- 브라우저가 `localhost refused to connect` → 터널이 없거나 멈췄습니다.
- 터널이 `Address already in use` → Mac 의 8090 을 다른 앱이 씁니다. 터널과 `--redirect-port` 를
  둘 다 8091 로 바꿉니다(등록 포트 8090·8091·8092).
- `Missing required OIDC config` → 07 의 4줄을 이 셸에 붙여 넣지 않은 것입니다.
- VK 를 `ANTHROPIC_AUTH_TOKEN` 으로 export 하지 않습니다. 같은 셸의 Claude Code 가 만료 뒤 401 이 납니다.

▶ 실행 · EC2 — 종단 1건
```bash
GW=https://$(kubectl -n llm-gateway get ingress llm-gateway-gateway -o jsonpath='{.spec.rules[0].host}')
bash 04-verify.sh --base-url $GW --vk $GATEWAY_KEY
```
기대: `HTTP 200` + 「C」 표에 새 행(비용 = 입력·출력 토큰 × Standard 단가 — 예: 491/10 토큰이면
0.002976).

| 확인 | 어떻게 | 기대 |
|---|---|---|
| 스키마 | 14 `alembic_version` | repo 의 최신 마이그레이션 번호 |
| 단가 | 14 price 행 · 호출 1건 뒤 `usage_logs.cost_usd` 검산 | pricing.tsv 와 일치 |
| 라우팅 | 14 `claude-… -> us.anthropic.…` | `us.` 그대로(Global 로 안 바뀜) |
| web search | Claude Code 로 검색 필요한 질문 1건 → 14 의 W(24h) | `web_search_count` ≥ 1 |
| thinking | `thinking:{type:enabled}` 요청 1건 | 200(400 아님) |
| readiness | 14 §4 `/health/ready` | 200 `HEALTHY` |
| 예산 | 14 U/B 비율 | 경고 없음(≤ 1.05) |
| 24h | 다음날 `14 --compare` 다시 | 오류 비율·비용 급변 없음 |

## 롤백

- **코드+DB 전부** — (3) 스냅샷 복원(새 클러스터로 생기므로 RDS Proxy 대상 교체가 따른다, 1h+) **+** `helm rollback`([8-U 4단계](8-U-update.md#4단계--안-되면-되돌린다)). 코드만 되돌려도 되는지는 (0) 블록의 「되돌리기」를 먼저 본다 — 옛 이미지가 새 스키마를 못 읽는 업데이트(예: US-10)는 스냅샷 복원과 함께 해야 한다.
- **단가만** — `snapshots/<ts>-08-pricing-rollback.sql` 을 psql 파드로.
- **values 태그만** — `snapshots/<ts>-values-dev.yaml.bak` 복원 후 `install-eks.sh dev`.

## (10) prod — prod 계정의 배포 EC2 에서, dev (9) 를 하루 지켜본 뒤

dev 와 같은 순서다. 다른 것은 셋뿐 — values 파일 이름, 스냅샷·terraform 디렉터리의 `prod`, 스크립트 인자 `prod`. `config.env` 는 prod EC2 의 것(`DEPLOY_ENV="prod"`)을 쓴다.

**(0) 의 업데이트 블록은 prod 에도 그대로 적용한다** — 블록의 (2)·(8)·(9) 항목은 (10-2)·(10-8)·(10-9) 에서 본다.

**(10-1) 저장소 최신화**

▶ 실행
```bash
cd ~/awsome-ai-gateway && git remote -v
V=deployment/charts/llm-gateway/values-eks-fargate-prod.yaml
cp $V ~/values.bak && git fetch origin
git reset --hard origin/us/deploy-fixes && cp ~/values.bak $V
cmp -s $V ~/values.bak && echo "values restored OK" || echo "RESTORE FAILED"
```

**(10-2) 사전 점검 · 설정 적용**

▶ 실행
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
grep -n '^DEPLOY_ENV' config.env
bash 00-preflight-check.sh
bash 14-postdeploy-check.sh --save pre
bash 06-persist-annotations.sh
bash 15-set-master-secret-ref.sh
bash 17-set-websearch-caps.sh
bash 17-set-websearch-caps.sh --apply
```
기대: `DEPLOY_ENV="prod"` · 「4. Migration pre-check」 전부 OK · `06` 은 `already matches` · `15`·`17` 은 `nothing to do`(아니면 `--apply`).

**(10-3) DB 스냅샷**

▶ 실행
```bash
SNAP=llm-gateway-prod-pre-sync-$(date +%Y%m%d)
aws rds create-db-cluster-snapshot --db-cluster-identifier llm-gateway-prod \
  --db-cluster-snapshot-identifier $SNAP --query DBClusterSnapshot.Status
aws rds wait db-cluster-snapshot-available --db-cluster-snapshot-identifier $SNAP
aws rds describe-db-cluster-snapshots --db-cluster-snapshot-identifier $SNAP \
  --query 'DBClusterSnapshots[0].[DBClusterSnapshotIdentifier,Status]' --output text
```
기대: 마지막 줄 `llm-gateway-prod-pre-sync-<날짜>  available`.

**(10-4) terraform — 인프라 변경 확인**

▶ 실행
```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
terraform init
terraform plan -no-color -detailed-exitcode > ~/plan.txt 2>&1; echo "exit=$?"
grep -E '# .* (will be|must be)|^Plan:|No changes|Error' ~/plan.txt
```
판정·apply·멈출 때는 (4) 와 같습니다.

**(10-5) 태그 올림**

▶ 실행
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 13-bump-image-tags.sh prod
bash 13-bump-image-tags.sh prod --apply
```

**(10-6) 이미지 빌드·push — 태그가 바뀐 것만**

▶ 실행
```bash
cd ~/awsome-ai-gateway
for s in migration gateway-proxy admin-api admin-ui notification-worker \
  cost-recorder-worker; do ./deployment/scripts/rebuild-image.sh $s prod || break; done
```
기대: (6) 과 같다 — `<- change` 서비스만 push, 나머지는 건너뜀.

**(10-7) 배포**

▶ 실행
```bash
cd ~/awsome-ai-gateway/deployment/terraform/environments/llm-gateway-prod
terraform init | tail -3
terraform output -json >/dev/null && echo "output OK"
cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh prod
```

**(10-8) 업데이트별 작업 · 단가** — (8) 과 같다. 먼저 (8) 의 「업데이트별 작업」 중 따라 하는 업데이트 것을 하고(명령 같음), 그다음 아래 `08`.

▶ 실행
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 08-set-model-pricing.sh
bash 08-set-model-pricing.sh --apply
```

**(10-9) 사후 점검** — 순서·기대·기능별 확인 표는 (9) 와 같습니다. (9) 의 「업데이트별 확인」도 함께 합니다.

▶ 실행 · prod EC2 — 자동 점검과 접속값
```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts
bash 14-postdeploy-check.sh --compare snapshots/pre.numbers
bash 07-client-values.sh
```
▶ 실행 · Client VPN 에 연결된 PC — VK 받기. prod admin-api 는 internal 이라 prod EC2 에서는
로그인할 수 없습니다. PC 셸에 07 출력에서 그 OS 의 4줄을 붙여 넣고 `gateway-cli login --redirect-port 8090`
→ `api-key-helper` 의 `vk-…` 를 복사합니다(같은 PC 라 터널은 필요 없습니다). 그다음 prod EC2 에서
`read -rs GATEWAY_KEY && export GATEWAY_KEY` 를 실행해 붙여 넣습니다(화면에 안 보입니다).

▶ 실행 · prod EC2 — 종단 1건
```bash
GW=https://$(kubectl -n llm-gateway get ingress llm-gateway-gateway -o jsonpath='{.spec.rules[0].host}')
bash 04-verify.sh --base-url $GW --vk $GATEWAY_KEY
```

롤백은 §롤백에서 `dev`→`prod`(스냅샷 `llm-gateway-prod-pre-sync-…`, 백업 `snapshots/<ts>-values-prod.yaml.bak`).
