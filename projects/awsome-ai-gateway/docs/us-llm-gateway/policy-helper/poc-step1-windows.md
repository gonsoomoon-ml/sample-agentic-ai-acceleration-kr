# policyHelper PoC 1단계 — Windows 에서 빌드·설치·연결·확인

- 작성일: 2026-09-26
- 대상: dev Windows 머신(891)에서 PoC 1단계를 직접 실행하는 사람
- 목표: 서버 없이 **기본값만 내보내는 policy-helper** 를 Claude Code 에 연결하고, 동작이 지금과
  똑같은지 확인합니다.

## 0. 시작 전 확인

이 머신에는 US-14 설치가 끝나 있어야 합니다. 관리자 PowerShell 에서 확인합니다.

▶ **실행** · 🔴 관리자 PowerShell

```powershell
gateway-cli version
Test-Path 'C:\Program Files\ClaudeCode\managed-settings.json'
claude --version
py -3 --version
```

- `gateway-cli 0.2.0` · `True` · `2.1.280` 이상 · `Python 3.11` 이상이 나와야 합니다.
- `gateway-cli login` 도 한 번 되어 있어야 합니다(마지막 단계의 질의 확인에 필요).

## 1. 소스 받기

▶ **실행** · 🔵 일반 PowerShell

```powershell
cd C:\build
git clone --depth 1 -b feat/policy-helper-poc https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git ph-src
cd C:\build\ph-src\projects\awsome-ai-gateway\policy-helper
```

`C:\build` 에는 US-14 설치기 소스가 이미 있으므로 폴더 이름을 `ph-src` 로 따로 둡니다.

## 2. 테스트 (선택)

Windows 에서도 helper 의 약속(항상 JSON 하나, 항상 종료 코드 0)이 지켜지는지 확인합니다.

▶ **실행** · 🔵 일반 PowerShell

```powershell
py -3 -m pip install --user --quiet pytest
py -3 -m pytest -q
```

`15 passed` 가 나오면 됩니다.

## 3. 기본값 파일(baseline.json) 만들기

helper 가 내보낼 기본값입니다. **지금 적용 중인 관리형 설정을 그대로 옮겨** 동작이 바뀌지 않게
합니다. gateway-cli 의 표시용 키 `_gatewayCli` 만 뺍니다 — helper 출력에 모르는 키가 섞이면
Claude Code 가 시작을 거부하기 때문입니다.

▶ **실행** · 🔵 일반 PowerShell

```powershell
$m = Get-Content 'C:\Program Files\ClaudeCode\managed-settings.json' -Raw | ConvertFrom-Json
$m.PSObject.Properties.Remove('_gatewayCli')
[IO.File]::WriteAllText('C:\build\baseline.json', ($m | ConvertTo-Json -Depth 10))
Get-Content C:\build\baseline.json -Raw
```

`env`·`apiKeyHelper`·`statusLine` 이 보이고 `_gatewayCli` 가 없으면 됩니다. 이 파일에는 사내 주소가
들어 있으므로 저장소에 커밋하지 않습니다.

## 4. 빌드

▶ **실행** · 🔵 일반 PowerShell

```powershell
cd C:\build\ph-src\projects\awsome-ai-gateway\policy-helper
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Baseline C:\build\baseline.json
```

마지막 줄에 `OK  ...\dist\policy-helper\policy-helper.exe` 가 나오면 성공입니다. 스크립트가 빌드한
exe 를 한 번 실행해 봉투가 나오는지까지 확인합니다.

## 5. 설치 — 관리자만 쓸 수 있는 폴더로

helper 실행 파일을 사용자가 바꿀 수 있으면 정책을 우회할 수 있습니다. 그래서 `C:\Program Files` 아래에
둡니다.

▶ **실행** · 🔴 관리자 PowerShell

```powershell
cd C:\build\ph-src\projects\awsome-ai-gateway\policy-helper
New-Item -ItemType Directory -Force 'C:\Program Files\PolicyHelper' | Out-Null
Copy-Item .\dist\policy-helper\* 'C:\Program Files\PolicyHelper' -Recurse -Force
icacls 'C:\Program Files\PolicyHelper'
```

`BUILTIN\Users` 가 `(RX)`(읽기·실행)만 가지고 있으면 됩니다. `(M)`·`(F)`·`(W)` 가 보이면 멈추고
알려 주세요.

설치한 exe 를 직접 실행해 봅니다.

▶ **실행** · 🔵 일반 PowerShell

```powershell
& 'C:\Program Files\PolicyHelper\policy-helper.exe'
```

`{"managedSettings": {...}}` 한 줄이 나오면 됩니다.

## 6. Claude Code 에 연결 — `policyHelper` 블록 추가

먼저 지금 파일을 백업합니다. 되돌릴 때 이 파일을 씁니다.

▶ **실행** · 🔴 관리자 PowerShell

```powershell
$f = 'C:\Program Files\ClaudeCode\managed-settings.json'
Copy-Item $f "$f.bak-poc" -Force
```

