# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""POST /v1/auth/admin-session — IdP id_token → 내부 admin 세션 JWT 교환.

배경 — admin-ui 의 OIDC hosted-UI 콜백은 Cognito 가 발급한 id_token 을 받는데,
그 토큰에는 ``role``/``team_id`` 클레임이 없다. 그대로 ``admin_jwt`` 쿠키에
구우면 admin-ui middleware 의 resolveRole 이 DEVELOPER 로 깔아 **TEAM_LEADER
(DB 수동 지정 역할 — Cognito 그룹이 아니다)가 로그인 직후 /403** 에 갇혔다.

그래서 콜백은 이 엔드포인트로 id_token 을 교환하고, admin-api 가 DB 신원으로
자체 서명한 세션 JWT(``sign_admin_session_jwt``)를 쿠키에 굽는다 — ROPC
로그인(``auth_admin._finish_login``)과 최종 형태가 같다.

여기서 검증하는 것:
- OIDC 미설정 → 503 oidc_disabled (봉투 한 겹)
- Bearer 없음/토큰 검증 실패 → 401
- DEVELOPER 등 admin-ui 권한 없는 사용자 → 403 (not_provisionable)
- 성공 → AdminLoginResponse 형태, **실제 서명된** JWT 에 role/team_id 클레임
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.core.db import get_db_session
from app.main import register_exception_handlers
from app.models.auth import User, UserRole
from app.routers import auth_oidc
from app.services.oidc_service import OIDCAuthError, OIDCNotProvisionableError

pytestmark = pytest.mark.asyncio


def _app(svc) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_oidc.router)
    app.state.oidc_service = svc

    async def _no_db():
        yield None

    app.dependency_overrides[get_db_session] = _no_db
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


class _Svc:
    """authenticate_for_admin_ui 를 대신하는 스텁 — 어떤 토큰이 왔는지 기록한다."""

    def __init__(self, *, user=None, exc: Exception | None = None):
        self.user = user
        self.exc = exc
        self.seen_token: str | None = None

    async def authenticate_for_admin_ui(self, session, *, token: str) -> User:
        self.seen_token = token
        if self.exc is not None:
            raise self.exc
        return self.user


async def test_admin_session_503_when_oidc_disabled():
    async with _client(_app(None)) as ac:
        res = await ac.post("/v1/auth/admin-session", headers={"Authorization": "Bearer x"})
    assert res.status_code == 503
    body = res.json()
    assert "detail" not in body, f"이중 봉투: {body}"
    assert body["error"]["type"] == "oidc_disabled"


async def test_admin_session_401_without_bearer():
    async with _client(_app(_Svc())) as ac:
        res = await ac.post("/v1/auth/admin-session")
    assert res.status_code == 401
    assert res.json()["error"]["type"] == "invalid_token"


async def test_admin_session_401_on_bad_idp_token():
    svc = _Svc(exc=OIDCAuthError("signature verification failed"))
    async with _client(_app(svc)) as ac:
        res = await ac.post("/v1/auth/admin-session", headers={"Authorization": "Bearer bad"})
    assert res.status_code == 401
    assert res.json()["error"]["type"] == "invalid_token"
    assert svc.seen_token == "bad"


async def test_admin_session_403_for_developer():
    """DEVELOPER 는 admin-ui 페이지가 없다 — authenticate_for_admin_ui 가
    upsert 이후 role 게이트로 거부한다. 401 이 아니라 **403** 이어야 한다."""
    svc = _Svc(exc=OIDCNotProvisionableError("admin_ui_access_denied"))
    async with _client(_app(svc)) as ac:
        res = await ac.post("/v1/auth/admin-session", headers={"Authorization": "Bearer t"})
    assert res.status_code == 403
    assert res.json()["error"]["type"] == "not_provisionable"


async def test_admin_session_issues_internal_jwt_with_db_role():
    """핵심 계약 — 응답 token 은 **실제 서명된** 내부 JWT 여야 하고, IdP 토큰이
    주지 않는 ``role``/``team_id`` 를 DB 신원에서 채워야 한다.

    TEAM_LEADER 를 고른 이유: 이 역할은 Cognito 그룹이 아니라 DB 수동 지정이라
    id_token 으로는 절대 전달될 수 없다 — 이 엔드포인트가 존재하는 이유 자체다.
    서명을 스텁하면 "JWT 형태 문자열을 준다"까지만 보장돼, 클레임 누락 회귀를
    못 잡는다 — 실제 키로 서명·검증한다.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(65537, 2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    team_id = uuid.uuid4()
    user = User(
        id=uuid.uuid4(),
        email="leader@example.test",
        display_name="Leader",
        role=UserRole.TEAM_LEADER,
        team_id=team_id,
        is_active=True,
    )
    svc = _Svc(user=user)

    settings = MagicMock()
    settings.ADMIN_UI_JWT_PRIVATE_KEY_PEM = MagicMock(
        get_secret_value=MagicMock(return_value=priv_pem)
    )
    settings.ADMIN_UI_JWT_TTL_HOURS = 12
    settings.ADMIN_UI_JWT_ISSUER = "ds-gateway-admin"
    settings.ADMIN_UI_JWT_AUDIENCE = "ds-gateway-admin-api"
    settings.ADMIN_UI_JWT_CONFIG_ID = str(uuid.uuid4())

    with patch("app.core.admin_jwt_signer.get_settings", return_value=settings):
        async with _client(_app(svc)) as ac:
            res = await ac.post(
                "/v1/auth/admin-session",
                headers={"Authorization": "Bearer cognito-id-token"},
            )

    assert res.status_code == 200, res.text
    assert svc.seen_token == "cognito-id-token"
    body = res.json()
    assert body["role"] == "TEAM_LEADER"
    assert body["email"] == "leader@example.test"
    assert body["team_id"] == str(team_id)
    assert body["expires_at"] > 0

    # 발급된 토큰을 실제 공개키로 검증 — 클레임이 실제로 박혔는지 확인한다.
    claims = jose_jwt.decode(
        body["token"],
        pub_pem,
        algorithms=["RS256"],
        issuer="ds-gateway-admin",
        audience="ds-gateway-admin-api",
        options={"verify_at_hash": False},
    )
    assert claims["role"] == "TEAM_LEADER"
    assert claims["team_id"] == str(team_id)
    assert claims["sub"] == str(user.id)
    assert claims["iss"] == "ds-gateway-admin"
