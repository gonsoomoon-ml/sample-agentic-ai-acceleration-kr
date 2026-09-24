# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from app.core.db import AsyncSessionLocal
from app.models.auth import UserRole
from app.services.service_token_service import (
    SERVICE_TOKEN_PREFIX,
    ServiceTokenService,
    hash_token,
)

logger = structlog.get_logger()


@dataclass
class CurrentUser:
    user_id: uuid.UUID
    email: str
    role: UserRole
    team_id: uuid.UUID | None
    is_service_token: bool = False
    service_token_id: uuid.UUID | None = None


class JWTVerifier:
    """Loads Admin JWT public keys at startup and verifies RS256 tokens."""

    def __init__(self) -> None:
        self._public_keys: dict[str, dict] = {}  # kid -> {pem, issuer, audience, algorithm}

    def load_configs(self, configs: list[dict]) -> None:
        for cfg in configs:
            kid = str(cfg["id"])
            self._public_keys[kid] = {
                "pem": cfg["public_key_pem"],
                "issuer": cfg["issuer"],
                "audience": cfg["audience"],
                "algorithm": cfg["algorithm"],
            }
        logger.info("jwt_verifier.loaded", key_count=len(self._public_keys))

    def verify(self, token: str) -> dict:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # If kid present, look up specific key; otherwise try all active keys
        candidates = [self._public_keys[kid]] if kid and kid in self._public_keys else list(self._public_keys.values())

        if not candidates:
            raise JWTError("No matching public key found")

        last_error: JWTError | None = None
        for key_cfg in candidates:
            try:
                payload = jwt.decode(
                    token,
                    key_cfg["pem"],
                    algorithms=[key_cfg["algorithm"]],
                    issuer=key_cfg["issuer"],
                    audience=key_cfg["audience"],
                    # IdP id_token 의 at_hash 는 access_token 과의 binding 검증용이다.
                    # 쿠키에는 id_token 만 실리므로 비교할 access_token 이 없다 —
                    # core.oidc_verifier 와 같은 이유로 끈다.
                    options={"verify_at_hash": False},
                )
                return payload
            except JWTError as e:
                last_error = e
                continue

        raise last_error  # type: ignore[misc]


