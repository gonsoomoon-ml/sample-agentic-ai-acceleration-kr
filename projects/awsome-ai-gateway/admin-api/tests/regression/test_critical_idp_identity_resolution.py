# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""IdP id_token → DB 신원 해석(``core/auth._resolve_idp_identity``).

배경 — admin-ui 는 OIDC 콜백에서 받은 **IdP id_token 을 그대로** ``admin_jwt`` 쿠키에
굽는다(admin-ui/src/app/api/auth/callback/route.ts). admin-api 는 그 토큰으로
리소스 서버 역할을 한다. 그런데 그 토큰의 형상은 우리가 발급하는 내부 admin JWT 와
다르다:

  * ``sub`` = **IdP subject**. 우리 ``auth.users.id`` 가 아니다.
  * ``role`` 클레임이 **없다**. IdP 는 groups 를 준다.

예전엔 IdP 토큰이 내부 JWT 경로를 그대로 타서 두 가지로 깨졌다:

  1. ``uuid.UUID(payload["sub"])`` 는 Cognito sub(UUID) 를 파싱하지만, 그 값은 DB 에
     없는 id 다 → ``created_by`` FK 가 붙는 모든 쓰기가 **FK 위반 500**.
  2. Okta/Keycloak/Entra 의 sub 는 UUID 가 아니다 → ``uuid.UUID()`` 가 **ValueError**
     를 던지고 아무도 잡지 않아 **500**. 인증 실패가 서버 오류로 보인다.

이 파일은 두 형상이 **서로를 오염시키지 않는다**는 것까지 고정한다 — 내부 JWT 경로가
한 글자도 바뀌지 않았음을 함께 단정해야 이 변경이 안전하다고 말할 수 있다.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.auth import get_current_user
from app.models.auth import UserRole


# ─────────────────────────────────────────────────────────────────────────────
# 하네스
# ─────────────────────────────────────────────────────────────────────────────


def _request(payload: dict) -> MagicMock:
    """``JWTVerifier.verify`` 가 이 payload 를 돌려주는 요청.

    서명 검증 자체는 이 테스트의 관심사가 아니다(JWTVerifier 는 별도 테스트가 있다).
    여기서 고정하는 것은 **검증을 통과한 클레임을 어떤 신원으로 바꾸는가** 다.
    """
    request = MagicMock()
    request.headers = {"Authorization": "Bearer sometoken"}
    verifier = MagicMock()
    verifier.verify = MagicMock(return_value=payload)
    request.app.state.jwt_verifier = verifier
    return request


def _db_user(
    *,
    user_id: uuid.UUID | None = None,
    email: str = "u@example.com",
    team_id: uuid.UUID | None = None,
    role: UserRole = UserRole.DEVELOPER,
    is_active: bool = True,
) -> MagicMock:
    u = MagicMock()
    u.id = user_id or uuid.uuid4()
    u.email = email
    u.team_id = team_id
    u.role = role
    u.is_active = is_active
    return u


def _settings(
    *,
    admin_groups: list[str] | None = None,
    admin_emails: list[str] | None = None,
    required_group: str = "",
    groups_claim: str = "groups",
) -> MagicMock:
    s = MagicMock()
    s.ADMIN_GROUPS = admin_groups or []
    s.ADMIN_EMAILS = admin_emails or []
    s.OIDC_REQUIRED_GROUP = required_group
    s.OIDC_GROUPS_CLAIM = groups_claim
    s.OIDC_USER_ID_CLAIM = "sub"
    s.OIDC_EMAIL_CLAIM = "email"
    s.OIDC_NAME_CLAIM = "name"
    return s


def _resolve(payload: dict, *, db_user, settings=None):
    """IdP 경로를 실제로 태운다. DB 는 ``get_by_sso_subject`` 만 대체한다."""
    settings = settings or _settings()
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=db_user)

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)

    return patch("app.core.auth.AsyncSessionLocal", return_value=session_cm), patch(
        "app.repositories.user_repository.UserRepository", return_value=repo
    ), patch("app.core.oidc_identity.get_settings", return_value=settings), repo


# ─────────────────────────────────────────────────────────────────────────────
# 1. 내부 admin JWT 경로는 바뀌지 않았다 (회귀 방지의 핵심)
# ─────────────────────────────────────────────────────────────────────────────


