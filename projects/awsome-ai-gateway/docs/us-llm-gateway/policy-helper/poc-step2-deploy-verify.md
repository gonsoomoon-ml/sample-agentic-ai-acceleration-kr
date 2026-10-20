# policyHelper PoC 2단계 — 서버 정책 배포와 확인

- 작성일: 2026-09-26
- 대상: US dev(891)에 2단계를 배포하고 Windows 머신에서 확인하는 사람
- 목표: 게이트웨이가 사용자별 정책 조각(`GET /v1/policy`)을 내주고, policy-helper 가 그것을 받아
  Claude Code 의 모델 선택 목록을 바꾸는지 확인합니다.

## 0. 이 단계에서 바뀌는 것

| 대상 | 지금 | 바뀐 뒤 |
|---|---|---|
| gateway-proxy 이미지 | `1.0.81-connretry` (helm rev 44) | `1.0.82-policyhelper` — 이번 변경만 추가 (인증 경로 누락이 있어 11절로 `1.0.83` 재배포, rev 48) |
| Windows 의 helper | 0.1.0 (기본값만) | 0.2.0 (서버 조각 병합) |
| 확인용 팀 허용 모델 | 없음 | 팀 하나에 모델 제한을 겁니다(8단계) |

⚠️ 8단계의 팀 제한은 **dev 게이트웨이를 쓰는 모든 클라이언트에 서버에서 적용**됩니다. 같은 팀이면 Mac 의
Claude Code·Cowork 도 막힌 모델을 쓸 수 없게 됩니다. 확인이 끝나면 10단계에서 풉니다.

## 1. 배포 EC2 — PoC 브랜치로 바꾸기

배포 EC2(`<EC2>`)에 평소처럼 접속합니다(SSH 는 VPN 필요, 막히면 SSM).

▶ **실행** · 배포 EC2 (bash) — PoC 브랜치 받기

```bash
export AWS_DEFAULT_REGION=us-west-2
cd /home/ubuntu/sample-agentic-ai-acceleration-kr
git fetch origin feat/policy-helper-poc
git checkout feat/policy-helper-poc
git log -1 --oneline
```

`d4a3ccd chore(policy-helper): ...` 가 나오면 됩니다. values 파일의 로컬 수정은 브랜치를 바꿔도 그대로
남습니다(두 브랜치에서 같은 파일이라).

이후 배포 EC2 블록은 모두 **첫 줄에서 프로젝트 루트로 이동**합니다. 어느 폴더에 있든 블록을 그대로 붙여
넣으면 됩니다. 긴 절대 경로는 붙여 넣을 때 줄이 잘릴 수 있어 `~` 로 줄였습니다.

▶ **실행** · 배포 EC2 (bash) — 프로젝트 루트로 이동

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
pwd
```

## 2. 배포 EC2 — gateway-proxy 태그만 새 이름으로

같은 태그로 다시 빌드하면 옛 이미지가 덮여 되돌릴 수 없게 됩니다. 그래서 새 태그를 붙입니다. 먼저
values 파일을 백업하고, 바꿀 줄이 정확히 하나인지 확인합니다.

▶ **실행** · 배포 EC2 (bash) — values 백업 · 바꿀 줄이 하나인지 확인

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V $V.bak-policyhelper
grep -c 'tag: "1.0.81-connretry"' $V
```

`1` 이 나와야 합니다. 다른 숫자면 멈추고 알려 주세요.

▶ **실행** · 배포 EC2 (bash) — 태그 교체 · helm 렌더로 확인

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
sed -i 's/tag: "1.0.81-connretry"/tag: "1.0.82-policyhelper"/' $V
helm template t deployment/charts/llm-gateway -f $V | grep -oE 'gateway-proxy:[^"]+' | sort -u
```

`gateway-proxy:1.0.82-policyhelper` 한 줄이 나오면 됩니다. helm 이 실제로 당길 값을 helm 으로 확인하는
것입니다.

## 3. 배포 EC2 — 이미지 빌드와 ECR 업로드

▶ **실행** · 배포 EC2 (bash) — gateway-proxy 이미지 빌드 · ECR 업로드

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
./deployment/scripts/rebuild-image.sh gateway-proxy dev
```

