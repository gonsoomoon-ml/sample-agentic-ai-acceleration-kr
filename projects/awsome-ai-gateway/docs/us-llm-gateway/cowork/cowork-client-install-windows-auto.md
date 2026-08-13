# Cowork 설치기 — Windows 자동 설치 가이드

---

## 한눈에 — 두 파트

```
Part A  운영자      설치기를 만든다      값 채우기 → build.ps1 → 산출물 전달
Part B  최종 사용자  받아서 설치하고 쓴다  Readiness → 설치 → login/setup/verify → 앱
```

[수동 설치 가이드](cowork-client-install-windows.md)와 **같은 결과**를 만든다. 차이는 Python·git·uv 를
직접 깔고 명령을 하나씩 치는 대신, **런타임이 통째로 번들된 exe** 를 만들어 쓰는 것이다.
최종 PC 에는 Python 도 인터넷도 필요 없다.

> ⚠️ **빌드 코드(`cowork-installer/`)는 이 저장소에 아직 없다.** 운영자에게 따로 받아
> `C:\cowork-build\installer\` 에 풀어 두었다고 가정하고 Part A 를 읽는다.

⚠️ **B-1 을 먼저 하십시오.** Cowork 는 격리된 가상 환경에서 돌기 때문에, 안 되는 PC 라면
나머지를 다 마쳐도 앱이 안 켜진다.

### 명령을 어디서 실행하는가


| 표시                           | 어느 창인가                                           | 왜 나뉘나                           |
| ---------------------------- | ------------------------------------------------ | ------------------------------- |
| ▶ 🔵 **실행 · 일반 PowerShell**  | Windows 에서 그냥 연 PowerShell                       | 빌드 산출물과 venv 가 **본인 폴더**에 들어갑니다 |
| ▶ 🔴 **실행 · 관리자 PowerShell** | **"관리자 권한으로 실행"** 으로 연 창 (제목 표시줄에 `관리자:` 가 보입니다) | 정책 키를 씁니다                       |


⚠️ **🔴 는 반드시 본인 계정으로 로그인한 세션에서 열어야 합니다.** `HKCU\SOFTWARE\Policies\Claude` 는
호출자 SID 의 하이브에 기록되므로, SSM·SYSTEM·다른 관리자 계정으로 실행하면 엉뚱한 곳에 써서
**명령은 성공하는데 앱이 아무것도 못 찾습니다.**

---



# Part A. 운영자 — 설치기 만들기

> 배포 좌표를 아는 사람만 할 수 있다. 최종 사용자는 이 파트를 읽지 않아도 된다 —
> 산출물(`gateway-cli-cowork-suite` 폴더)만 받으면 Part B 로 바로 간다.

## A-1. 값 — US ( 아래는 값의 예 )


| 항목            | 값                                                                                     |
| ------------- | ------------------------------------------------------------------------------------- |
| Cognito pool  | `us-west-2_AbCdEfGhI` (`llm-gateway-dev-userpool`)                                    |
| OIDC issuer   | `https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEfGhI`                     |
| OIDC client   | `<OIDC_CLIENT_ID>` (`llm-gateway-dev-cli`)                                  |
| CloudFront    | `<CF_DIST_ID>` → `https://xxx.cloudfront.net` (origin = gateway ALB)      |
| gateway ALB   | `<GATEWAY_ALB_DNS>`             |
| admin-api ALB | `<ADMIN_API_ALB_DNS>`             |
| 모델 alias      | `claude-opus-5` · `claude-opus-4-8` · `claude-sonnet-5` · `claude-haiku-4-5-20251001` |


`packaging/site-config.json` — 이 파일 하나가 exe 에 값을 박아 넣는다. `.gitignore` **대상이라
저장소에 없다**(직접 만든다). 예시 파일도 없다.

```json
{
  "oidcIssuerUrl": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEfGhI",
  "oidcClientId": "<OIDC_CLIENT_ID>",

  "gatewayUrl": "https://xxx.cloudfront.net",
  "adminApiUrl": "http://<ADMIN_API_ALB_DNS>",
  "caBundle": "",

  "coworkGatewayHttpsUrl": "https://xxx.cloudfront.net",
  "coworkModels": "claude-opus-5,claude-opus-4-8,claude-sonnet-5,claude-haiku-4-5-20251001",

  "orgUuid": "",
  "expectedCaSha256": ""
}
```

