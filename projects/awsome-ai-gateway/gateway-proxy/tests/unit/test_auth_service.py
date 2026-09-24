# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.domain import AuthType, Role
from app.services.auth_service import (
    DualAuthStrategy,
    JWTAuthStrategy,
    VKAuthStrategy,
    _is_jwt_token,
    resolve_auth_strategy,
)


def test_is_jwt_token_true():
    assert _is_jwt_token("a.b.c") is True


def test_is_jwt_token_false():
    assert _is_jwt_token("vk-abc123def456") is False


def test_resolve_auth_strategy_bedrock():
    strategy = resolve_auth_strategy("/model/us.anthropic.claude/invoke")
    assert isinstance(strategy, VKAuthStrategy)


def test_resolve_auth_strategy_openai_chat_completions():
    # FR-1.4 A안 (2026-04-17): 임시로 VK DUAL 허용. Week 3 JWT 복귀 예정.
    strategy = resolve_auth_strategy("/v1/chat/completions")
    assert isinstance(strategy, DualAuthStrategy)


def test_resolve_auth_strategy_openai_completions():
    # FR-1.4 A안 (2026-04-17): 임시로 VK DUAL 허용. Week 3 JWT 복귀 예정.
    strategy = resolve_auth_strategy("/v1/completions")
    assert isinstance(strategy, DualAuthStrategy)


def test_resolve_auth_strategy_unknown_v1_still_jwt():
    # 범용 /v1/* 경로는 여전히 JWT 전용이어야 한다 (DUAL 화이트리스트에 추가되지 않은 경로).
    strategy = resolve_auth_strategy("/v1/embeddings")
    assert isinstance(strategy, JWTAuthStrategy)


def test_resolve_auth_strategy_usage_me():
    strategy = resolve_auth_strategy("/v1/usage/me")
    assert isinstance(strategy, DualAuthStrategy)


def test_resolve_auth_strategy_messages():
    strategy = resolve_auth_strategy("/v1/messages")
    assert isinstance(strategy, DualAuthStrategy)


def test_resolve_auth_strategy_count_tokens():
    # KI-05: Claude Code calls /v1/messages/count_tokens with a VK.
    # This must map to DUAL (VK or JWT) rather than JWT-only.
    strategy = resolve_auth_strategy("/v1/messages/count_tokens")
    assert isinstance(strategy, DualAuthStrategy)


def test_resolve_auth_strategy_models():
    strategy = resolve_auth_strategy("/v1/models")
    assert isinstance(strategy, DualAuthStrategy)


def test_resolve_auth_strategy_health():
    strategy = resolve_auth_strategy("/health")
    assert strategy is None


@pytest.mark.asyncio
async def test_vk_auth_cache_hit():
    redis = AsyncMock()
    auth_data = {
        "user_id": "u1",
        "team_id": "t1",
        "dept_id": "d1",
        "roles": ["USER"],
        "auth_type": "VIRTUAL_KEY",
        "key_id": "k1",
        "allowed_models": None,
    }
    redis.get = AsyncMock(return_value=json.dumps(auth_data).encode())

    key = "vk-test12345678"
    strategy = VKAuthStrategy()
    auth = await strategy.authenticate(f"Bearer {key}", redis, None)

    assert auth.user_id == "u1"
    assert auth.auth_type == AuthType.VIRTUAL_KEY


@pytest.mark.asyncio
async def test_vk_auth_missing_token():
    strategy = VKAuthStrategy()
    with pytest.raises(ValueError):
        await strategy.authenticate("", AsyncMock(), None)


@pytest.mark.asyncio
async def test_vk_auth_cache_hit_with_allowed_models():
    """AuthContext 캐시에 allowed_models 스냅샷이 담겨 있으면 그대로 로드."""
    redis = AsyncMock()
    auth_data = {
        "user_id": "u1",
        "team_id": "t1",
        "dept_id": "",
        "roles": ["USER"],
        "auth_type": "VIRTUAL_KEY",
        "key_id": None,
        "allowed_models": ["claude-haiku"],
    }
    redis.get = AsyncMock(return_value=json.dumps(auth_data).encode())

    strategy = VKAuthStrategy()
    auth = await strategy.authenticate("Bearer vk-abc", redis, None)

    assert auth.allowed_models == ["claude-haiku"]