async def test_internal_admin_jwt_still_uses_sub_as_user_id_without_touching_the_db():
    """role 클레임이 있으면 예전 그대로 — DB 조회조차 하지 않는다.

    ⚠️ 이 단정이 없으면 IdP 지원을 넣으면서 모든 내부 요청에 DB 왕복을 하나 추가한 걸
       아무도 눈치채지 못한다(요청당 커넥션 점유가 늘어난다).
    """
    user_id, team_id = uuid.uuid4(), uuid.uuid4()
    payload = {
        "sub": str(user_id),
        "role": "ADMIN",
        "email": "admin@example.com",
        "team_id": str(team_id),
    }

    with patch("app.core.auth.AsyncSessionLocal") as MockSession:
        user = await get_current_user(_request(payload))

    assert user.user_id == user_id
    assert user.role == UserRole.ADMIN
    assert user.team_id == team_id
    assert user.email == "admin@example.com"
    MockSession.assert_not_called(), "내부 JWT 경로가 DB 를 건드렸다"


async def test_internal_jwt_with_non_uuid_sub_is_401_not_500():
    """role 은 있는데 sub 가 UUID 가 아니면 우리 토큰이 아니다 — 500 이 아니라 401.

    예전엔 ``uuid.UUID()`` 의 ValueError 가 그대로 올라가 최후의 그물이 500 을 냈다.
    """
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request({"sub": "00u1a2b3c4d5", "role": "ADMIN"}))
    assert exc.value.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 2. IdP 경로 — sub 로 DB 신원을 찾는다
# ─────────────────────────────────────────────────────────────────────────────


async def test_idp_token_resolves_user_id_from_db_not_from_sub():
    """이게 FK 위반 500 의 근본 원인이었다 — sub 를 user_id 로 쓰면 안 된다."""
    idp_sub = "00u1a2b3c4d5e6f7g8h9"  # UUID 가 아닌 Okta 형식
    db_id, team_id = uuid.uuid4(), uuid.uuid4()
    db_user = _db_user(user_id=db_id, email="kim@example.com", team_id=team_id)

    p1, p2, p3, repo = _resolve({"sub": idp_sub, "email": "kim@example.com"}, db_user=db_user)
    with p1, p2, p3:
        user = await get_current_user(_request({"sub": idp_sub, "email": "kim@example.com"}))

    assert user.user_id == db_id, "DB PK 가 아니라 IdP sub 를 썼다 — created_by FK 가 깨진다"
    assert user.team_id == team_id
    repo.get_by_sso_subject.assert_awaited_once_with(idp_sub)


async def test_non_uuid_idp_sub_does_not_raise_value_error():
    """Okta/Keycloak/Entra 의 불투명 sub 로 500 이 나지 않아야 한다."""
    db_user = _db_user()
    p1, p2, p3, _ = _resolve({"sub": "opaque|subject|value"}, db_user=db_user)
    with p1, p2, p3:
        user = await get_current_user(_request({"sub": "opaque|subject|value"}))
    assert user.user_id == db_user.id


async def test_unprovisioned_user_is_403_not_401():
    """403 이어야 한다 — 401 은 무한 리다이렉트를 만든다.

    admin-ui middleware 는 401 을 보면 쿠키를 지우고 로그인으로 되돌린다. IdP 세션이
    아직 살아 있으므로 즉시 같은 토큰으로 돌아오고, 영원히 반복된다. 토큰은 유효하고
    사용자만 없는 상황은 403 이 정확하다.
    """
    p1, p2, p3, _ = _resolve({"sub": "unknown-sub"}, db_user=None)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_request({"sub": "unknown-sub"}))
    assert exc.value.status_code == 403
    assert exc.value.detail == "user_not_provisioned"


async def test_deactivated_user_is_rejected():
    """DB 에서 비활성화한 사용자가 IdP 토큰으로 계속 들어오면 안 된다."""
    p1, p2, p3, _ = _resolve({"sub": "s"}, db_user=_db_user(is_active=False))
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_request({"sub": "s"}))
    assert exc.value.status_code == 403
    assert exc.value.detail == "user_deactivated"


async def test_missing_subject_claim_is_401():
    p1, p2, p3, _ = _resolve({"email": "x@y.z"}, db_user=_db_user())
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_request({"email": "x@y.z"}))
    assert exc.value.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 3. 그룹 → 역할
# ─────────────────────────────────────────────────────────────────────────────


async def test_admin_group_grants_admin_even_when_db_says_developer():
    """IdP 에서 관리자 그룹을 받은 사용자는 DB 가 DEVELOPER 여도 ADMIN 이다.

    (프로비저닝은 exchange 경로가 하므로, 그룹 승격이 DB 에 반영되기 전 창이 있다.)
    """
    claims = {"sub": "s", "email": "a@b.c", "groups": ["GatewayAdmin"]}
    p1, p2, p3, _ = _resolve(
        claims,
        db_user=_db_user(role=UserRole.DEVELOPER),
        settings=_settings(admin_groups=["GatewayAdmin"]),
    )
    with p1, p2, p3:
        user = await get_current_user(_request(claims))
    assert user.role == UserRole.ADMIN