URL 3종이 서로 다른 곳에 쓰인다 — 헷갈리면 안 된다.


| 키                       | 쓰이는 곳                                                                  |
| ----------------------- | ---------------------------------------------------------------------- |
| `coworkGatewayHttpsUrl` | `inferenceGatewayBaseUrl` — 앱의 추론 base. **공개 신뢰 HTTPS 필수**(앱이 HTTP 거부) |
| `gatewayUrl`            | env `ANTHROPIC_BASE_URL`                                               |
| `adminApiUrl`           | env `ADMIN_API_URL` + `GATEWAY_CLI_GATEWAY_URL` — **VK 발급**            |


- `caBundle` 빈 값 = 사내 TLS 프록시 없음 → `setup` 이 CA 설치를 자동으로 건너뛴다(정상).
- `expectedCaSha256` 빈 값 = CA 지문 핀 없음(dev 빌드).



## A-2. 빌드

▶ 🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
cd C:\cowork-build\installer
```

▶ 🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

진행: `.build-venv` 생성 → pip 설치 → `site-config.json` 로드 → `_site_config.py` 생성 →
PyInstaller → 각 exe `--help` smoke test → (자격증명 있으면) 서명 → Inno Setup 컴파일.
**smoke test 가 실패하면 빌드가 거기서 멈춘다.**

산출물:

```
dist\gateway-cli-cowork-suite\      gateway-cli-cowork.exe · api-key-helper.exe · _internal\
dist\installer\gateway-cli-cowork-setup-<version>.exe     ← Inno Setup 이 있을 때만
```



## A-3. 빌드 직후 확인

값이 제대로 박혔는지 **설치 전에** 본다.

▶ 🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
dist\gateway-cli-cowork-suite\gateway-cli-cowork.exe verify
```

⚠️ **이 시점의** `overall: fail` **은 정상이다.** `setup` 을 아직 안 했으니 `cowork-config` 가
실패하는 것이 맞다. 나머지 4개가 **주입된 값이 옳다는 증거**다 — 2026-08-12 실측:


| 검사                     | 기대값            | 읽는 법                                                                |
| ---------------------- | -------------- | ------------------------------------------------------------------- |
| `cowork-config`        | ✗ fail         | `setup` 미실행 — 이 단계에선 정상                                             |
| `cowork-hklm`          | ✓ ok           | HKLM 이 비어 있음 — 값이 있으면 앱이 HKCU 를 통째로 무시한다                            |
| `cowork-inference-url` | ✓ **HTTP 401** | 주입된 CloudFront 주소가 게이트웨이에 닿았다. VK 가 없으니 401 이 정답 — 주소가 틀리면 타임아웃/404 |
| `cowork-egress`        | ✓ HTTP 403     | `downloads.claude.ai` 도달                                            |
| `cowork-ca`            | ! warn         | `caBundle` 빈 값이라 CA PEM 없음 — 의도대로                                   |


> ⚠️ 벤더 메시지가 `run 'gateway-cli setup'` 이라고 안내하지만 이 빌드의 실행 파일은
> `gateway-cli-cowork` 다. 그대로 치면 명령을 못 찾는다.




여기까지가 운영자의 몫이다. `dist\gateway-cli-cowork-suite\` 폴더를 최종 사용자에게 전달한다.

---

# Part B. 최종 사용자 — 설치하고 쓰기

> 운영자에게 받은 `gateway-cli-cowork-suite` 폴더가 있으면 시작할 수 있다.
> 값을 채우거나 빌드할 일은 없다.

## B-1. Cowork 실행 가능 여부 확인

**브라우저**로 아래 주소를 열어 점검 도구를 받는다(약 2 MB).

```
https://claude.ai/api/desktop/win32/x64/cowork-readiness-check/latest/redirect
```

Arm 프로세서 PC(Snapdragon 계열)라면 이쪽이다. 모르면 `설정 → 시스템 → 정보`의 「시스템 종류」를 본다.

```
https://claude.ai/api/desktop/win32/arm64/cowork-readiness-check/latest/redirect
```

받은 파일을 실행하면 항목별 통과 여부가 나온다.

```
This computer is ready for Cowork              ← 이 문장이 나오면 B-2 로
This computer does not meet the requirements for Cowork
```

**통과하지 못했다면** 도구가 어느 항목이 왜 막혔는지와 고치는 명령까지 알려준다. 자주 걸리는 둘:

```
Hardware virtualization    펌웨어(BIOS/UEFI)에서 가상화가 꺼져 있음
Virtual Machine Platform   Windows 기능이 안 켜져 있음
```

**고친 뒤에는 반드시 재부팅하고 다시 실행한다.** 기능만 켜고 확인하면 여전히 실패로 나온다.

⚠️ **일반 가상 머신 위에서는 대개 실패한다.** 가상화 기능을 손님에게 넘기지 않는 환경에서는
통과할 수 없다. 

---

## B-2. 설치

`dist\gateway-cli-cowork-suite\` 를 **고정 위치로 옮기고** PATH 에 넣는다.

🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
Copy-Item C:\cowork-build\installer\dist\gateway-cli-cowork-suite C:\GatewayCLI-Cowork -Recurse -Force
```