@pytest.mark.asyncio
async def test_vk_auth_db_fallback_loads_team_allowed_models(monkeypatch):
    """캐시 miss → DB 경로에서 team_allowed_models 조회하여 AuthContext에 주입."""
    from app.services import auth_service as auth_mod

    redis = AsyncMock()
    redis.get = AsyncMock(
        side_effect=[
            None,  # key:cache:vk:* miss
            b"user-123",  # key:vk:* → user_id
        ]
    )
    redis.setex = AsyncMock()

    user = MagicMock()
    user.id = "user-123"
    user.team_id = "team-abc"
    user.is_active = True
    user.sso_subject = "sub-user-123"  # must be a string for Pydantic

    db = AsyncMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user

    tam_result = MagicMock()
    tam_scalars = MagicMock()
    tam_scalars.all.return_value = ["claude-sonnet", "claude-haiku"]
    tam_result.scalars.return_value = tam_scalars

    # 3rd call: user_allowed_clients query → empty → team/org 폴백 조회도 빈 결과
    uac_result = MagicMock()
    uac_scalars = MagicMock()
    uac_scalars.all.return_value = []
    uac_result.scalars.return_value = uac_scalars

    db.execute = AsyncMock(
        side_effect=[user_result, tam_result, uac_result, uac_result, uac_result]
    )

    strategy = auth_mod.VKAuthStrategy()
    auth = await strategy.authenticate("Bearer vk-xyz", redis, db)

    assert auth.team_id == "team-abc"
    assert auth.allowed_models == ["claude-sonnet", "claude-haiku"]
    assert auth.allowed_clients is None  # empty rows → None


@pytest.mark.asyncio
async def test_vk_auth_db_fallback_empty_team_means_allow_all():
    """team_allowed_models 엔트리 0개 → allowed_models=None (전체 허용)."""
    from app.services import auth_service as auth_mod

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=[None, b"user-123"])
    redis.setex = AsyncMock()

    user = MagicMock()
    user.id = "user-123"
    user.team_id = "team-abc"
    user.is_active = True
    user.sso_subject = "sub-user-456"  # must be a string for Pydantic

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user

    tam_result = MagicMock()
    tam_scalars = MagicMock()
    tam_scalars.all.return_value = []
    tam_result.scalars.return_value = tam_scalars

    # 3rd call: user_allowed_clients query → empty → team/org 폴백 조회도 빈 결과
    uac_result = MagicMock()
    uac_scalars = MagicMock()
    uac_scalars.all.return_value = []
    uac_result.scalars.return_value = uac_scalars

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[user_result, tam_result, uac_result, uac_result, uac_result]
    )

    strategy = auth_mod.VKAuthStrategy()
    auth = await strategy.authenticate("Bearer vk-xyz", redis, db)

    assert auth.allowed_models is None
    assert auth.allowed_clients is None


# ── allowed_clients user > team > org 폴백 (alembic 0038) ─────────────────────


def _vk_user_and_db(*, client_rows_sequence):
    """캐시 miss → DB 경로. client_rows_sequence 는 user→team→org 조회의
    scalars().all() 결과를 순서대로 — 조회가 멈추면 뒤 항목은 소비되지 않는다."""
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=[None, b"user-123"])
    redis.setex = AsyncMock()

    user = MagicMock()
    user.id = "user-123"
    user.team_id = "team-abc"
    user.is_active = True
    user.sso_subject = "sub-fallback"

    def _scalars(rows):
        r = MagicMock()
        s = MagicMock()
        s.all.return_value = rows
        r.scalars.return_value = s
        return r

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    uam_result = _scalars(["any-model"])  # user override 있음 → team_models 생략

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[user_result, uam_result]
        + [_scalars(rows) for rows in client_rows_sequence]
    )
    return redis, db


@pytest.mark.asyncio
async def test_vk_auth_clients_user_rows_win():
    """user_allowed_clients 행이 있으면 team/org 조회 없이 그것만 쓴다."""
    from app.services import auth_service as auth_mod

    redis, db = _vk_user_and_db(client_rows_sequence=[["claude-code"]])
    auth = await auth_mod.VKAuthStrategy().authenticate("Bearer vk-1", redis, db)

    assert auth.allowed_clients == ["claude-code"]
    assert db.execute.await_count == 3  # user + uam + uac 만


@pytest.mark.asyncio
async def test_vk_auth_clients_team_fallback():
    """user 행 0개 → team_allowed_clients 로 폴백."""
    from app.services import auth_service as auth_mod

    redis, db = _vk_user_and_db(client_rows_sequence=[[], ["cowork"]])
    auth = await auth_mod.VKAuthStrategy().authenticate("Bearer vk-2", redis, db)

    assert auth.allowed_clients == ["cowork"]


@pytest.mark.asyncio
async def test_vk_auth_clients_org_fallback():
    """user·team 모두 0개 → org_allowed_clients 로 폴백."""
    from app.services import auth_service as auth_mod

    redis, db = _vk_user_and_db(client_rows_sequence=[[], [], ["codex", "cowork"]])
    auth = await auth_mod.VKAuthStrategy().authenticate("Bearer vk-3", redis, db)

    assert sorted(auth.allowed_clients) == ["codex", "cowork"]


@pytest.mark.asyncio
async def test_vk_auth_clients_all_empty_means_unrestricted():
    """user/team/org 전부 0개 → None(전체 허용)."""
    from app.services import auth_service as auth_mod

    redis, db = _vk_user_and_db(client_rows_sequence=[[], [], []])
    auth = await auth_mod.VKAuthStrategy().authenticate("Bearer vk-4", redis, db)

    assert auth.allowed_clients is None
