# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Integration test fixtures.

Uses real FastAPI test client with mocked DB/Redis for router-level testing.
Full DB integration tests use testcontainers (requires Docker).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.session_double import wire_savepoint
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.auth import CurrentUser, JWTVerifier
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.db import SESSION_STATE_ATTR
from app.core.encryption import AESEncryptionService
from app.models.auth import UserRole
from app.services.analytics_service import AnalyticsService
from app.services.budget_service import BudgetService
from app.services.cli_service import CLIService
from app.services.key_service import KeyService
from app.services.model_service import ModelService
from app.services.rate_limit_service import RateLimitService
from app.services.user_team_service import UserTeamService

TEST_ENCRYPTION_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

# JWT test keypair (RS256)
# In real integration tests, generate an RSA key pair. For router tests, we mock the verifier.
ADMIN_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_TEAM_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
LEADER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")


def _build_test_app() -> FastAPI:
    """Build a FastAPI app with mocked dependencies for integration tests."""
    # dashboard/monitoring/my 는 app.state 서비스를 쓰지 않는 순수 세션 쿼리 라우터다.
    # 예전엔 테스트 앱에 등록되지 않아 limit 경계·KST 집계 같은 계약을 통합테스트가
    # 전혀 밟지 못했다(요청하면 404 라서 '통과'처럼 보였다).
    from app.routers import (
        analytics,
        audit_reconcile,
        budgets,
        cli,
        dashboard,
        internal,
        keys,
        models,
        monitoring,
        my,
        rate_limits,
        users,
    )

    app = FastAPI(title="Test Admin API")

    # ⚠️ 핸들러를 여기서 손으로 복제하지 말 것. 예전엔 복제본이라 main.py 에만 추가된
    #    핸들러(422 정규화 / 미처리 예외 최후의 그물)를 통합테스트가 전혀 검증하지
    #    못했고, 프로덕션과 테스트의 에러 봉투가 조용히 갈라졌다.
    from app.main import register_exception_handlers

    register_exception_handlers(app)

    app.include_router(keys.router)
    app.include_router(budgets.router)
    app.include_router(models.router)
    app.include_router(rate_limits.router)
    app.include_router(users.router)
    app.include_router(analytics.router)
    app.include_router(cli.router)
    app.include_router(internal.router)
    app.include_router(audit_reconcile.router)
    app.include_router(dashboard.router)
    app.include_router(monitoring.router)
    app.include_router(my.router)

    # ⚠️ 프로덕션 create_app() 과 같은 배선. 이 호출이 없으면 통합테스트는 커밋이
    #    응답 **뒤에** 일어나는 예전 앱 형상을 검증하게 되고, 커밋 실패가 200 으로
    #    나가는 회귀를 통합테스트가 전혀 잡지 못한다(핸들러 손복제와 같은 부류의 틈).
    #    반드시 모든 include_router 뒤여야 한다 — 뒤에 추가된 라우터는 승격되지 않는다.
    from app.core.db import install_commit_before_response

    install_commit_before_response(app)

    return app


class MockJWTVerifier(JWTVerifier):
    """Always returns a predetermined payload based on the token value."""

    def verify(self, token: str) -> dict:
        if token == "admin-token":
            return {"sub": str(ADMIN_USER_ID), "email": "admin@test.com", "role": "ADMIN", "team_id": str(ADMIN_TEAM_ID)}
        elif token == "leader-token":
            return {"sub": str(LEADER_USER_ID), "email": "leader@test.com", "role": "TEAM_LEADER", "team_id": str(ADMIN_TEAM_ID)}
        elif token == "dev-token":
            return {"sub": str(DEV_USER_ID), "email": "dev@test.com", "role": "DEVELOPER", "team_id": str(ADMIN_TEAM_ID)}
        else:
            from jose import JWTError
            raise JWTError("Invalid token")


def _mock_db_result() -> MagicMock:
    """A benign SQLAlchemy Result mock: every access path yields 'empty'.

    Router-level integration tests mock the repositories, but some services also
    issue queries directly on the session (e.g. KeyService.list_keys' email
    enrichment select, or a real UserRepository.get_user via session.get). Those
    calls must not reach a real Postgres — return an empty result for all shapes.
    """
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalar_one.return_value = 0  # COUNT/SUM aggregates → 0 (analytics repo)
    result.scalar.return_value = 0
    result.one_or_none.return_value = None
    result.all.return_value = []
    result.first.return_value = None
    result.__iter__ = MagicMock(return_value=iter([]))  # `for row in result` → no rows
    scalars = MagicMock()
    scalars.all.return_value = []
    scalars.first.return_value = None
    result.scalars.return_value = scalars
    return result


