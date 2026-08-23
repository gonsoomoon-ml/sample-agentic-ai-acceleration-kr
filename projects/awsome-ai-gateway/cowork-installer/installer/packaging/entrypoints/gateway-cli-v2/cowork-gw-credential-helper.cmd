@echo off
REM ============================================================================
REM Claude Cowork Credential Helper (Windows) — REFERENCE ONLY, NOT PACKAGED
REM ============================================================================
REM 이 .cmd 는 더 이상 설치 payload 에 포함되지 않으며 앱에 등록되지 않습니다.
REM `gateway-cli cowork setup` 은 inferenceCredentialHelper 로 api-key-helper.exe
REM 의 전체 경로를 직접 등록합니다. api-key-helper.exe 는 이제:
REM   - baked 기본값으로 OIDC/ADMIN 환경변수를 폴백 (clean MSIX 환경 대응, 5.6)
REM   - stderr 로만 로그 출력 (stdout 은 VK 토큰 한 줄만 — stdout-leak fix)
REM 하므로 이 셸 래퍼(cmd.exe + findstr) 없이 단독으로 Codex #3 계약을 만족합니다.
REM
REM 이 파일은 수동 진단/레거시 참고용으로만 남겨둡니다.
REM ----------------------------------------------------------------------------
REM Cowork 앱이 이 스크립트를 주기적으로 실행하여 Virtual Key를 받아갑니다.
REM stdout에 VK 한 줄만 출력합니다 (Cowork가 Authorization: Bearer <VK> 로 사용).
REM
REM 사전 조건:
REM   1. gateway-cli login 완료 (~/.gateway-cli/oidc-tokens.json 존재)
REM   2. 아래 환경변수가 설정되어 있어야 합니다 (설치 시 자동 설정됨)
REM
REM 설치: 이 파일을 C:\gateway-cli\ 에 복사
REM ============================================================================

REM --- 환경변수 설정 (고객 환경에 맞게 수정) ---
if not defined OIDC_ISSUER_URL set "OIDC_ISSUER_URL=OIDC_ISSUER_URL_HERE"
if not defined OIDC_CLIENT_ID set "OIDC_CLIENT_ID=OIDC_CLIENT_ID_HERE"
if not defined ADMIN_API_URL set "ADMIN_API_URL=http://ADMIN_API_ALB_HERE"

REM --- api-key-helper 경로 찾기 ---
set "HELPER="

REM gateway-cli 설치 경로 (Windows installer 기본)
if exist "C:\gateway-cli\api-key-helper.exe" (
    set "HELPER=C:\gateway-cli\api-key-helper.exe"
    goto :found
)

REM Program Files 경로
if exist "%ProgramFiles%\gateway-cli\api-key-helper.exe" (
    set "HELPER=%ProgramFiles%\gateway-cli\api-key-helper.exe"
    goto :found
)

REM PATH에서 찾기
where api-key-helper.exe >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where api-key-helper.exe') do set "HELPER=%%i"
    goto :found
)

REM 못 찾은 경우
echo credential-helper: api-key-helper.exe를 찾을 수 없습니다. 1>&2
echo gateway-cli가 설치되어 있는지 확인하세요. 1>&2
exit /b 127

:found

REM --- VK 발급/조회 ---
REM --auth-mode oidc로 OIDC 모드 강제. stdout에서 vk- 로 시작하는 줄만 추출.
for /f "tokens=*" %%v in ('"%HELPER%" --auth-mode oidc 2^>nul ^| findstr /b "vk-"') do (
    echo %%v
    exit /b 0
)

REM VK 획득 실패
echo credential-helper: Virtual Key 획득 실패 1>&2
echo 'gateway-cli login' 실행 후 다시 시도하세요. 신규 발급은 사내 VPN이 필요합니다. 1>&2
exit /b 1