▶ 🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
[Environment]::SetEnvironmentVariable("PATH", ([Environment]::GetEnvironmentVariable("PATH","User") + ";C:\GatewayCLI-Cowork"), "User")
```

**새 창**을 열어야 PATH 가 잡힌다. 확인:

▶ 🔵 **실행 · 일반 PowerShell (새 창)** — 본인 PC

```powershell
where.exe gateway-cli-cowork; where.exe api-key-helper
```

둘 다 `C:\GatewayCLI-Cowork\` 로 나오면 된다.

## B-3. 연결 — login → setup → verify

세 명령 모두 **본인 계정의 관리자 창**에서 실행한다.

### ① 로그인

▶ 🔴 **실행 · 관리자 PowerShell** — 본인 PC

```powershell
gateway-cli-cowork login --redirect-port 8091
```

브라우저에 Cognito 로그인 화면이 뜬다. 운영자가 준 이메일·비밀번호를 넣는다.
**포트는 8090·8091·8092 중에서만** 골라야 한다 — US Cognito client 에 등록된 콜백이 그 3개뿐이다.

⚠️ **`Login failed` 와 HTTP 오류 코드가 뜬다면** — Cognito 인증까지는 끝났고 그다음
**VK 발급**에서 깨진 것이다. 브라우저에 뜬 페이지 자체는 정상 응답이고, 화면에 보이는
코드는 `adminApiUrl` 쪽에서 돌아온 값을 그대로 옮겨 적은 것이다.

▶ 🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
dir "$env:LOCALAPPDATA\gateway-cli-cowork"
```

`oidc-tokens.json` 만 있고 `vk-cache.json` 이 없으면 확정이다. 이때는 **`adminApiUrl` 부터
확인한다** — `gateway-cli-cowork verify` 의 `Admin API:` 줄. 주소가 틀리면 게이트웨이가 아닌
엉뚱한 대상이 응답해 5xx 로 보인다(2026-08-12 실제 사례).

### ② 게이트웨이 연결

▶ 🔴 **실행 · 관리자 PowerShell** — 본인 PC

```powershell
gateway-cli-cowork setup
```

`setup` 이 하는 일 — 사내 CA 설치(PEM 없으면 건너뜀) → 관리 설정을
`HKCU\SOFTWARE\Policies\Claude` 에 기록 → Claude Desktop 자동 재시작.
설정은 앱 **실행 시점에만** 읽히므로 재시작이 필요하다(`--no-relaunch` 로 생략 가능).

중간에 실패하면 이번 실행이 만든 변경만 자동 원복하고 원래 오류를 그대로 보고한다.

> 앱이 아직 없어도 `setup` 은 정상 성공하고 자동 재시작만 건너뛴다(2026-08-12 실측):
> `Auto-relaunch skipped: Claude Desktop MSIX package not found`



### ③ 확인

▶ 🔴 **실행 · 관리자 PowerShell** — 본인 PC

```powershell
gateway-cli-cowork verify
```

`overall: warn` **이 이 배포의 정상 종착 상태다** — 사내 프록시가 없어 `cowork-ca` 만 warn 이고
나머지 6개는 ok 다. 2026-08-12 실측:

```
[✓] cowork-config        provider=gateway, 6 inference keys set
[✓] cowork-hklm          no HKLM precedence conflict
[✓] cowork-inference-url reachable (HTTP 401)
[✓] cowork-egress        reachable (HTTP 403)
[!] cowork-ca            corporate CA PEM not found   ← 이 환경에선 정상
[✓] oidc-tokens          valid
[✓] vk-cache             valid
```

기록된 관리 설정 6개 키(A-1 의 주입값과 일치해야 한다):

```
inferenceProvider            = gateway
inferenceGatewayBaseUrl      = https://xxx.cloudfront.net
inferenceGatewayAuthScheme   = bearer
inferenceCredentialKind      = helper-script
inferenceCredentialHelper    = C:\GatewayCLI-Cowork\api-key-helper.exe
inferenceModels              = ["claude-opus-5","claude-opus-4-8","claude-sonnet-5","claude-haiku-4-5-20251001"]
```

> `inferenceModels` 는 **JSON 배열을 통째로 담은** `REG_SZ` **문자열**이다(`REG_MULTI_SZ` 아님).
> 수동 설치 가이드에서 검증된 형식과 같다.

---



## B-4. Claude Desktop 설치

먼저 이미 있는지 본다.

▶ 🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
Get-AppxPackage -Name "*laude*" | Select-Object Name, Version
```

출력이 있으면 B-5 로 간다. 비어 있으면 **브라우저**로 아래 주소를 열어 약 1.8 GB 를 받는다.

```
https://claude.ai/api/desktop/win32/x64/offline/latest/redirect
```

⚠️ **반드시 이 offline 주소의** `.msix` **를 받는다.** `claude.com/download` 는 `.exe` 를 주는데,
그것으로 깔면 Claude Desktop 은 설치되지만 **Cowork 가 빠진다.** offline 판만 Cowork 가 들어
있고 작업 환경 번들도 파일 안에 있어 설치 중 추가 다운로드가 없다.

⚠️ **404 가 나면 잠시 뒤 다시 받는다.** 새 버전 직후 offline 판이 아직 안 올라온 구간이 있고,
이때는 이전 버전으로 물러나지 않고 404 로 실패한다(벤더 문서에 명시된 동작). 받아 둔 설치
파일이 있으면 버리지 말 것.

▶ 🔵 **실행 · 일반 PowerShell** — 본인 PC

```powershell
Add-AppxPackage -Path "$env:USERPROFILE\Downloads\Claude-offline-win32-x64-1.24012.11.msix"
```

1.77 GB 라 몇 분 걸린다. 진행 표시가 없어도 정상이고, 성공하면 아무 출력이 없다.
확인은 위 `Get-AppxPackage` 로 — 2026-08-12 실측: `Claude 1.24012.11.0`.

> ⚠️ MSIX 는 **사용자별 설치**다. 본인 계정 세션에서 실행해야 그 계정에 깔린다.
> SSM·SYSTEM 으로 돌리면 엉뚱한 계정에 설치된다.



## B-5. 실행하고 대화

1. Claude Desktop 을 열고 **Cowork 탭** → 모델 선택기에 A-1 의 alias 4개가 보이는지 확인.
2. 아무 모델이나 골라 `hi` 를 보낸다. 응답이 오면
  **PC → CloudFront → gateway-proxy → Bedrock(US Geo)** 이 전부 통한 것이다.



## B-6. 되돌리기

범위가 작은 것부터 고른다.


| 목적                        | 명령                                           | 창   |
| ------------------------- | -------------------------------------------- | --- |
| 설정만 원복 (CA 는 둠)           | `gateway-cli-cowork disable`                 | 🔵  |
| CA 신뢰만 원복                 | `gateway-cli-cowork ca restore`              | 🔵  |
| 설정·CA·토큰 전부 정리 (바이너리는 남김) | `gateway-cli-cowork clear`                   | 🔵  |
| 바이너리 제거                   | *앱 및 기능* 또는 `gateway-cli-cowork uninstall`   | 🔴  |
| **전부 제거**                 | `gateway-cli-cowork uninstall --clear-first` | 🔴  |


`clear` 는 확인 프롬프트가 뜬다 — `-y` 로 생략, `--dry-run` 으로 미리보기,
`--keep-ca`/`--keep-tokens` 로 일부 보존.

`setup` 은 파일 수정 **전에** 항상 스냅샷을 남긴다 →
`C:\Users\<user>\AppData\Local\gateway-cli\backups\`