`policyHelper` 블록을 넣습니다. 기존 키는 그대로 둡니다.

▶ **실행** · 🔴 관리자 PowerShell

```powershell
$m = Get-Content $f -Raw | ConvertFrom-Json
$exe = 'C:\Program Files\PolicyHelper\policy-helper.exe'
$ph = [ordered]@{ path = $exe; timeoutMs = 10000; refreshIntervalMs = 300000 }
$m | Add-Member -NotePropertyName policyHelper -NotePropertyValue $ph -Force
[IO.File]::WriteAllText($f, ($m | ConvertTo-Json -Depth 10))
```

확인합니다.

▶ **실행** · 🔴 관리자 PowerShell

```powershell
(Get-Content $f -Raw | ConvertFrom-Json).policyHelper
```

`path`·`timeoutMs`·`refreshIntervalMs` 세 줄이 나오면 됩니다. `policyHelper` 는 **Claude Code 를 새로
시작할 때부터** 적용됩니다.

## 7. 확인 — 통과 기준

**① Claude Code 가 helper 를 쓰는지**

▶ **실행** · 🔵 일반 PowerShell — 새 창에서

```powershell
claude
```

Claude Code 안에서 `/status` 를 입력합니다. 관리형 설정의 출처(Setting sources)에 helper 가 표시되면
됩니다.

**② 질의가 게이트웨이를 타는지** — `hi` 를 한 번 보냅니다. 응답이 오면 알려 주세요. 게이트웨이
기록(`usage_logs`)에 `claude-code` 행이 생겼는지 확인해 드립니다.

**③ Cowork 가 계속 열리는지** — Cowork 를 완전히 종료(작업 표시줄 트레이 포함)했다가 다시 열고 메시지를
하나 보냅니다. 이 PC 의 Cowork 도 Claude Code 관리형 설정을 읽기 때문에 확인합니다.

Cowork 를 끌 때는 앱만 종료합니다. 백그라운드 서비스 `CoworkVMService` 까지 멈추면 "workspace service
isn't running" 경고가 뜹니다. 그때는 관리자 PowerShell 에서 `Start-Service CoworkVMService` 로 되살립니다.

## 8. 대체 경로 확인 (선택)

기본값 파일이 없어도 Claude Code 가 **정상 시작**하는지 봅니다. helper 는 `{}` 를 내보내고, 그러면
파일에 남아 있는 정적 설정이 그대로 적용됩니다.

**① 기본값 파일을 치웁니다.**

▶ **실행** · 🔴 관리자 PowerShell

```powershell
$b = 'C:\Program Files\PolicyHelper\baseline.json'
Rename-Item $b baseline.json.off
& 'C:\Program Files\PolicyHelper\policy-helper.exe'
```

stderr 에 `baseline not found` 가 나오고 stdout 은 `{}` 입니다.

**② 되돌리기 전에 Claude Code 를 띄웁니다.**

▶ **실행** · 🔵 일반 PowerShell — 새 창에서

```powershell
claude
```

시작 거부 없이 뜨고, `/status` 의 Setting sources 에 `Enterprise managed settings (file + drop-ins)` 가
보이고, `hi` 에 응답이 오면 통과입니다. `/exit` 로 닫습니다.

**③ 기본값 파일을 되돌립니다.**

▶ **실행** · 🔴 관리자 PowerShell

```powershell
Rename-Item "$b.off" baseline.json
```

## 9. 되돌리기

백업해 둔 파일로 돌아가면 끝입니다. 다음번 Claude Code 실행부터 지금의 정적 설정으로 돌아옵니다.

▶ **실행** · 🔴 관리자 PowerShell

```powershell
$f = 'C:\Program Files\ClaudeCode\managed-settings.json'
Copy-Item "$f.bak-poc" $f -Force
```

▶ **실행** · 🔵 일반 PowerShell — 새 창에서

```powershell
claude
```

`/status` 의 Setting sources 에서 `(helper)` 가 사라지고 `Enterprise managed settings (file + drop-ins)` 가
보이면 되돌리기가 끝난 것입니다.

helper 폴더(`C:\Program Files\PolicyHelper`)는 남겨 둬도 됩니다. 연결이 풀리면 실행되지 않습니다.

## 함정

| 증상 | 원인 | 조치 |
|---|---|---|
| Claude Code 가 시작 자체를 거부 | helper 파일이 없거나 경로 오타 | 9. 되돌리기 → 경로 확인 후 다시 6 |
| `/status` 에 helper 가 안 보임 | Claude Code 를 새로 띄우지 않음 | 창을 닫고 새 창에서 `claude` |
| helper 가 `{}` 만 냄 | baseline 이 없거나 `_` 로 시작하는 키가 있음 | exe 를 직접 실행해 stderr 확인 |
| 빌드 중 `py` 를 못 찾음 | Python 설치 때 py 런처가 빠짐 | python.org 설치본으로 재설치 |
