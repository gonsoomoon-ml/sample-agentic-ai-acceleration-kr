"""IdP id_token 의 **서명 검증** — admin 콘솔 Cognito 로그인이 500 이던 결함.

배경 (2026-09-25 US dev 실측) — admin-ui 는 OIDC 콜백에서 받은 IdP id_token 을
그대로 ``admin_jwt`` 쿠키에 굽고, 화면이 admin-api 를 부를 때마다 그 토큰을 Bearer 로
보낸다. admin-api 의 ``get_current_user`` 는 그 토큰을 **``auth.admin_jwt_configs``
의 정적 공개키(``JWTVerifier``)로만** 검증했다. 그 테이블에는 시드된 자리표시 행
(``REPLACE_WITH_ACTUAL_RS256_PUBLIC_KEY``)뿐이라 jose 가 키를 만들다 ``JWKError`` 를
던졌고, 코드는 ``JWTError`` 만 잡으므로 **모든 API 호출이 500** 이었다 — 대시보드
숫자가 비고 모니터링 화면이 500.

정작 admin-api 에는 IdP 의 JWKS 를 자동으로 받아 ``kid`` 로 고르는 ``OIDCVerifier`` 가
이미 있고(VK 발급 ``/v1/auth/exchange`` 가 쓴다) 키 교체에도 안전하다. IdP 가 발급한
토큰은 그걸로 검증해야 한다.

기존 회귀 테스트(test_critical_idp_identity_resolution.py)는 ``JWTVerifier.verify`` 를
목으로 바꿔 **서명 검증을 한 번도 태우지 않았다** — 그래서 이 결함이 안 잡혔다. 이
파일은 진짜 RSA 키로 서명한 토큰을 진짜 ``OIDCVerifier`` 에 넣는다. 네트워크 대신
``httpx.MockTransport`` 가 디스커버리 문서와 JWKS 를 돌려준다.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwk, jwt

from app.core.auth import JWTVerifier, get_current_user
from app.core.oidc_verifier import OIDCVerifier
from app.models.auth import UserRole

ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_TESTPOOL"
CLIENT_ID = "testclientid"
KID = "cognito-kid-1"

# db/init/03_seed_data.sql 의 자리표시 행 — 설치 직후 모든 배포가 이 상태다.
SEED_PLACEHOLDER = {
    "id": "00000000-0000-4000-a000-000000000030",
    "issuer": "ds-gateway-admin",
    "audience": "ds-gateway-admin-api",
    "public_key_pem": (
        "-----BEGIN PUBLIC KEY-----\n"
        "REPLACE_WITH_ACTUAL_RS256_PUBLIC_KEY\n"
        "-----END PUBLIC KEY-----"
    ),
    "algorithm": "RS256",
}


# ─────────────────────────────────────────────────────────────────────────────
# 하네스
# ─────────────────────────────────────────────────────────────────────────────


def _rsa_pair() -> tuple[str, str]:
    """(private_pem, public_pem)"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _jwks_client(public_pem: str, kid: str = KID) -> httpx.AsyncClient:
    """IdP 의 discovery + JWKS 를 흉내 내는 클라이언트 (네트워크 없음)."""
    jwk_dict = jwk.construct(public_pem, "RS256").to_dict()
    jwk_dict.update({"kid": kid, "use": "sig", "alg": "RS256"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": ISSUER + "/.well-known/jwks.json"})
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json={"keys": [jwk_dict]})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _id_token(private_pem: str, *, iss: str = ISSUER, kid: str = KID, **extra) -> str:
    now = int(time.time())
    claims = {
        "iss": iss,
        "sub": "3861b390-20f1-7050-344e-a5d5dd0224a1",
        "aud": CLIENT_ID,
        "token_use": "id",
        "email": "admin@example.com",
        "cognito:groups": ["Claude_platform", "ClaudeAdmin"],
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(extra)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _request(token: str, *, oidc_verifier=None, oidc_http=None) -> MagicMock:
    """설치 직후와 같은 상태 — 정적 검증기에는 시드된 자리표시 키만 있다."""
    static = JWTVerifier()
    static.load_configs([SEED_PLACEHOLDER])
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token}"}
    request.app.state = SimpleNamespace(
        jwt_verifier=static, oidc_verifier=oidc_verifier, oidc_http=oidc_http
    )
    return request


def _db_patches(db_user):
    settings = MagicMock()
    settings.ADMIN_GROUPS = ["ClaudeAdmin"]
    settings.ADMIN_EMAILS = []
    settings.OIDC_REQUIRED_GROUP = ""
    settings.OIDC_GROUPS_CLAIM = "cognito:groups"
    settings.OIDC_USER_ID_CLAIM = "sub"
    settings.OIDC_EMAIL_CLAIM = "email"
    settings.OIDC_NAME_CLAIM = "name"

    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=db_user)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    return (
        patch("app.core.auth.AsyncSessionLocal", return_value=session_cm),
        patch("app.repositories.user_repository.UserRepository", return_value=repo),
        patch("app.core.oidc_identity.get_settings", return_value=settings),
    )


