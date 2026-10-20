# policyHelper PoC 3단계 — 서버에 닿지 않을 때의 대체 경로 확인 (Windows)

- 작성일: 2026-09-26
- 대상: 2단계까지 마친 dev Windows 머신(891)에서 확인하는 사람
- 목표: 게이트웨이에 닿지 않아도 Claude Code 가 **시작을 거부하지 않고**, 캐시가 있으면 마지막 정책으로,
  없으면 기본값으로 뜨는지 확인합니다.

## 0. 시작 전 확인

2단계의 팀 제한(opus-5 제외)이 걸려 있고, helper 캐시에 그 정책이 저장돼 있어야 합니다.

▶ **실행** · 🔵 일반 PowerShell — 캐시에 팀 제한이 들어 있는지 확인

```powershell
Get-Content "$env:LOCALAPPDATA\policy-helper\policy-cache.json" -Raw
```

`"availableModels"` 가 보이면 됩니다. 없으면 2단계 8단계를 먼저 합니다.

Claude Code 창은 모두 닫습니다.

## 1. 게이트웨이 막기 — hosts 파일

게이트웨이 주소를 이 PC 에서만 `127.0.0.1` 로 돌립니다. 연결이 곧바로 거절되므로 helper 는 기다리지
않고 대체 경로로 갑니다. **이 동안에는 Claude Code 질의도 게이트웨이에 닿지 않습니다.**

▶ **실행** · 🔴 관리자 PowerShell — hosts 백업 · 게이트웨이 막기

```powershell
$h = "$env:SystemRoot\System32\drivers\etc\hosts"
Copy-Item $h "$h.bak-policyhelper" -Force
Add-Content -Path $h -Value "`r`n127.0.0.1 gateway-dev.awsome-ai-gw.click"
ipconfig /flushdns | Out-Null
(Test-NetConnection gateway-dev.awsome-ai-gw.click -Port 443 -WarningAction SilentlyContinue).RemoteAddress
```

마지막 줄이 `127.0.0.1` 이면 막힌 것입니다.

## 2. 캐시로 시작하는지

▶ **실행** · 🔵 일반 PowerShell — helper 를 직접 실행

```powershell
& 'C:\Program Files\PolicyHelper\policy-helper.exe'
```

stderr 에 `policy fetch failed … using cached policy` 가 나오고, 출력 끝에 여전히 `"availableModels"` 가
있으면 됩니다.

▶ **실행** · 🔵 일반 PowerShell — 새 창에서 · Claude Code 실행

```powershell
claude
```

- 시작 거부 없이 뜨는지
- `/status` — `Enterprise managed settings (helper)` 인지
- `/model` — 2단계와 같이 Fable 이 숨겨져 있는지(캐시의 정책)

`hi` 는 게이트웨이가 막혀 있어 실패하는 것이 정상입니다. `/exit` 로 닫습니다.

## 3. 캐시도 없을 때 — 기본값으로 시작하는지

▶ **실행** · 🔵 일반 PowerShell — 캐시 치우기 · helper 실행

```powershell
$c = "$env:LOCALAPPDATA\policy-helper\policy-cache.json"
Rename-Item $c policy-cache.json.off
& 'C:\Program Files\PolicyHelper\policy-helper.exe'
```

stderr 에 `no cached policy yet; baseline only` 가 나오고, 출력에 `"availableModels"` 가 **없으면** 됩니다.

▶ **실행** · 🔵 일반 PowerShell — 새 창에서 · Claude Code 실행

```powershell
claude
```

- 시작 거부 없이 뜨는지
- `/model` — 제한이 풀려 Fable 이 다시 보이는지(기본값에는 모델 제한이 없음)

`/exit` 로 닫습니다.

## 4. 되돌리기

▶ **실행** · 🔴 관리자 PowerShell — hosts 복원

```powershell
$h = "$env:SystemRoot\System32\drivers\etc\hosts"
Copy-Item "$h.bak-policyhelper" $h -Force
ipconfig /flushdns | Out-Null
(Test-NetConnection gateway-dev.awsome-ai-gw.click -Port 443 -WarningAction SilentlyContinue).TcpTestSucceeded
```

`True` 면 게이트웨이에 다시 닿습니다.

▶ **실행** · 🔵 일반 PowerShell — 캐시 복원 · 서버에서 다시 받기

```powershell
$c = "$env:LOCALAPPDATA\policy-helper\policy-cache.json"
if (Test-Path "$c.off") { Remove-Item "$c.off" }
& 'C:\Program Files\PolicyHelper\policy-helper.exe' | Out-Null
Get-Content $c -Raw
```

캐시가 새로 생기고 `source` 가 게이트웨이 주소면 원래 상태입니다. 치운 캐시는 서버에서 다시 받으므로
지워도 됩니다.

## 통과 기준

| 상황 | 기대 결과 |
|---|---|
| 게이트웨이 막힘 + 캐시 있음 | 시작함 · `/model` 이 캐시의 정책대로 제한됨 |
| 게이트웨이 막힘 + 캐시 없음 | 시작함 · 제한 없음(기본값) |
| 어느 경우든 | **시작 거부 0회** |
