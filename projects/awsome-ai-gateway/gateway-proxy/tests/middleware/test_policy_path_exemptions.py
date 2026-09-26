"""GET /v1/policy must pass the gateway's per-request controls untouched.

policy-helper runs outside Claude Code with its own User-Agent, so client
identification cannot tell it is Claude Code; and a policy fetch is not model
usage, so it must never be budgeted, rate-limited or downgraded.
"""
from __future__ import annotations

import pytest

from app.middleware.budget import BudgetMiddleware
from app.middleware.client_authz import ClientAuthorizationMiddleware
from app.middleware.downgrade import _path_eligible

pytestmark = pytest.mark.unit


class Ctx:
    allowed_clients = ["cowork"]  # whitelist that does not include the helper's client


def scope_for(path: str) -> dict:
    return {
        "type": "http", "method": "GET", "path": path, "headers": [],
        "state": {"auth_context": Ctx(), "client": "other"},
    }


async def call(mw_cls, path: str) -> tuple[bool, int | None]:
    reached = {"app": False}
    status = {"code": None}

    async def app(scope, receive, send):
        reached["app"] = True

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    await mw_cls(app)(scope_for(path), receive, send)
    return reached["app"], status["code"]


@pytest.mark.asyncio
async def test_client_authorization_lets_policy_fetch_through():
    reached, status = await call(ClientAuthorizationMiddleware, "/v1/policy")

    assert reached and status is None


@pytest.mark.asyncio
async def test_client_authorization_still_blocks_inference_for_same_client():
    reached, status = await call(ClientAuthorizationMiddleware, "/v1/messages")

    assert not reached and status == 403


@pytest.mark.asyncio
async def test_budget_does_not_apply_to_policy_fetch():
    reached, status = await call(BudgetMiddleware, "/v1/policy")

    assert reached and status is None


def test_downgrade_does_not_apply_to_policy_fetch():
    assert _path_eligible("/v1/policy") is False
