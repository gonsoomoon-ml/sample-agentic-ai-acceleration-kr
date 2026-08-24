# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""POST /v1/auth/admin/login — admin-ui 커스텀 로그인 폼 → Cognito ROPC → admin_jwt 세션 발급.

admin-ui 의 ``/api/auth/login`` 라우트가 이 엔드포인트를 호출하고, 반환된 ``token`` 을
그대로 ``admin_jwt`` 쿠키에 저장한다. Admin JWT 인증 불필요 (로그인 엔드포인트 자체).

``/login`` 이 ``challenge: "NEW_PASSWORD_REQUIRED"`` 를 반환하면(관리자가 콘솔/CLI 로
막 만든 계정의 임시 비밀번호 상태), admin-ui 가 비밀번호 설정 화면을 띄우고
``/new-password`` 로 이어서 로그인을 완료한다.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.oidc_verifier import OIDCConfigError
from app.services.admin_auth_service import (
    AdminAuthService,
    AdminLoginChallengeError,
    AdminLoginError,
    AdminLoginRateLimitedError,
)
from app.services.oidc_service import OIDCNotProvisionableError

logger = structlog.get_logger()

router = APIRouter(prefix="/v1/auth/admin", tags=["Admin UI Auth"])


def _client_ip(request: Request) -> str:
    """실제 브라우저 IP를 뽑는다.

    이 엔드포인트는 브라우저가 아니라 admin-ui(Next.js 서버)가 서버 간 호출로
    부른다 — ``request.client.host`` 는 admin-ui pod 자체의 IP라, 로그인 실패
    rate limit 의 IP 키로 쓰면 모든 사용자의 시도가 한 버킷에 합쳐진다.
    admin-ui 의 ``/api/auth/login``·``/new-password`` 라우트가 브라우저 요청의
    ``x-forwarded-for``(ALB가 붙인 진짜 클라이언트 IP)를 그대로 전달해주므로
    그 값을 우선 사용하고, 없으면(직접 호출/테스트) 기존 방식으로 폴백한다.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminNewPasswordRequest(BaseModel):
    email: str
    new_password: str
    cognito_session: str


class AdminLoginResponse(BaseModel):
    token: str
    expires_at: int
    role: str
    email: str
    display_name: str
    team_id: str | None


class AdminChallengeResponse(BaseModel):
    challenge: str
    cognito_session: str


def _user_to_login_response(user, token: str, expires_at: int) -> AdminLoginResponse:
    return AdminLoginResponse(
        token=token,
        expires_at=expires_at,
        role=user.role.value,
        email=user.email,
        display_name=user.display_name,
        team_id=str(user.team_id) if user.team_id else None,
    )


@router.post("/login", response_model=AdminLoginResponse | AdminChallengeResponse)
async def admin_login(
    request: Request,
    body: AdminLoginRequest,
    session: AsyncSession = Depends(get_db_session),
):
    svc: AdminAuthService | None = request.app.state.admin_auth_service
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "type": "admin_login_disabled",
                    "message": (
                        "Cognito admin-ui login is not configured. "
                        "Set OIDC_ISSUER_URL, COGNITO_USER_POOL_ID, COGNITO_APP_CLIENT_ID, "
                        "ADMIN_UI_JWT_PRIVATE_KEY_PEM."
                    ),
                }
            },
        )

    try:
        user, token, expires_at = await svc.login(
            session,
            email=body.email,
            password=body.password,
            ip_address=_client_ip(request),
        )
    except AdminLoginRateLimitedError as e:
        raise HTTPException(
            status_code=429,
            detail={"error": {"type": "rate_limited", "message": str(e)}},
        )
    except AdminLoginChallengeError as e:
        if e.challenge_name == "NEW_PASSWORD_REQUIRED":
            return AdminChallengeResponse(challenge=e.challenge_name, cognito_session=e.cognito_session)
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "type": "challenge_required",
                    "message": f"Cognito challenge required: {e.challenge_name}. Contact your administrator.",
                }
            },
        )
    except AdminLoginError as e:
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "invalid_credentials", "message": str(e)}},
        )
    except OIDCNotProvisionableError as e:
        logger.info("admin_auth.login_forbidden", email=body.email, reason=str(e))
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "forbidden", "message": str(e)}},
        )
    except OIDCConfigError as e:
        # IDP discovery / JWKS unreachable — auth_oidc.py 의 /v1/auth/exchange 와 동일 매핑.
        raise HTTPException(
            status_code=503,
            detail={"error": {"type": "idp_unavailable", "message": str(e)}},
        )

    return _user_to_login_response(user, token, expires_at)


@router.post("/new-password", response_model=AdminLoginResponse)
async def admin_new_password(
    request: Request,
    body: AdminNewPasswordRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """``/login`` 이 ``NEW_PASSWORD_REQUIRED`` 챌린지를 반환했을 때 새 비밀번호로 로그인을
    완료한다. ``cognito_session`` 은 ``/login`` 응답의 것을 그대로 되돌려줘야 하며 1회용
    (만료/재사용 시 401 — 처음부터 다시 로그인)."""
    svc: AdminAuthService | None = request.app.state.admin_auth_service
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail={"error": {"type": "admin_login_disabled", "message": "Cognito admin-ui login is not configured."}},
        )

    try:
        user, token, expires_at = await svc.complete_new_password(
            session,
            email=body.email,
            new_password=body.new_password,
            cognito_session=body.cognito_session,
            ip_address=_client_ip(request),
        )
    except AdminLoginRateLimitedError as e:
        raise HTTPException(
            status_code=429,
            detail={"error": {"type": "rate_limited", "message": str(e)}},
        )
    except AdminLoginChallengeError as e:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "type": "challenge_required",
                    "message": f"Additional Cognito challenge required: {e.challenge_name}. Contact your administrator.",
                }
            },
        )
    except AdminLoginError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"type": "invalid_new_password", "message": str(e)}},
        )
    except OIDCNotProvisionableError as e:
        logger.info("admin_auth.new_password_forbidden", email=body.email, reason=str(e))
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "forbidden", "message": str(e)}},
        )
    except OIDCConfigError as e:
        raise HTTPException(
            status_code=503,
            detail={"error": {"type": "idp_unavailable", "message": str(e)}},
        )

    return _user_to_login_response(user, token, expires_at)
