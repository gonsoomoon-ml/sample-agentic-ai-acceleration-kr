# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Admin-UI 로그인 — Cognito ROPC(USER_PASSWORD_AUTH) + OIDC 검증 + 자체 세션 JWT 발급.

기존 ``/api/auth/dev-login`` (role 을 직접 선택하는 MVP bypass) 를 대체하는 실제
Cognito 로그인 경로. 흐름:

    1. admin-ui 커스텀 로그인 폼에서 받은 email/password 로 Cognito
       ``InitiateAuth(USER_PASSWORD_AUTH)`` 호출 → id_token 획득.
       - 관리자가 콘솔/CLI 로 새로 만든 계정은 임시 비밀번호 상태(FORCE_CHANGE_PASSWORD)
         라 여기서 ``NEW_PASSWORD_REQUIRED`` 챌린지가 뜬다 — ``complete_new_password``
         가 이어서 처리한다(``routers/auth_admin.py`` 의 두 번째 엔드포인트).
    2. ``OIDCService.authenticate_for_admin_ui`` 로 id_token 서명 검증 + Cognito
       그룹(``ClaudeAdmin``/``Claude_<team>``) 기반 role/team 매핑 + user upsert.
       TEAM_LEADER 는 Cognito 그룹이 아니라 admin-ui 에서 수동 지정된 값을 그대로
       유지한다 (``_upsert_user`` 참고). role 이 ADMIN/TEAM_LEADER 가 아니면 거부.
    3. ``admin_jwt_signer`` 로 admin-api 자체 서명 세션 JWT(RS256) 발급.