출력의 `tag` 가 `1.0.82-policyhelper` 인지 보고, 끝까지 성공하면 다음으로 갑니다. **빌드가 성공하기 전에는
6단계(배포)로 가지 않습니다** — 이미지가 ECR 에 없으면 새 파드가 이미지를 못 당겨 배포가 시간 초과로
끝납니다.

확인 — ECR 에 새 태그가 올라갔는지 봅니다.

▶ **실행** · 배포 EC2 (bash) — ECR 에 새 이미지가 있는지 확인

```bash
aws ecr describe-images --repository-name llm-gateway/gateway-proxy --image-ids imageTag=1.0.82-policyhelper --query 'imageDetails[0].imagePushedAt' --output text
```

올린 시각이 나오면 됩니다. `ImageNotFoundException` 이면 빌드가 안 된 것이니 위 명령을 다시 실행합니다.

## 4. 배포 EC2 — 배포 전 어노테이션 확인

`install-eks.sh` 는 Ingress 를 다시 만듭니다. 클러스터에만 걸린 설정이 있으면 사라지므로 먼저 봅니다.
아무것도 바꾸지 않는 확인입니다.

▶ **실행** · 배포 EC2 (bash) — Ingress 허용 대역이 values 와 같은지 확인 (읽기 전용)

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
cd docs/us-llm-gateway/update-scripts
bash 06-persist-annotations.sh
```

`already matches` 나 `nothing to persist` 만 나오면 다음으로 갑니다. 옮길 것이 있다고 나오면 멈추고
알려 주세요.

## 5. 배포 EC2 — terraform output 확인

`install-eks.sh` 는 `terraform output` 으로 주소·권한을 읽습니다. 먼저 그게 되는지 봅니다(읽기만 합니다).

▶ **실행** · 배포 EC2 (bash) — terraform output 이 되는지 확인 (읽기 전용)

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
cd deployment/terraform/environments/llm-gateway-dev
terraform output | head -3
```

값이 나오면 6단계로 갑니다. `Required plugins are not installed` 가 나오면 플러그인
캐시가 빈 것이고 인프라 문제가 아닙니다. 플러그인을 다시 받습니다.

▶ **실행** · 배포 EC2 (bash) — provider 플러그인 다시 받기

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
cd deployment/terraform/environments/llm-gateway-dev
terraform init
terraform output | head -3
```

⚠️ `terraform apply` 와 `terraform init -upgrade` 는 쓰지 않습니다. `init` 이 lock 파일을 바꾸므로 6단계
끝에서 git 으로 되돌립니다.

## 6. 배포 EC2 — 배포

▶ **실행** · 배포 EC2 (bash) — 클러스터에 배포

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
./deployment/scripts/install-eks.sh dev
```

끝나면 확인합니다.

▶ **실행** · 배포 EC2 (bash) — 파드 · revision · 이미지 확인

```bash
kubectl -n llm-gateway get pods
helm -n llm-gateway history llm-gateway | tail -2
kubectl -n llm-gateway get deploy llm-gateway-gateway-proxy -o jsonpath='{.spec.template.spec.containers[0].image}'; echo
```

파드가 모두 `Running`, 새 revision(45)이 `deployed`, 이미지 끝이 `1.0.82-policyhelper` 면 됩니다.

5단계에서 `terraform init` 을 했다면 lock 파일을 git 에서 되살립니다(git 추적 파일이라 그대로 두면 다음
`git pull` 을 막습니다). 백업 복사보다 git 이 확실합니다 — 5단계를 두 번 돌리면 백업이 이미 바뀐 내용으로
덮어써집니다.

