# Copyright 2026 © Amazon.com and Affiliates.
"""invoke-backend 프로필의 default_model 폴백 — /v1/messages Bedrock 경로.

이전엔 invoke profile 의 default_model 이 죽은 필드였다(요청 모델이 항상 우선,
미등록은 404). codex 경로와 같은 의미로 미등록 이름을 default 로 대체한다.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers.messages import _resolve_bedrock_with_default
from app.schemas.routing import RoutingProfileSchema
from app.services.router_service import ModelInactiveError


def _profile(default_model: str | None) -> RoutingProfileSchema:
    return RoutingProfileSchema(
        client="claude-code",
        backend="invoke",
        region="ap-south-1",
        default_model=default_model,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_alias_falls_back_to_default():
    """미등록 이름 → profile.default_model 로 대체 (codex 경로와 같은 의미)."""
    profile = _profile("claude-haiku-4-5-20251001")
    sentinel = MagicMock(name="default_cfg")

    with patch("app.routers.messages._router_service") as rs:
        rs.resolve_bedrock_model = AsyncMock(side_effect=[LookupError("not found"), sentinel])
        cfg = await _resolve_bedrock_with_default(
            AsyncMock(), AsyncMock(), "claude-opus-5[1m]", profile, "claude-code"
        )

    assert cfg is sentinel
    assert rs.resolve_bedrock_model.await_args_list[1].args[2] == "claude-haiku-4-5-20251001"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_default_still_404():
    """default_model 없는 프로필은 종전대로 404 — 조용한 대체가 아니다."""
    profile = _profile(None)
    with patch("app.routers.messages._router_service") as rs:
        rs.resolve_bedrock_model = AsyncMock(side_effect=LookupError("not found"))
        with pytest.raises(LookupError):
            await _resolve_bedrock_with_default(
                AsyncMock(), AsyncMock(), "bogus", profile, "claude-code"
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_profile_still_404():
    with patch("app.routers.messages._router_service") as rs:
        rs.resolve_bedrock_model = AsyncMock(side_effect=LookupError("not found"))
        with pytest.raises(LookupError):
            await _resolve_bedrock_with_default(
                AsyncMock(), AsyncMock(), "bogus", None, "claude-code"
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inactive_never_falls_back():
    """INACTIVE 는 kill switch — default 로 조용히 대체하면 안 된다."""
    profile = _profile("claude-haiku-4-5-20251001")
    with patch("app.routers.messages._router_service") as rs:
        rs.resolve_bedrock_model = AsyncMock(
            side_effect=ModelInactiveError("inactive")
        )
        with pytest.raises(ModelInactiveError):
            await _resolve_bedrock_with_default(
                AsyncMock(), AsyncMock(), "killed-model", profile, "claude-code"
            )
    # default 재시도가 없었어야 한다
    assert rs.resolve_bedrock_model.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_broken_default_propagates():
    """default 자체의 resolve 실패는 숨기지 않는다 — 진짜 서버 결함."""
    profile = _profile("claude-haiku-4-5-20251001")
    with patch("app.routers.messages._router_service") as rs:
        rs.resolve_bedrock_model = AsyncMock(
            side_effect=[LookupError("requested not found"), LookupError("default not found")]
        )
        with pytest.raises(LookupError, match="default not found"):
            await _resolve_bedrock_with_default(
                AsyncMock(), AsyncMock(), "bogus", profile, "claude-code"
            )