async def test_configured_groups_claim_name_is_honoured():
    """Cognito 는 groups 를 ``cognito:groups`` 로 준다 — 이름이 설정으로 읽혀야 한다."""
    claims = {"sub": "s", "email": "a@b.c", "cognito:groups": ["GatewayAdmin"]}
    p1, p2, p3, _ = _resolve(
        claims,
        db_user=_db_user(role=UserRole.DEVELOPER),
        settings=_settings(admin_groups=["GatewayAdmin"], groups_claim="cognito:groups"),
    )
    with p1, p2, p3:
        user = await get_current_user(_request(claims))
    assert user.role == UserRole.ADMIN


async def test_group_matching_is_exact_not_substring():
    """``GatewayAdminReadOnly`` 가 ``GatewayAdmin`` 으로 승격되면 안 된다.

    IdP 가 그룹을 **문자열 하나**로 주는 경우가 있어(단일 값 매퍼), 리스트로 정규화하지
    않고 ``in`` 을 쓰면 부분 문자열 매칭이 된다 — 읽기 전용 그룹이 관리자가 된다.
    """
    claims = {"sub": "s", "email": "a@b.c", "groups": "GatewayAdminReadOnly"}
    p1, p2, p3, _ = _resolve(
        claims,
        db_user=_db_user(role=UserRole.DEVELOPER),
        settings=_settings(admin_groups=["GatewayAdmin"]),
    )
    with p1, p2, p3:
        user = await get_current_user(_request(claims))
    assert user.role == UserRole.DEVELOPER, "부분 문자열 매칭으로 ADMIN 이 됐다"


async def test_single_string_group_still_matches_when_it_is_the_admin_group():
    """위의 정규화가 과잉차단이 아님을 보인다 — 문자열 하나여도 정확히 맞으면 ADMIN."""
    claims = {"sub": "s", "email": "a@b.c", "groups": "GatewayAdmin"}
    p1, p2, p3, _ = _resolve(
        claims,
        db_user=_db_user(role=UserRole.DEVELOPER),
        settings=_settings(admin_groups=["GatewayAdmin"]),
    )
    with p1, p2, p3:
        user = await get_current_user(_request(claims))
    assert user.role == UserRole.ADMIN


async def test_db_admin_is_preserved_when_group_mapping_is_unused():
    """ADMIN_GROUPS 를 안 쓰는 운영 형태를 깨지 않는다 — DB role 이 유지된다."""
    claims = {"sub": "s", "email": "a@b.c"}
    p1, p2, p3, _ = _resolve(
        claims, db_user=_db_user(role=UserRole.ADMIN), settings=_settings(admin_groups=[])
    )
    with p1, p2, p3:
        user = await get_current_user(_request(claims))
    assert user.role == UserRole.ADMIN


async def test_admin_email_bootstrap_applies_on_this_path_too():
    claims = {"sub": "s", "email": "Boss@Example.COM"}
    p1, p2, p3, _ = _resolve(
        claims,
        db_user=_db_user(role=UserRole.DEVELOPER, email="Boss@Example.COM"),
        settings=_settings(admin_emails=["boss@example.com"]),
    )
    with p1, p2, p3:
        user = await get_current_user(_request(claims))
    assert user.role == UserRole.ADMIN, "이메일 비교가 대소문자를 구분했다"


# ─────────────────────────────────────────────────────────────────────────────
# 4. OIDC_REQUIRED_GROUP 게이트
# ─────────────────────────────────────────────────────────────────────────────


async def test_required_group_missing_is_403_before_any_db_lookup():
    """게이트에 막히면 DB 를 조회하지 않는다 — 인가되지 않은 토큰에 왕복을 쓰지 않는다."""
    claims = {"sub": "s", "groups": ["SomeOtherGroup"]}
    p1, p2, p3, repo = _resolve(
        claims, db_user=_db_user(), settings=_settings(required_group="GatewayUsers")
    )
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_request(claims))
    assert exc.value.status_code == 403
    assert exc.value.detail == "required_group_missing"
    repo.get_by_sso_subject.assert_not_awaited()


async def test_empty_required_group_lets_everyone_through():
    """기본값(빈 문자열)은 게이트가 없다는 뜻 — 여기서 막으면 전원 잠김이다."""
    claims = {"sub": "s", "groups": []}
    p1, p2, p3, _ = _resolve(claims, db_user=_db_user(), settings=_settings(required_group=""))
    with p1, p2, p3:
        user = await get_current_user(_request(claims))
    assert user.user_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# 5. 정책이 두 경로에서 갈리지 않는지