▶ **실행** · 배포 EC2 (bash) — lock 파일을 git 에서 복원

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
git checkout -- deployment/terraform/environments/llm-gateway-dev/.terraform.lock.hcl
git status -s | head -5
```

`git status` 에 lock 파일이 보이지 않으면 됩니다.

마지막으로 EC2 저장소를 원래 브랜치로 돌려놓습니다. values 파일은 새 태그로 바뀐 채 남습니다 — 클러스터가
지금 그 태그로 돌고 있으므로 맞는 상태입니다(되돌릴 때는 10단계).

▶ **실행** · 배포 EC2 (bash) — 저장소를 원래 브랜치로

```bash
cd ~/sample-agentic-ai-acceleration-kr
git checkout us/deploy-fixes
```

## 7. Windows — helper 0.2.0 으로 다시 빌드·설치

Claude Code 창을 모두 닫습니다(실행 중이면 exe 를 덮어쓸 수 없습니다). 기본값 파일은 1단계에서 만든
`C:\build\baseline.json` 을 그대로 씁니다.

▶ **실행** · 🔵 일반 PowerShell — helper 소스 받기 · 빌드

```powershell
cd C:\build\ph-src
git pull
cd C:\build\ph-src\projects\awsome-ai-gateway\policy-helper
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Baseline C:\build\baseline.json
```

▶ **실행** · 🔴 관리자 PowerShell — helper 를 관리자 전용 폴더에 설치

```powershell
cd C:\build\ph-src\projects\awsome-ai-gateway\policy-helper
Copy-Item .\dist\policy-helper\* 'C:\Program Files\PolicyHelper' -Recurse -Force
```

서버에 닿는지 바로 봅니다. VK 가 만료됐으면 helper 는 서버를 부르지 않으므로, 먼저 api-key-helper 로 VK 를
새로 받습니다.

▶ **실행** · 🔵 일반 PowerShell — VK 갱신 · helper 실행 · 캐시 확인

```powershell
& 'C:\Program Files\GatewayCLI\api-key-helper.exe' | Out-Null
& 'C:\Program Files\PolicyHelper\policy-helper.exe'
Get-Content "$env:LOCALAPPDATA\policy-helper\policy-cache.json" -Raw
```

- 아직 팀 제한이 없으므로 출력은 1단계와 같습니다(모델 관련 키 없음). stderr 에 오류가 없으면 됩니다.
- 캐시 파일이 생기고 `source` 가 `https://gateway-dev.awsome-ai-gw.click/v1/policy` 이면, **helper 가 서버에서
  정책을 받아 온 것**입니다.

## 8. 팀 허용 모델 걸기 (dev 관리 화면)

Windows 머신에서 로그인하는 사용자의 팀에 모델 제한을 겁니다.

1. dev 관리 화면 → **모델** 메뉴 → 아래쪽 **팀별 모델 접근 관리**
2. 그 팀에 `claude-sonnet-5` 와 `claude-haiku-4-5-20251001` 만 허용하고 저장합니다(`claude-opus-5-5` 는 뺍니다).

정책은 서버가 요청마다 DB 에서 새로 계산하므로 바로 반영됩니다. helper 를 다시 실행해 봅니다.

▶ **실행** · 🔵 일반 PowerShell — 팀 제한 뒤 helper 출력 확인

```powershell
& 'C:\Program Files\PolicyHelper\policy-helper.exe'
```

출력 끝에 `"availableModels": ["claude-haiku-4-5-20251001", "claude-sonnet-5"], "enforceAvailableModels": true`
가 보이면 됩니다.

## 9. Claude Code 에서 확인

▶ **실행** · 🔵 일반 PowerShell — 새 창에서 · Claude Code 실행

```powershell
claude
```

다음 셋을 확인하고, 화면을 그대로 붙여 주세요.

1. `/status` — Setting sources 에 `Enterprise managed settings (helper)` 가 있는지
2. `/model` — 선택 목록에 허용한 두 모델만 보이는지. 이 PC 에 저장된 기본 모델은
   `claude-opus-5-5[1m]` 이라, 허용 목록 밖의 기본값을 Claude Code 가 어떻게 처리하는지도 이때 봅니다.
3. `hi` — 응답이 오는지. 게이트웨이 기록에 이 질의는 남고, **helper 의 정책 조회는 남지 않아야** 합니다
   (제가 `usage_logs` 에서 확인합니다).

## 10. 되돌리기

**팀 제한 풀기** — 관리 화면 **모델 → 팀별 모델 접근 관리**에서 8단계의 제한을 비웁니다. Mac 의 dev 클라이언트도 이것으로 원래대로
돌아옵니다.

**gateway-proxy 되돌리기** — values 백업을 되살리고 다시 배포합니다.

▶ **실행** · 배포 EC2 (bash) — values 복원 · 재배포

```bash
export AWS_DEFAULT_REGION=us-west-2
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
cp $V.bak-policyhelper $V
./deployment/scripts/install-eks.sh dev
```

급할 때는 `helm -n llm-gateway rollback llm-gateway 44` 가 더 빠릅니다. 다만 values 파일에는 새 태그가
남아 있어, 다음번 `install-eks.sh` 가 PoC 이미지를 다시 올립니다. 롤백한 뒤에도 values 백업은 꼭
되살립니다.