gateway-cli 용 ``/v1/auth/exchange`` (VK 발급) 와는 별개의 진입점이지만, 같은
Cognito User Pool + 같은 ``OIDCService`` 인스턴스(claim 검증/그룹 매핑/user upsert)를
공유한다.
"""
from __future__ import annotations

import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_jwt_signer import sign_admin_session_jwt
from app.models.auth import User
from app.services.oidc_service import OIDCAuthError, OIDCService

logger = structlog.get_logger()


class AdminLoginError(Exception):
    """Cognito 인증 실패 (401/400 로 매핑)."""


class AdminLoginChallengeError(Exception):
    """추가 조치가 필요한 Cognito 챌린지. ``NEW_PASSWORD_REQUIRED`` 는 admin-ui 가
    비밀번호 설정 화면으로 이어서 처리하고, 그 외(예: MFA)는 아직 미지원이라 라우터가
    401 로 거부한다."""

    def __init__(self, challenge_name: str, cognito_session: str) -> None:
        super().__init__(challenge_name)
        self.challenge_name = challenge_name
        self.cognito_session = cognito_session


class AdminLoginRateLimitedError(Exception):
    """이메일 또는 IP 기준 로그인 실패가 임계치를 넘음 (429 로 매핑)."""


# Redis 기반 로그인 실패 잠금 — 무차별 대입/credential stuffing 방어.
# Cognito 자체 스로틀링(계정 단위, 조악함)만으로는 IP 단위 분산 시도를 못 막는다.
# 이메일 키 + IP 키 둘 다 체크해 "한 계정을 여러 IP로 공격"과 "한 IP로 여러 계정을
# 공격" 양쪽을 모두 방어한다. fail-open: Redis 장애 시 로그인 자체를 막지 않는다.
_LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
_LOGIN_RATE_LIMIT_WINDOW_SEC = 900  # 15분


class AdminAuthService:
    """email/password → admin-ui 세션 JWT."""

    def __init__(
        self,
        cognito_client,
        app_client_id: str,
        oidc_service: OIDCService,
        redis=None,
    ) -> None:
        self._cognito = cognito_client
        self._app_client_id = app_client_id
        self._oidc_service = oidc_service
        self._redis = redis

    def _rate_limit_keys(self, *, email: str, ip_address: str) -> list[str]:
        return [
            f"admin_login_fail:email:{email.strip().lower()}",
            f"admin_login_fail:ip:{ip_address}",
        ]

    async def _check_rate_limit(self, *, email: str, ip_address: str) -> None:
        if self._redis is None:
            return
        try:
            for key in self._rate_limit_keys(email=email, ip_address=ip_address):
                raw = await self._redis.get(key)
                if raw is not None and int(raw) >= _LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
                    raise AdminLoginRateLimitedError(
                        "Too many failed login attempts. Please try again later."
                    )
        except AdminLoginRateLimitedError:
            raise
        except Exception:
            logger.warning("admin_auth.rate_limit_check_failed", exc_info=True)

    async def _register_failure(self, *, email: str, ip_address: str) -> None:
        if self._redis is None:
            return
        try:
            for key in self._rate_limit_keys(email=email, ip_address=ip_address):
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, _LOGIN_RATE_LIMIT_WINDOW_SEC, nx=True)
                await pipe.execute()
        except Exception:
            logger.warning("admin_auth.rate_limit_register_failed", exc_info=True)

    async def _clear_failures(self, *, email: str, ip_address: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(*self._rate_limit_keys(email=email, ip_address=ip_address))
        except Exception:
            logger.warning("admin_auth.rate_limit_clear_failed", exc_info=True)

    async def login(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        ip_address: str = "0.0.0.0",
    ) -> tuple[User, str, int]:
        """Returns (user, admin_jwt_token, expires_at_epoch_seconds)."""
        await self._check_rate_limit(email=email, ip_address=ip_address)

        try:
            resp = await asyncio.to_thread(
                self._cognito.initiate_auth,
                ClientId=self._app_client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": email, "PASSWORD": password},
            )
        except (
            self._cognito.exceptions.NotAuthorizedException,
            self._cognito.exceptions.UserNotFoundException,
        ) as e:
            await self._register_failure(email=email, ip_address=ip_address)
            raise AdminLoginError("invalid email or password") from e
        except self._cognito.exceptions.UserNotConfirmedException as e:
            await self._register_failure(email=email, ip_address=ip_address)
            raise AdminLoginError("user not confirmed") from e
        except Exception as e:
            logger.warning("admin_auth.cognito_initiate_auth_failed", error=str(e))
            await self._register_failure(email=email, ip_address=ip_address)
            raise AdminLoginError("authentication failed") from e

        if resp.get("ChallengeName"):
            # 예: NEW_PASSWORD_REQUIRED — admin 이 콘솔/CLI 로 사용자를 만들면 임시
            # 비밀번호가 발급되고 첫 로그인 시 반드시 이 챌린지를 통과해야 함.
            # 자격증명 자체는 맞았으므로 실패로 집계하지 않는다.
            raise AdminLoginChallengeError(resp["ChallengeName"], resp.get("Session", ""))

        result = await self._finish_login(session, resp)
        await self._clear_failures(email=email, ip_address=ip_address)
        return result

    async def complete_new_password(
        self,
        session: AsyncSession,
        *,
        email: str,
        new_password: str,
        cognito_session: str,
        ip_address: str = "0.0.0.0",
    ) -> tuple[User, str, int]:
        """``NEW_PASSWORD_REQUIRED`` 챌린지 응답 — 임시 비밀번호를 영구 비밀번호로 교체하며
        로그인을 완료한다. Returns (user, admin_jwt_token, expires_at_epoch_seconds)."""
        await self._check_rate_limit(email=email, ip_address=ip_address)

        try:
            resp = await asyncio.to_thread(
                self._cognito.respond_to_auth_challenge,
                ClientId=self._app_client_id,
                ChallengeName="NEW_PASSWORD_REQUIRED",
                Session=cognito_session,
                ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_password},
            )
        except self._cognito.exceptions.InvalidPasswordException as e:
            # Cognito 원문 예외 메시지를 그대로 클라이언트에 노출하지 않는다 — 내부
            # 정책 문구/스택 정보가 섞여 나올 수 있어 표준화된 메시지로 대체.
            logger.info("admin_auth.invalid_password_policy", error=str(e))
            await self._register_failure(email=email, ip_address=ip_address)
            raise AdminLoginError(
                "Password does not meet the policy requirements "
                "(minimum length, upper/lower case, number, symbol)."
            ) from e
        except (
            self._cognito.exceptions.NotAuthorizedException,
            self._cognito.exceptions.CodeMismatchException,
            self._cognito.exceptions.ExpiredCodeException,
        ) as e:
            await self._register_failure(email=email, ip_address=ip_address)
            raise AdminLoginError("session expired — please sign in again") from e
        except Exception as e:
            logger.warning("admin_auth.respond_to_challenge_failed", error=str(e))
            await self._register_failure(email=email, ip_address=ip_address)
            raise AdminLoginError("could not set new password") from e

        if resp.get("ChallengeName"):
            # 예: 다음 단계로 MFA 등이 이어지는 경우 — 아직 미지원.
            raise AdminLoginChallengeError(resp["ChallengeName"], resp.get("Session", ""))

        result = await self._finish_login(session, resp)
        await self._clear_failures(email=email, ip_address=ip_address)
        return result

    async def _finish_login(self, session: AsyncSession, cognito_resp: dict) -> tuple[User, str, int]:
        """InitiateAuth/RespondToAuthChallenge 가 챌린지 없이 토큰을 반환한 뒤 공통 경로."""
        id_token = cognito_resp.get("AuthenticationResult", {}).get("IdToken")
        if not id_token:
            raise AdminLoginError("no id_token returned by Cognito")

        try:
            user = await self._oidc_service.authenticate_for_admin_ui(session, token=id_token)
        except OIDCAuthError as e:
            raise AdminLoginError(str(e)) from e

        token, expires_at = sign_admin_session_jwt(user)
        return user, token, expires_at
