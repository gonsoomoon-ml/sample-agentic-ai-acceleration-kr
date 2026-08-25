# Cowork 설치기 빌드 — Windows (빌드 담당 관리자)

```
clone → packaging\site-config.json 채우기 → build.ps1
      → dist\installer\gateway-cli-cowork-setup-<ver>.exe
```

[관리자 E2E](cowork-installer-admin-e2e-windows.md) 의 1)~3) 단계. 결과물 파일 하나를 §5 대로 전달하면 끝난다.
빌드 자체는 🔵 **일반 PowerShell**(본인 계정)에서 한다 — 관리자 권한이 필요 없다. §0 의 사전 설치만 🔴 **관리자 PowerShell**.

## 0. 빌드 PC 준비 (1회)

세 가지가 있어야 한다. 하나씩 확인하고, 없으면 바로 아래 설치 명령을 🔴 **관리자 PowerShell** 에서 실행한다.

▶ **실행** · 빌드 PC — 🔴 관리자 PowerShell

```powershell
py --version
```

→ `3.11` 이상. 없으면:

▶ **실행 (없을 때만)** · 🔴 관리자 PowerShell

```powershell
winget install --id Python.Python.3.12 -e --scope machine
```

▶ **실행** · 🔴 관리자 PowerShell

```powershell
Test-Path "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

→ `True`. `False` 면:

▶ **실행 (없을 때만)** · 🔴 관리자 PowerShell

```powershell
winget install -e --id JRSoftware.InnoSetup
```

▶ **실행** · 🔴 관리자 PowerShell

```powershell
git --version
```

→ 버전이 찍혀야 한다. 없으면:

▶ **실행 (없을 때만)** · 🔴 관리자 PowerShell

```powershell
winget install --id Git.Git -e
```

무엇이든 새로 설치했으면 **PowerShell 창을 닫고 새로 연 뒤** 위 확인 3개를 다시 실행한다 — 새로 깐 프로그램의 경로는 그 뒤에 연 창부터 반영된다.

## 1. 소스 받기

▶ **실행** · 빌드 PC — 🔵 일반 PowerShell (새 창)

```powershell
cd $env:USERPROFILE
```

▶ **실행** · 🔵 일반 PowerShell

```powershell
git clone --depth 1 -b feat/cowork-installer-import https://github.com/gonsoomoon-ml/sample-agentic-ai-acceleration-kr.git
```

▶ **실행** · 🔵 일반 PowerShell

```powershell
cd sample-agentic-ai-acceleration-kr\projects\awsome-ai-gateway\cowork-installer\installer
```

이 폴더가 이후 모든 명령의 기준이다.

## 2. 값 채우기 — `packaging\site-config.json`



### (1) 값 얻기 — 배포 EC2 에서 (읽기 전용)

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash 07-client-values.sh
```

출력 4줄이 그대로 들어간다:


| 스크립트 출력              | `site-config.json` 키                              |
| -------------------- | ------------------------------------------------- |
| `OIDC_ISSUER_URL`    | `oidcIssuerUrl`                                   |
| `OIDC_CLIENT_ID`     | `oidcClientId`                                    |
| `ADMIN_API_URL`      | `adminApiUrl`                                     |
| `ANTHROPIC_BASE_URL` | `gatewayUrl` **와** `coworkGatewayHttpsUrl` (같은 값) |


모델 alias 는 같은 EC2 에서:

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway/docs/us-llm-gateway/update-scripts && bash 00-preflight-check.sh
```

출력 중 `--- model_aliases: ACTIVE ---` 절의 `alias` 열이 게이트웨이에 등록된 모델이다
(나머지 절 — routing_profiles·pricings·ALB 등 — 은 이 작업과 무관):

📋 **참고** — 위 명령의 **출력 예시**다 (`alias` 열만 쓴다)

```
--- model_aliases: ACTIVE ---
           alias           | provider |  provider_model_id
---------------------------+----------+---------------------
 claude-haiku-4-5-20251001 | bedrock  | us.anthropic.…
 claude-opus-5             | bedrock  | us.anthropic.…
 claude-sonnet-5           | bedrock  | us.anthropic.…