def _db_user() -> MagicMock:
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "admin@example.com"
    u.team_id = uuid.uuid4()
    u.role = UserRole.DEVELOPER
    u.is_active = True
    return u


# ─────────────────────────────────────────────────────────────────────────────
# 1. IdP 가 발급한 토큰은 IdP 의 JWKS 로 검증한다
# ─────────────────────────────────────────────────────────────────────────────


async def test_cognito_id_token_is_verified_with_idp_jwks_even_with_placeholder_static_key():
    """dev 실측 재현: 시드 자리표시 키만 있는 설치에서 Cognito 로그인 후 API 호출.

    예전: 자리표시 PEM 으로 키를 만들다 JWKError → 500. 기대: JWKS 로 검증 통과 →
    sub 로 DB 신원 → ClaudeAdmin 그룹이므로 ADMIN.
    """
    private_pem, public_pem = _rsa_pair()
    verifier = OIDCVerifier(issuer_url=ISSUER, audience="")
    db_user = _db_user()

    p1, p2, p3 = _db_patches(db_user)
    async with _jwks_client(public_pem) as http:
        with p1, p2, p3:
            user = await get_current_user(
                _request(_id_token(private_pem), oidc_verifier=verifier, oidc_http=http)
            )

    assert user.user_id == db_user.id
    assert user.role == UserRole.ADMIN


async def test_idp_token_signed_by_another_key_is_401_not_500():
    """서명이 맞지 않는 토큰은 인증 실패(401)이지 서버 오류(500)가 아니다."""
    signer_pem, _ = _rsa_pair()
    _, published_pem = _rsa_pair()
    verifier = OIDCVerifier(issuer_url=ISSUER, audience="")

    async with _jwks_client(published_pem) as http:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(
                _request(_id_token(signer_pem), oidc_verifier=verifier, oidc_http=http)
            )
    assert exc.value.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 2. 정적 키 경로 — 자리표시 키가 500 을 만들지 않는다
# ─────────────────────────────────────────────────────────────────────────────


async def test_placeholder_static_key_gives_401_not_500():
    """OIDC 를 안 켠 설치에서 모르는 RS256 토큰이 오면 401 이어야 한다.

    예전엔 자리표시 PEM 으로 키를 만들다 난 JWKError 가 잡히지 않아 500 이었다.
    """
    private_pem, _ = _rsa_pair()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request(_id_token(private_pem)))
    assert exc.value.status_code == 401


async def test_internal_admin_jwt_still_verified_with_static_keys_when_oidc_is_on():
    """OIDC 가 켜져 있어도 우리가 발급한 내부 admin JWT(다른 iss)는 정적 키로 검증된다."""
    private_pem, public_pem = _rsa_pair()
    static = JWTVerifier()
    static.load_configs([{**SEED_PLACEHOLDER, "id": "k-internal", "public_key_pem": public_pem}])
    user_id = uuid.uuid4()
    token = jwt.encode(
        {
            "iss": "ds-gateway-admin",
            "aud": "ds-gateway-admin-api",
            "sub": str(user_id),
            "role": "ADMIN",
            "exp": int(time.time()) + 600,
        },
        private_pem,
        algorithm="RS256",
    )
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token}"}
    request.app.state = SimpleNamespace(
        jwt_verifier=static,
        oidc_verifier=OIDCVerifier(issuer_url=ISSUER, audience=""),
        oidc_http=None,  # 이 경로에서 IdP 를 부르면 여기서 터진다
    )

    user = await get_current_user(request)
    assert user.user_id == user_id
    assert user.role == UserRole.ADMIN
