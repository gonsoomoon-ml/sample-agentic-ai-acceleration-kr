# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Admin-UI 세션 JWT 발급 — admin-api 가 자체 서명하는 RS256 JWT.

Cognito 로그인(ROPC) 성공 + role/team 매핑이 끝난 사용자에 대해, admin-ui 가
쿠키(``admin_jwt``)로 들고 다닐 세션 토큰을 이 모듈이 서명한다. 이 토큰은
``core.auth.JWTVerifier`` 가 ``auth.admin_jwt_configs`` 테이블에 저장된
공개키로 다시 검증한다 (self-issued, kid 매칭).

Cognito 의 id_token 을 그대로 쓰지 않는 이유: id_token 에는 ``role``/``team_id``
claim 이 없고(자동 프로비저닝/그룹 매핑 결과는 DB 에만 있음), 세션 TTL 도
admin-ui 요구사항(내부 정책)과 Cognito 토큰 TTL 이 다를 수 있기 때문.
"""
from __future__ import annotations

import time

from jose import jwt

from app.core.config import get_settings
from app.models.auth import User


def sign_admin_session_jwt(user: User) -> tuple[str, int]:
    """user 정보로 admin-ui 세션 JWT 를 서명한다.

    Returns
    -------
    (token, expires_at_epoch_seconds)
    """
    settings = get_settings()
    private_key_pem = settings.ADMIN_UI_JWT_PRIVATE_KEY_PEM.get_secret_value()
    if not private_key_pem:
        raise RuntimeError(
            "ADMIN_UI_JWT_PRIVATE_KEY_PEM not configured — "
            "run scripts/generate_admin_jwt_keypair.py and set the env var."
        )

    now = int(time.time())
    exp = now + settings.ADMIN_UI_JWT_TTL_HOURS * 3600
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "team_id": str(user.team_id) if user.team_id else None,
        "iss": settings.ADMIN_UI_JWT_ISSUER,
        "aud": settings.ADMIN_UI_JWT_AUDIENCE,
        "iat": now,
        "exp": exp,
    }
    token = jwt.encode(
        payload,
        private_key_pem,
        algorithm="RS256",
        headers={"kid": settings.ADMIN_UI_JWT_CONFIG_ID},
    )
    return token, exp
