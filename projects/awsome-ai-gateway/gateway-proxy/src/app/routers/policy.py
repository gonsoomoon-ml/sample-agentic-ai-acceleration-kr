"""GET /v1/policy — Claude Code policy for policy-helper.

policy-helper (run by Claude Code at launch and every refreshIntervalMs) calls this
with the user's VK and lays `managedSettings` over its built-in baseline. The route
is exempt from client authorization (the helper's User-Agent is not Claude Code's)
and sits outside the budget / rate-limit / downgrade paths — it is not model usage.

Any non-200 answer makes the helper keep its cached policy, so failures here never
stop Claude Code from starting.
"""
from __future__ import annotations

import hashlib
import json

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.policy_service import load_models_inputs, models_fragment

logger = structlog.get_logger(__name__)
router = APIRouter()

CLIENT = "claude-code"


def _version(fragment: dict) -> str:
    canonical = json.dumps(fragment, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@router.get("/v1/policy")
async def get_policy(request: Request) -> JSONResponse:
    state = request.scope.get("state", {})
    auth_context = state.get("auth_context")
    if auth_context is None:
        return JSONResponse(status_code=401, content={
            "type": "error", "error": {"type": "authentication_error", "message": "VK required"},
        })
    session_factory = state.get("_session_factory")
    try:
        async with session_factory() as db:
            inputs = await load_models_inputs(db, auth_context.user_id, CLIENT)
    except Exception:
        logger.warning("policy_lookup_failed", user_id=auth_context.user_id, exc_info=True)
        return JSONResponse(status_code=503, content={
            "type": "error", "error": {"type": "api_error", "message": "policy unavailable"},
        })
    fragment = models_fragment(**inputs) if inputs is not None else {}
    return JSONResponse(content={"version": _version(fragment), "managedSettings": fragment})
