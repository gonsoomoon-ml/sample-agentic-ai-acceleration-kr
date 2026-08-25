# Cowork (Claude Desktop) 클라이언트 문서 — US 배포

Cowork 를 게이트웨이에 붙이는 방법은 두 갈래다. **지금 새로 배포한다면 설치기(installer) 계열**을 읽는다.

| 갈래 | 방식 | 상태 |
|---|---|---|
| **설치기** | 빌드한 `setup.exe` 하나로 CLI 배포 → 관리자가 정책 적용(HKLM) | **권장** |
| 수동 | 대상 PC 마다 Python·pip 로 CLI 설치 후 개인이 설정 | 레거시 — 설치기 도입 전 PC 정리용 |

## 설치기 계열 (Windows)

| 문서 | 읽는 사람 | 무엇 |
|---|---|---|
| [관리자 End-to-End](installer/cowork-installer-admin-e2e-windows.md) | 배포 관리자 | **여기서 시작** — 전체 흐름, 빌드 전 결정, 배포, 정책 적용, 검증, 탭 구성 |
| [빌드](installer/cowork-installer-build-windows.md) | 빌드 담당 관리자 | 값 채우기 → `build.ps1` → `gateway-cli-cowork-setup-<ver>.exe` |
| [사용자 설치·사용](installer/cowork-installer-user-windows.md) | 최종 사용자 | 적합성 확인 → Claude Desktop 설치 → `login` → 사용 → `verify` |
| [되돌리기·제거](installer/cowork-installer-uninstall-windows.md) | 관리자 | 설정만 원복(1st-party 전환) / 전부 제거 |

## 수동 설치 계열 (레거시)

| 문서 | 읽는 사람 | 무엇 |
|---|---|---|
| [Windows 수동 설치](manual/cowork-client-install-windows.md) | 운영자·직원 | Python·pip 로 CLI 설치 후 개인 PC 설정 (본편) |
| [Windows 자동 설치](manual/cowork-client-install-windows-auto.md) | 운영자 | 2026-08-12 판 설치기 기준 — **설치기 계열로 대체됨** |
| [Windows 수동 제거](manual/cowork-client-uninstall-windows-manual.md) | 운영자 | 수동 설치분 정리. 설치기로 넘어가기 전 §1·§2·§5·§6 만 적용 |

## macOS

| 문서 | 읽는 사람 | 무엇 |
|---|---|---|
| [macOS 설치](cowork-client-install-macos.md) | Mac 사용자 | 관리형 프로파일 기반 설정 (설치기는 Windows 전용) |

---

- 설치기 소스는 upstream 머지 전까지 fork 브랜치 `feat/cowork-installer-import` 의 `cowork-installer/` 에 있다(벤더 원본 = CodeCommit phase-2 `1c11531`).
- 게이트웨이 쪽 작업(모델 등록·클라이언트 값 조회·IP 허용)은 [`../update-scripts/`](../update-scripts/README.md).