# ─────────────────────────────────────────────────────────────────────────────


def test_oidc_service_delegates_role_policy_instead_of_duplicating_it():
    """``OIDCService._derive_role`` 이 ``core.oidc_identity.derive_role`` 을 부르는가.

    복제하면 exchange 경로와 admin_jwt 경로가 조용히 어긋난다 — 정확히 그 부류의 사고가
    이 레포에 있었다(admin-ui 하드코딩 vs admin-api 설정값 → 관리자 잠김).
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].parent / "src" / "app" / "services" / "oidc_service.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_derive_role":
            target = node
    assert target is not None, "_derive_role 을 찾지 못했다"

    calls = {
        n.func.id
        for n in ast.walk(target)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "derive_role" in calls, (
        "_derive_role 이 공용 정책을 부르지 않는다 — 정책이 복제됐다"
    )
    # 대조군: 복제 흔적(ADMIN_GROUPS 를 직접 읽는 코드)이 남아 있지 않은가.
    #
    # ⚠️ docstring 을 빼고 봐야 한다. `ast.dump(target)` 은 docstring 까지 문자열로
    #    담으므로, "정책은 oidc_identity 하나뿐" 이라고 **설명한 주석** 때문에
    #    ADMIN_GROUPS 가 걸려 이 단정이 거짓 실패한다(자기 문서를 증거로 읽는 함정).
    body = list(target.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    executable = ast.dump(ast.Module(body=body, type_ignores=[]))
    assert "ADMIN_GROUPS" not in executable, (
        "_derive_role 이 여전히 ADMIN_GROUPS 를 직접 읽는다 — 위임이 반쪽이다"
    )
    # 그리고 이 판정이 공허하지 않은지: 위임 본체(oidc_identity)에는 ADMIN_GROUPS 가
    # 실제로 있어야 한다. 없으면 정책이 사라진 것이고 위 단정은 자동 통과다.
    identity = src.parent.parent / "core" / "oidc_identity.py"
    assert "ADMIN_GROUPS" in identity.read_text(encoding="utf-8"), (
        "oidc_identity 에 ADMIN_GROUPS 정책이 없다 — 위임 대상이 비었다"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. JWTVerifier 가 IdP id_token 형상을 실제로 검증할 수 있는가
# ─────────────────────────────────────────────────────────────────────────────
#
# 위 테스트들은 verifier.verify 를 모킹한다 — 서명 검증은 관심사 밖이라고 선언하고
# 넘겼다. 그런데 dev 에서 Cognito 로그인이 정확히 그 경계에서 깨졌다: Cognito
# id_token 은 ``at_hash`` 클레임을 싣는데, jose 는 access_token 없이는 at_hash 를
# 검증할 수 없어 JWTClaimsError 를 던지고, verify() 는 그걸 JWTError 로 삼켜 401 이
# 됐다. 모킹된 테스트는 이걸 영원히 못 잡는다 — 실제 RSA 서명·검증을 돌려야 한다.


def test_jwt_verifier_accepts_idp_id_token_with_at_hash():
    """``at_hash`` 를 단 IdP id_token 이 ``JWTVerifier.verify`` 를 통과하는가.

    쿠키에는 id_token 만 실리므로 비교할 access_token 이 없다 — at_hash 검증은
    꺼져야 한다(core.oidc_verifier 와 동일 정책). 이 테스트는 실제 RSA 키로 서명한
    토큰을 verify() 에 통과시켜, claims 검증 옵션이 IdP 토큰 형상을 받는지 고정한다.
    """
    import time

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose import jwt as jose_jwt

    from app.core.auth import JWTVerifier

    issuer = "https://idp.example.com/pool"
    audience = "app-client-id"
    priv = rsa.generate_private_key(65537, 2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    verifier = JWTVerifier()
    verifier.load_configs([
        {
            "id": uuid.uuid4(),
            "issuer": issuer,
            "audience": audience,
            "public_key_pem": pub_pem,
            "algorithm": "RS256",
        }
    ])

    now = int(time.time())
    token = jose_jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": issuer,
            "aud": audience,
            "exp": now + 3600,
            "iat": now,
            "at_hash": "binds-to-access-token-we-do-not-have",
            "cognito:groups": ["ClaudeAdmin"],
        },
        priv_pem,
        algorithm="RS256",
        # kid 가 config id(UUID) 와 절대 안 맞는 형태 — all-keys 폴백 경로도 같이 검증
        headers={"kid": "idpSigningKeyId"},
    )

    payload = verifier.verify(token)
    assert payload["iss"] == issuer
    assert payload["at_hash"] == "binds-to-access-token-we-do-not-have"