```

`alias` 값을 `coworkModels` 에 쉼표로 나열한다. 출력은 알파벳순이지만 **첫 항목이 앱의
기본 모델**이 되므로 원하는 순서로 적는다 (예: `claude-opus-5,claude-sonnet-5,claude-haiku-4-5-20251001`).
admin-ui **모델** 화면의 `ACTIVE` 목록과 같은 값이다.

### (2) 파일 작성 — 빌드 PC 에서

저장소에 없는 파일이다(커밋 금지). 새로 만들고 아래를 붙여 넣은 뒤 `<…>` 를 (1) 에서 얻은 값으로 바꾼다.

▶ **실행** · 빌드 PC — 🔵 일반 PowerShell (§1 의 installer 폴더)

```powershell
notepad packaging\site-config.json
```

📋 **참고** — notepad 에 **붙여넣을 내용**이다 (터미널에서 실행하는 게 아니다)

```json
{
  "oidcIssuerUrl": "https://cognito-idp.<REGION>.amazonaws.com/<USER_POOL_ID>",
  "oidcClientId": "<OIDC_CLIENT_ID>",

  "gatewayUrl": "https://<GATEWAY_HOST>",
  "adminApiUrl": "https://<ADMIN_API_HOST>",

  "coworkGatewayHttpsUrl": "https://<GATEWAY_HOST>",
  "coworkModels": "<ALIAS_1>,<ALIAS_2>,<ALIAS_3>"
}
```


| 키                       | 무엇                                     | 예                                                                 |
| ----------------------- | -------------------------------------- | ----------------------------------------------------------------- |
| `oidcIssuerUrl`         | Cognito user pool 의 issuer URL         | `https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEfGhI` |
| `oidcClientId`          | 그 풀의 app client id                     | `1a2b3c4d5e6f7g8h9i0j`                                            |
| `gatewayUrl`            | 게이트웨이 추론 엔드포인트                         | `https://gateway-dev.example.com`                                 |
| `adminApiUrl`           | admin-api (VK 발급)                      | `https://admin-api-dev.example.com`                               |
| `coworkGatewayHttpsUrl` | 앱이 쓰는 추론 base — 보통 `gatewayUrl` 과 같은 값 | `https://gateway-dev.example.com`                                 |
| `coworkModels`          | 게이트웨이에 등록된 모델 alias, 쉼표 구분 (첫 항목이 기본)  | `claude-opus-5,claude-sonnet-5,claude-haiku-4-5-20251001`         |




## 3. 빌드

▶ **실행** · 🔵 일반 PowerShell — installer 폴더

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

빌드 결과물:

- `dist\gateway-cli-cowork-suite\` — `gateway-cli-cowork.exe` · `api-key-helper.exe` · `_internal\` (§4 verify 용)
- `dist\installer\gateway-cli-cowork-setup-<ver>.exe` — **전달물** (§5)



## 4. 빌드 직후 확인 (설치 전)

▶ **실행** · 🔵 일반 PowerShell — installer 폴더

```powershell
dist\gateway-cli-cowork-suite\gateway-cli-cowork.exe verify
```

이 시점의 `overall` 실패는 정상이다 — `setup` 전이라 정책이 없다. 나머지 항목이 **주입한 값이 맞다는 증거**다. (실측 후 갱신)


| 검사                     | 기대         | 뜻                                 |
| ---------------------- | ---------- | --------------------------------- |
| `cowork-config`        | ✗          | `setup` 전 — 정상                    |
| `cowork-inference-url` | ✓ HTTP 401 | 주입된 게이트웨이 주소에 닿음(VK 없으니 401 이 정답) |
| `cowork-egress`        | ✓ HTTP 403 | `downloads.claude.ai` 도달          |
| `cowork-hklm`          | ✓          | HKLM 에 기존 정책 값이 없음                |




## 5. 전달

`dist\installer\gateway-cli-cowork-setup-<ver>.exe` 를 그 PC 의 관리자에게 전달 → [관리자 E2E](cowork-installer-admin-e2e-windows.md) §2.

---

