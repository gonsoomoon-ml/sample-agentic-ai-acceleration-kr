# 8-L. Admin UI Cognito 로그인 활성화 (dev-login 대체)

> ← [operations.md](../operations.md) §8 목차로 · 이 절 = **§8-L**

> 📒 **`US-10` · 등급 선택(운영이면 강력 권장)** — [README.md 「최신 업데이트」](../README.md#2-최신-업데이트).

> **신규 설치도 대상이다.** `US-01` 기준선은 admin-ui 로그인이 `DEV_LOGIN_ENABLED=true` 인 dev-login(role 을 화면에서 직접 골라 서명 없는 쿠키를 발급하는 MVP 우회)뿐이다. 이 절을 적용해야 실제 Cognito 계정(이메일/비밀번호)으로 로그인하고, 로그인한 계정의 Cognito 그룹(`ClaudeAdmin`/`Claude_<team>`)에 따라 role·소속 팀이 자동으로 매겨진다.

**무엇이 바뀌나**

| | 지금 (dev-login) | 적용 후 |
| --- | --- | --- |
| 로그인 화면 | role 을 드롭다운으로 직접 선택 → 서명 없는 쿠키 발급 | 이메일/비밀번호 입력 → Cognito 인증 |
| 인증 주체 | 없음(누구나 원하는 role 로 접속 가능) | Cognito User Pool 계정 |
| role 결정 | 화면에서 직접 선택 | `ClaudeAdmin` 그룹 → ADMIN, 그 외 → DEVELOPER(팀 리더는 admin-ui 에서 수동 지정) |
| 노출 범위 | `DEV_LOGIN_ENABLED=true` 인 동안 인증 자체가 없는 것과 같음 | 유효한 Cognito 계정 + 올바른 그룹만 |

> 🔴 **`DEV_LOGIN_ENABLED=true` 상태에서 admin-ui/admin-api 가 인터넷에 열려 있으면 누구나 관리자 권한을 발급받을 수 있다** — [8-S-hardening.md](8-S-hardening.md) 가 이미 이 위험 때문에 admin 콘솔을 관리자 IP/VPN 대역으로 좁혀두라고 안내하고 있다. 이 절을 적용하고 `global.devLoginEnabled: false` 로 끄면 그 네트워크 제한 없이도 admin 콘솔을 안전하게 열어둘 수 있다.

**(1) 이미지 재빌드** — admin-api·admin-ui 코드가 바뀌었다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/rebuild-image.sh admin-api dev
bash deployment/scripts/rebuild-image.sh admin-ui dev
```

**(2) 세션 서명 키 발급 + DB/Secret 반영** — 1회성. admin-ui 로그인 성공 시 admin-api 가 자체 서명하는 세션 JWT(RS256) 키쌍을 만들고, 공개키는 DB(`auth.admin_jwt_configs`)에, 개인키는 Secret(ExternalSecrets 면 Secrets Manager `/llm-gateway/<env>/app`, 아니면 K8s Secret)에 반영한다.

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/setup-admin-ui-login.sh dev
```

요약을 보여주고 `y/N` 확인 후 진행한다. 마지막에 `values-eks-fargate-<env>.yaml` 에 `auth.adminUiJwt.privateKeySecretName` 을 자동으로 반영해준다 — 이 값이 있어야 다음 `install-eks.sh` 에서도 유지된다.

> ⚠️ **이미 admin-ui Cognito 로그인이 활성화돼 있는 상태에서 다시 돌리면 키가 교체되어 기존 admin-ui 세션이 전부 무효화된다**(재로그인 필요). VK(Claude Code/Cowork 인증)는 별개 경로라 영향 없다.

**(3) 반영**

▶ **실행** · 배포 EC2

```bash
cd ~/awsome-ai-gateway
bash deployment/scripts/install-eks.sh dev
```

`terraform output` 의 `cognito_client_id` 가 `adminApi.adminUiLogin.cognitoAppClientId` 로 자동 주입되고, (2)에서 반영한 `auth.adminUiJwt.privateKeySecretName` 때문에 admin-api 가 `checksum/secret` 변경으로 재시작된다.

**(4) 확인**

```bash
kubectl -n llm-gateway logs -l app.kubernetes.io/component=admin-api --tail=50 | grep -i admin_auth
```

`admin_auth.enabled client_id=...` 가 찍혀야 한다. `admin_auth.disabled reason=...` 가 보이면 아래 「함정」의 첫 항목을 본다.

admin-ui `/login` 에서 실제 Cognito 계정으로 로그인 시도. 로그인 대상 계정은 `ClaudeAdmin` 또는 `Claude_<team>` 그룹에 속해 있어야 한다(§3-8 Cognito 사용자 관리, [install-guide.md](../install-guide.md) 참고).

> ℹ️ **임시 비밀번호 계정도 admin-ui 에서 바로 처리된다.** `admin-create-user` 로 막 만든 계정(FORCE_CHANGE_PASSWORD 상태)으로 로그인하면 Cognito 의 `NEW_PASSWORD_REQUIRED` 챌린지가 뜨는데, admin-ui `/login` 이 자동으로 "새 비밀번호 설정" 화면으로 전환해 그 자리에서 영구 비밀번호를 받고 로그인까지 완료한다 — 별도로 `aws cognito-idp admin-set-user-password` 를 미리 돌릴 필요 없다.

**(5) dev-login 끄기** — (4)까지 실제 로그인이 성공적으로 확인된 뒤에만.

`values-eks-fargate-<env>.yaml`을 직접 고치지 않고도 `install-eks.sh`에 환경변수로 override 할 수 있다.

```bash
DEV_LOGIN_ENABLED=false bash deployment/scripts/install-eks.sh dev
```

admin-api·admin-ui 양쪽에 동시 반영된다. 끄고 나면 `/api/auth/dev-login` 은 404, `/login` 화면의 "Sign in with dev mode" 링크도 사라진다. 반대로 급하게 되돌리려면 `DEV_LOGIN_ENABLED=true`로 다시 실행한다.

> ⚠️ 이 `--set` override는 Helm release에만 적용되고 `values-eks-fargate-<env>.yaml` 파일은 건드리지 않는다. 다음 `install-eks.sh`를 다시 실행할 때도 여전히 dev-login이 꺼진 상태로 두려면, 이 환경변수를 함께 주거나, values 파일의 `global.devLoginEnabled`를 `false`로 바꿔도 된다.

**함정 3가지**

- **키/Secret 만 반영하고 (3)을 건너뛰면 아무 효과가 없다.** `setup-admin-ui-login.sh` 는 DB·Secret·values 파일만 바꾼다. admin-api 가 실제로 그 값을 읽으려면 `install-eks.sh` 로 재배포(→ pod 재시작)해야 한다. 그 전까지는 `admin_auth.disabled` 상태 그대로다.
- **팀 리더는 Cognito 그룹이 아니다.** `ClaudeAdmin` 은 자동으로 ADMIN role 을 주지만, TEAM_LEADER 는 admin-ui `/users`(조직 관리 화면)에서 관리자가 팀원 한 명을 직접 지정해야 한다(`PUT /admin/teams/{id}/leader`) — 별도 Cognito 그룹을 두 개씩 관리할 필요가 없도록 의도적으로 이렇게 설계됨. 한 번 지정하면 그 사람이 Cognito 로 재로그인하거나 전체 sync 를 돌려도 role 이 유지된다.
- **팀 그룹이 하나도 없는 계정은 ADMIN 이어도 로그인이 거부될 수 있다.** `OIDC_REJECT_UNMATCHED_GROUPS=true`(기본값)인 상태에서 `Claude_<team>` 패턴 그룹에 하나도 속해 있지 않으면 `no_matching_team_group` 으로 403 이 난다 — `ClaudeAdmin` 부트스트랩 계정도 팀 그룹에 함께 가입시켜 둘 것.
