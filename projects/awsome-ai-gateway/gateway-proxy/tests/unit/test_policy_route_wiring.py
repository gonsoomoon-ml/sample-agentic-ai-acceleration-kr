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