def _extract_token(request: Request) -> str:
    """Extract JWT from Authorization header or admin_jwt cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    # Fallback: read from cookie (admin-ui sends cookies via server-side fetch)
    cookie_header = request.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("admin_jwt="):
            return part[len("admin_jwt="):]

    raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


def _parse_dev_token(token: str) -> dict | None:
    """Parse dev-mode JWT (format: dev.<base64url-payload>.sig). Returns None if not a dev token."""
    import base64
    import json
    import os

    if not token.startswith("dev.") or os.getenv("DEV_LOGIN_ENABLED") != "true":
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    try:
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception:
        return None


async def get_current_user(request: Request) -> CurrentUser:
    token = _extract_token(request)

    # Dev token shortcut (DEV_LOGIN_ENABLED=true only)
    dev_payload = _parse_dev_token(token)
    if dev_payload is not None:
        return CurrentUser(
            user_id=uuid.UUID("00000000-0000-4000-a000-000000000010"),
            email=dev_payload.get("email", "admin@dev.local"),
            role=UserRole(dev_payload.get("role", "ADMIN")),
            team_id=None,
        )

    # Service token shortcut: external systems call with `Bearer svc-...`.
    # Resolved against auth.service_tokens (sha256 hash). Synthesizes an ADMIN
    # identity. Non-`svc-` tokens fall through to the JWT path below (unchanged).
    if token.startswith(SERVICE_TOKEN_PREFIX):
        svc_service = ServiceTokenService()
        async with AsyncSessionLocal() as session:
            svc_tok = await svc_service.verify(session, hash_token(token))
        if svc_tok is None:
            raise HTTPException(status_code=401, detail="Invalid or expired service token")
        return CurrentUser(
            user_id=uuid.UUID("00000000-0000-4000-a000-000000000011"),
            email="service-token@admin.local",
            role=UserRole.ADMIN,
            team_id=None,
            is_service_token=True,
            service_token_id=svc_tok.id,
        )

    verifier: JWTVerifier = request.app.state.jwt_verifier

    try:
        payload = verifier.verify(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # ── 내부 admin JWT: `sub` 가 곧 auth.users.id 이고 `role` 클레임이 있다 ──
    #
    # ⚠️ IdP 가 직접 발급한 id_token 은 이 형상이 **아니다.** admin-ui 는 OIDC 콜백에서
    #    받은 id_token 을 그대로 `admin_jwt` 쿠키에 굽고(app/api/auth/callback/route.ts),
    #    그 토큰의 `sub` 는 **IdP 의 subject** 이지 우리 DB PK 가 아니다. 그리고 `role`
    #    클레임도 없다(IdP 는 groups 를 준다). 예전엔 그 토큰이 아래 한 줄을 그대로 타서
    #    두 가지로 깨졌다:
    #
    #      1. `uuid.UUID(payload["sub"])` — Cognito sub 는 UUID 라 파싱은 되지만 그 값은
    #         DB 에 없는 id 다. created_by FK 가 붙는 모든 쓰기(모델 생성·단가 설정)가
    #         **FK 위반 500** 으로 죽었다.
    #      2. Okta/Keycloak/Entra 의 sub 는 UUID 가 아니다("00u1a2b3…"). 그러면
    #         `uuid.UUID()` 가 **ValueError** 를 던지는데 이 함수는 그걸 잡지 않으므로
    #         401 도 403 도 아닌 **500** 이 나간다 — 인증 실패가 서버 오류로 보인다.
    #
    #    그래서 IdP 토큰은 DB 신원으로 해석한다. 판정 기준은 **발급자(iss)** 다 —
    #    IdP 에 custom mapper 로 `role` 클레임을 싣는 설정이면, 그 클레임을 그대로
    #    신뢰하는 것이 권한 상승 경로가 된다. 내부 admin JWT 경로는 한 글자도 바뀌지
    #    않는다(issuer 가 IdP 가 아니고 role 이 있으면 아래로 온다).
    from app.core.config import get_settings

    oidc_issuer = get_settings().OIDC_ISSUER_URL
    if (oidc_issuer and payload.get("iss") == oidc_issuer) or "role" not in payload:
        return await _resolve_idp_identity(payload)

    team_id_raw = payload.get("team_id")
    try:
        return CurrentUser(
            user_id=uuid.UUID(payload["sub"]),
            email=payload.get("email", ""),
            role=UserRole(payload["role"]),
            team_id=uuid.UUID(team_id_raw) if team_id_raw else None,
        )
    except (ValueError, TypeError, KeyError):
        # sub 가 UUID 가 아니거나 role 이 enum 에 없는 값 → 우리 토큰이 아니다.
        # 500 이 아니라 401.
        raise HTTPException(status_code=401, detail="Invalid token claims")


async def _resolve_idp_identity(claims: dict) -> CurrentUser:
    """IdP id_token 클레임 → DB 신원.

    ``auth.users`` 를 ``sso_subject`` 로 조회한다. 여기서 프로비저닝은 **하지 않는다** —
    이 경로는 admin-ui 세션 검증이고, 프로비저닝은 ``POST /v1/auth/exchange``
    (``services/oidc_service.py``)의 책임이다. 두 곳에서 만들면 같은 사용자가 서로 다른
    팀/역할로 두 번 생길 수 있다.

    role 은 DB 값이 아니라 **현재 토큰의 그룹**으로 판정한다. IdP 에서 관리자 그룹을
    떼어낸 사용자가 DB 에 ADMIN 으로 남아 있는 동안 계속 ADMIN 으로 도는 것을 막아야
    한다(권한 회수가 즉시 반영되어야 하는 방향). 단, 그룹으로 ADMIN 이 아니어도 DB
    role 이 더 높으면 그쪽을 쓴다 — 그룹 매핑을 안 쓰는 운영 형태를 깨지 않기 위해서다.
    """
    from app.core.oidc_identity import derive_role, extract_claims, required_group_missing
    from app.repositories.user_repository import UserRepository

    parsed = extract_claims(claims)
    if not parsed.subject:
        raise HTTPException(status_code=401, detail="Token is missing a subject claim")

    missing = required_group_missing(parsed.groups)
    if missing is not None:
        logger.warning("auth.idp_required_group_missing", subject=parsed.subject, required=missing)
        raise HTTPException(status_code=403, detail="required_group_missing")

    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_sso_subject(parsed.subject)

    if user is None:
        # ⚠️ 401 이 아니라 403 이다. 토큰은 유효하다 — 이 사람이 아직 프로비저닝되지
        #    않은 것이다. 401 을 주면 admin-ui middleware 가 쿠키를 지우고 로그인으로
        #    되돌리는데, IdP 세션이 살아 있으니 즉시 같은 토큰으로 돌아와 무한 루프가 된다.
        logger.warning(
            "auth.idp_user_not_provisioned", subject=parsed.subject, email=parsed.email
        )
        raise HTTPException(status_code=403, detail="user_not_provisioned")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="user_deactivated")

    role_from_groups = derive_role(parsed.email or (user.email or ""), parsed.groups)
    role = UserRole.ADMIN if UserRole.ADMIN in (role_from_groups, user.role) else user.role

    return CurrentUser(
        user_id=user.id,
        email=user.email or parsed.email,
        role=role,
        team_id=user.team_id,
    )


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


async def require_admin_or_team_leader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in (UserRole.ADMIN, UserRole.TEAM_LEADER):
        raise HTTPException(status_code=403, detail="Admin or Team Leader role required")
    return user


def require_team_leader_of(team_id: uuid.UUID):
    """Returns a dependency that verifies the user is ADMIN or TEAM_LEADER of the specified team."""

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role == UserRole.ADMIN:
            return user
        if user.role == UserRole.TEAM_LEADER and user.team_id == team_id:
            return user
        raise HTTPException(status_code=403, detail="Not authorized for this team")

    return _check
