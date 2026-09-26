"""The real gateway app serves GET /v1/policy, and only with a VK."""
from __future__ import annotations

import pytest

from app.main import app
from app.middleware.auth import EXEMPT_PATHS

pytestmark = pytest.mark.unit


def test_gateway_app_exposes_get_policy():
    # FastAPI keeps included routers lazily, so read the resolved OpenAPI paths.
    paths = app.openapi()["paths"]

    assert "get" in paths.get("/v1/policy", {})


def test_policy_route_requires_a_vk():
    assert "/v1/policy" not in EXEMPT_PATHS


def test_policy_route_accepts_a_vk_like_the_other_client_paths():
    # policy-helper authenticates with the user's VK. Paths missing from the
    # explicit list fall through to JWT-only and reject every VK with 401
    # (caught on US dev: the helper got 401 while the same VK served /v1/messages).
    from app.services.auth_service import _DUAL_STRATEGY, resolve_auth_strategy

    assert resolve_auth_strategy("/v1/policy") is _DUAL_STRATEGY
    assert resolve_auth_strategy("/v1/policy") is resolve_auth_strategy("/v1/models")