async def _mock_get_db_session(request: Request):
    """Override for app.core.db.get_db_session — a mock AsyncSession.

    The integration conftest promises "mocked DB" but the routers depend on the
    real get_db_session (which builds a live asyncpg engine). Override it so no
    router test ever opens a real connection; repository-level patches still take
    precedence where a test sets them.

    ⚠️ 진짜 get_db_session 처럼 request.state 에 세션을 매달아야 한다. 안 매달면
       CommittingRoute 가 세션을 못 찾아 그대로 통과하고, 통합테스트는 커밋 경로를
       **한 번도** 밟지 않는다(라우트만 승격되고 실제 동작은 미검증). in_transaction()
       도 실제 세션처럼 커밋/롤백 뒤 False 로 떨어지게 해서, 승격된 라우트가 커밋한
       세션을 종료 블록이 또 커밋하지 않는지까지 같은 배선으로 확인한다.
    """
    session = AsyncMock()
    # begin_nested 는 실물에서 sync 호출 → async CM (session_double 주석 참조)
    wire_savepoint(session)

    def _leader_scoped_result():
        """auth.teams.leader_user_id 인가 조회 — leader-token 의 TEAM_LEADER 가
        ADMIN_TEAM_ID 의 리더로 지정된 상태를 흉내낸다(엄격 정책: 리더인 팀만 열람)."""
        result = _mock_db_result()
        scalars = MagicMock()
        scalars.all.return_value = [ADMIN_TEAM_ID]
        result.scalars.return_value = scalars
        return result

    async def _execute(stmt, *args, **kwargs):
        text = str(stmt)
        if "leader_user_id" in text:
            return _leader_scoped_result()
        return _mock_db_result()

    session.execute = AsyncMock(side_effect=_execute)
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()

    open_tx = {"value": True}
    session.in_transaction = MagicMock(side_effect=lambda: open_tx["value"])

    async def _close(*_args, **_kwargs):
        open_tx["value"] = False

    session.commit = AsyncMock(side_effect=_close)
    session.rollback = AsyncMock(side_effect=_close)

    setattr(request.state, SESSION_STATE_ATTR, session)
    yield session


@pytest.fixture
def test_app() -> FastAPI:
    from app.core.db import get_db_session

    app = _build_test_app()
    # Mocked DB: routers must never touch a real Postgres in router-level tests.
    app.dependency_overrides[get_db_session] = _mock_get_db_session

    # Mock dependencies
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.delete = AsyncMock()
    mock_redis.ping = AsyncMock()
    mock_redis.srem = AsyncMock()
    # Pipeline support (KeyService.issue_key buffers setex/sadd through a pipe):
    # redis-py's async pipeline buffers commands synchronously, only execute() awaits.
    mock_redis.setex = MagicMock()
    mock_redis.sadd = MagicMock()
    mock_redis.execute = AsyncMock()
    mock_redis.pipeline = MagicMock(return_value=mock_redis)

    encryption = AESEncryptionService(TEST_ENCRYPTION_KEY)
    cache_mgr = CacheInvalidationManager(mock_redis)

    app.state.redis = mock_redis
    app.state.cache_mgr = cache_mgr
    app.state.jwt_verifier = MockJWTVerifier()

    key_service = KeyService(encryption=encryption, cache_mgr=cache_mgr)
    app.state.key_service = key_service
    app.state.cli_service = CLIService(key_service=key_service)
    app.state.budget_service = BudgetService(cache_mgr=cache_mgr)
    app.state.model_service = ModelService(cache_mgr=cache_mgr)
    app.state.rate_limit_service = RateLimitService(cache_mgr=cache_mgr)
    app.state.user_team_service = UserTeamService(cache_mgr=cache_mgr, key_service=key_service)
    app.state.analytics_service = AnalyticsService()

    return app


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin-token"}


@pytest.fixture
def leader_headers() -> dict[str, str]:
    return {"Authorization": "Bearer leader-token"}


@pytest.fixture
def dev_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}