**Windows** — helper 연결을 끊으려면 1단계 가이드의 9. 되돌리기와 같습니다(관리형 설정 백업 복원).

## 11. 코드를 고친 뒤 다시 배포

PoC 브랜치에 수정이 올라오면 태그를 하나 올려 다시 배포합니다. 같은 태그로 덮어쓰지 않습니다.
아래 `OLD`·`NEW` 만 바꿔 씁니다. values 백업(`.bak-policyhelper`)은 처음 값(1.0.81)을 담고 있으니
다시 만들지 않습니다. **모두 배포 EC2 에 SSH 로 접속한 터미널에서** 실행합니다(Windows 가 아닙니다).

▶ **실행** · 배포 EC2 (bash) — PoC 브랜치를 최신으로

```bash
cd ~/sample-agentic-ai-acceleration-kr
git fetch origin feat/policy-helper-poc
git checkout feat/policy-helper-poc
git pull --ff-only
git log -1 --oneline
```

마지막 줄이 PoC 브랜치의 최신 커밋이면 됩니다(2026-09-26 기준 `3bebaf8 fix(gateway-proxy): /v1/policy 를 VK
인증 경로(DUAL)에 등록`).

▶ **실행** · 배포 EC2 (bash) — 태그 교체 · helm 렌더로 확인

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
OLD=1.0.82-policyhelper; NEW=1.0.83-policyhelper
V=deployment/charts/llm-gateway/values-eks-fargate-dev.yaml
grep -c "tag: \"$OLD\"" $V
sed -i "s/tag: \"$OLD\"/tag: \"$NEW\"/" $V
helm template t deployment/charts/llm-gateway -f $V | grep -oE 'gateway-proxy:[^"]+' | sort -u
```

`grep -c` 가 `1`, 마지막 줄이 `gateway-proxy:1.0.83-policyhelper` 면 됩니다.

▶ **실행** · 배포 EC2 (bash) — 이미지 빌드 · ECR 확인

```bash
cd ~/sample-agentic-ai-acceleration-kr/projects/awsome-ai-gateway
./deployment/scripts/rebuild-image.sh gateway-proxy dev
aws ecr describe-images --repository-name llm-gateway/gateway-proxy --image-ids imageTag=1.0.83-policyhelper --query 'imageDetails[0].imagePushedAt' --output text
```

올린 시각이 나오면 **5단계 → 6단계**(terraform output 확인 → 배포 → 확인 → lock 복원 → 브랜치
되돌리기)를 그대로 합니다. helper 가 바뀌지 않았다면 Windows 는 7단계 마지막 블록(VK 갱신 · helper
실행 · 캐시 확인)만 다시 합니다.

## 함정

| 증상 | 원인 | 조치 |
|---|---|---|
| 캐시 파일이 안 생김 | VK 만료 또는 다른 배포의 VK | 7단계처럼 api-key-helper 를 먼저 실행하고, stderr 의 이유를 봅니다 |
| stderr 에 `SSL` 오류 | 얼린 exe 의 HTTPS 모듈 문제 | Claude Code 는 기본값으로 뜹니다. stderr 전문을 알려 주세요 |
| `/model` 에 모든 모델이 보임 | 팀 제한이 다른 팀에 걸림 | 관리 화면에서 이 사용자의 팀을 확인 |
| exe 복사가 실패 | Claude Code 가 helper 를 실행 중 | Claude Code 창을 모두 닫고 다시 복사 |
| helper 가 `HTTP 401` — 같은 VK 로 질의는 됨 | 게이트웨이 `1.0.82` 의 인증 경로 누락 | 11절로 `1.0.83` 이상 배포 |
| api-key-helper 가 `cache_hit` 인데 401 | 캐시 적중은 서버 검증이 아님 | 같은 VK 로 질의가 되는지부터 확인 |
| `/status` 가 `(file + drop-ins)`, `/model` 이 안 걸러짐 | 관리형 설정에 `policyHelper` 블록이 없음(1단계 되돌리기를 돌리면 사라짐) | 1단계 가이드 6단계 블록을 다시 넣기 |
| 배포 뒤 rev 가 `failed` → `Rollback` | 3단계 빌드 없이 배포(이미지가 ECR 에 없음) | 3단계 ECR 확인 뒤 6단계 다시 |
