# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Anthropic Messages API route (/v1/messages) — Bedrock native proxy.

Claude Code sends POST /v1/messages with Anthropic Messages API format.
Bedrock invoke_model for Claude models accepts this body format directly.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import get_settings
from app.providers.bedrock_adapter import BedrockAdapter
from app.routers.bedrock import _rewrite_model_id_for_region, _strip_region_prefix
from app.schemas.domain import (
    DegradationLevel,
    ModelConfigSchema,
    ProviderType,
    TokenUsage,
)
from app.schemas.routing import RoutingProfileSchema
from app.services.body_log_records import (
    build_body_record_for_nonstream,
    build_body_record_for_stream,
    provider_name,
    resolve_body_logger,
)
from app.services.fallback_loop import (
    FallbackResult,
    enforce_candidate_admission,
    run_fallback_loop,
)
from app.services.fallback_resolver import make_same_provider
from app.services.router_service import RouterService
from app.services.streaming import bedrock_anthropic_sse_stream
from app.services.thinking_normalizer import (
    normalize_thinking,
    sanitize_bedrock_messages,
    sanitize_output_config,
)
from app.services.tool_filter import strip_unsupported_tools
from app.services.upstream_compat import (
    apply_forwarded_betas,
    client_betas,
    forward_beta_map,
    strip_unsupported_server_tools,
    unsupported_tool_prefixes,
)
from app.services.web_search_loop import normalize_inbound_native_blocks

logger = structlog.get_logger(__name__)

router = APIRouter()
_router_service = RouterService()


def _has_1h_cache_control(req_data: dict) -> bool:
    """Detect if any cache_control block in the request uses 1-hour TTL (ttl=3600).

    Scans system, messages, and tools for cache_control.ttl == "3600" or 3600.
    """
    def _check_blocks(blocks):
        if not isinstance(blocks, list):
            return False
        for block in blocks:
            if not isinstance(block, dict):
                continue
            cc = block.get("cache_control")
            if isinstance(cc, dict) and str(cc.get("ttl", "")) == "3600":
                return True
        return False

    # system prompt blocks
    system = req_data.get("system")
    if isinstance(system, list) and _check_blocks(system):
        return True

    # message content blocks
    for msg in req_data.get("messages", []):
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list) and _check_blocks(content):
            return True

    # tool definitions
    for tool in req_data.get("tools", []):
        if isinstance(tool, dict):
            cc = tool.get("cache_control")
            if isinstance(cc, dict) and str(cc.get("ttl", "")) == "3600":
                return True

    return False

# Bedrock invoke_model only accepts specific fields — strip everything else.
# Claude Code sends extra fields (model, stream, context_management, etc.) that Bedrock rejects.
# anthropic_beta 와 safeguards 는 일부러 이 목록에 넣지 않는다 — 정해 둔 beta 와 그 짝
# 필드만 _build_candidate_body 에서 apply_forwarded_betas 로 넣는다(그래야 끄기 스위치가
# 동작한다).
# English: anthropic_beta and safeguards are deliberately NOT in this list — only the
# configured betas and their paired fields are added, by apply_forwarded_betas in
# _build_candidate_body (that is what makes the kill switch work).
_BEDROCK_ALLOWED_FIELDS = {
    "anthropic_version",
    "messages",
    "max_tokens",
    "system",
    "stop_sequences",
    "temperature",
    "top_p",
    "top_k",
    "metadata",
    "tools",
    "tool_choice",
    "thinking",
    # ⚠️ adaptive 계열이 thinking 깊이를 제어하는 유일한 수단이다
    #    (`output_config.effort`). 여기 없으면 클라이언트가 보내도 우리가 버려서, 정규화를
    #    배선해도 깊이 제어가 동작하지 않는다.
    #
    # ⚠️ legacy 계열(haiku)은 이 필드를 받지 않는다. 그래서 이 필드를 허용하는 것은
    #    `normalize_thinking` 이 legacy 계열에서 이것을 **무조건 떨구는** 것과 한 쌍이다
    #    (thinking 형태와 무관하게). 그 두 변경 중 하나만 하면 haiku 가 400 을 내기 시작한다.
    "output_config",
}


@dataclass
class BackendDecision:
    provider: ProviderType
    profile: RoutingProfileSchema | None = None
    model_config: ModelConfigSchema | None = None  # pre-resolved for Mantle
    model_id: str | None = None          # resolved provider_model_id for Mantle; None = default Bedrock path
    endpoint: str | None = None


async def _select_backend(*, loader, router_service, redis, db, client, requested_alias):
    """Decide which backend serves this request.

    Rule A (Cowork client-override): a 'mantle' profile WITH a default_model forces
    that model (cowork → cowork-opus), ignoring the requested alias.
    Rule B (Claude Code alias opt-in): if the requested alias's provider is
    BEDROCK_MANTLE, route to Mantle using the requested alias. The profile (if any)
    supplies region/account_role_arn (NULL = in-account 374).
    Everyone else → the existing Bedrock path.
    """
    profile = await loader.load(redis, db, client)
    # loader.load() already filters disabled profiles → a non-None profile is always enabled here.

    # Rule A — cowork override (unchanged): mantle profile + default_model.
    if profile is not None and profile.backend == "mantle" and profile.default_model:
        cfg = await router_service.resolve_mantle_model(redis, db, profile.default_model)
        return BackendDecision(
            provider=ProviderType.BEDROCK_MANTLE,
            profile=profile,
            model_config=cfg,
            model_id=cfg.provider_model_id,
            endpoint=cfg.endpoint,
        )

    # Rule B — alias-triggered Mantle (Claude Code in-account option).
    # Rule B needs a profile for region/account (region is required, not synthesizable); no profile → fall through to Bedrock.
    if profile is not None and await router_service.alias_provider(redis, db, requested_alias) == ProviderType.BEDROCK_MANTLE:
        cfg = await router_service.resolve_mantle_model(redis, db, requested_alias)
        return BackendDecision(
            provider=ProviderType.BEDROCK_MANTLE,
            profile=profile,
            model_config=cfg,
            model_id=cfg.provider_model_id,
            endpoint=cfg.endpoint,
        )

    # Bedrock path: routing ignores the profile, but carry it through so the caller can
    # read per-client flags (e.g. web_search_enabled) uniformly across all backends.
    return BackendDecision(provider=ProviderType.BEDROCK, profile=profile)


@router.post("/v1/messages", response_model=None)
async def messages(request: Request) -> StreamingResponse | JSONResponse:
    state = request.scope.get("state", {})
    auth_context = state.get("auth_context")
    client = state.get("client")
    redis = state.get("_redis")
    session_factory = state.get("_session_factory")
    registry = request.app.state.provider_registry
    cost_recorder = request.app.state.cost_recorder
    request_id = state.get("request_id", "")
    start_time = state.get("request_start_time", time.monotonic())

    body = await request.body()

    try:
        req_data = json.loads(body)
        # Anthropic-only server tools (Claude Code's advisor …) are a Bedrock 400 for the whole
        # request. Removed HERE, before both consumers of req_data — the web-search loop and
        # the direct/fallback/Mantle bodies. Same object when there is nothing to remove.
        req_data, _ = strip_unsupported_server_tools(
            req_data,
            unsupported_tool_prefixes(get_settings().bedrock_unsupported_tool_type_prefixes))
        # Replayed native search blocks (server_tool_use / web_search_tool_result) are unknown
        # to Bedrock and would 400. The web-search loop receives the ORIGINAL req_data and
        # rewrites them as tool_use/tool_result; every path that bypasses the loop (fallback
        # loop, Mantle, profile toggle off, helper-model requests) uses the body normalised
        # here, where they become text trace lines. Without such blocks the same object is
        # returned, so other requests are byte-identical.
        req_for_bedrock = (normalize_inbound_native_blocks(req_data)
                           if isinstance(req_data, dict) else req_data)
        model_alias = req_data.get("model", "")
        is_stream = req_data.get("stream", False)

        # ⚠️ 정규화는 여기서 하지 않는다 — 모델 해석(아래 resolve_*) 전이라 alias
        #    밖에 모르는데, alias 추정이 실제 모델을 오판하면 필드를 잘못 지운다.
        #    이 본문은 body-logging 의 request_body 용이라 해석된 pmid 로 아래서
        #    다시 정규화한다(실제 wire body 는 _build_candidate_body 가 만든다).
        bedrock_body = {k: v for k, v in req_for_bedrock.items() if k in _BEDROCK_ALLOWED_FIELDS}
        bedrock_body["anthropic_version"] = "bedrock-2023-05-31"
        if auth_context and auth_context.sso_subject:
            bedrock_body["metadata"] = {"user_id": auth_context.sso_subject}
        cache_ttl_1h = _has_1h_cache_control(req_data)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request_error", "message": "Invalid JSON body"}},
        )

    # 클라이언트가 보낸 anthropic-beta 와 넘길 목록은 요청마다 한 번만 읽는다.
    # _build_candidate_body 는 폴백 후보·웹 검색 턴마다 불리므로 그 안에서 읽지 않는다.
    # English: read the client's anthropic-beta and the forward list once per request;
    # _build_candidate_body runs once per fallback candidate and per web-search turn.
    _client_betas = client_betas(request.headers.getlist("anthropic-beta"))
    _fwd_map = forward_beta_map(get_settings().bedrock_forward_betas)

    if not model_alias:
        return JSONResponse(
            status_code=400,
            content={
                "error": {"type": "invalid_request_error", "message": "model field is required"}
            },
        )

    # Resolve backend (client routing profile) → model config (short-lived session)
    dm = state.get("_degradation_manager")
    is_db_degraded = dm and dm.level in (
        DegradationLevel.DB_DEGRADED,
        DegradationLevel.BOTH_DEGRADED,
    )

    routing_loader = getattr(request.app.state, "routing_profile_loader", None)
    decision = BackendDecision(provider=ProviderType.BEDROCK)
    model_config = None
    try:
        if not is_db_degraded and session_factory is not None:
            async with session_factory() as db:
                if routing_loader is not None:
                    decision = await _select_backend(
                        loader=routing_loader, router_service=_router_service,
                        redis=redis, db=db, client=client, requested_alias=model_alias,
                    )
                if decision.provider != ProviderType.BEDROCK_MANTLE:
                    model_config = await _router_service.resolve_bedrock_model(
                        redis, db, model_alias
                    )
        else:
            if routing_loader is not None:
                decision = await _select_backend(
                    loader=routing_loader, router_service=_router_service,
                    redis=redis, db=None, client=client, requested_alias=model_alias,
                )
            if decision.provider != ProviderType.BEDROCK_MANTLE:
                model_config = await _router_service.resolve_bedrock_model(
                    redis, None, model_alias
                )
    except LookupError as e:
        return JSONResponse(
            status_code=404,
            content={"error": {"type": "not_found_error", "message": str(e)}},
        )

    # cross-account(claude-code→374) 일 때만 Bedrock 분기에서 profile.region 으로 설정.
    # 분기 전 명시 초기화 → 아래 _rewrite 클로저가 locals() 리플렉션 없이 직접 참조(리팩터 안전).
    _region_for_rewrite = None
    if decision.provider == ProviderType.BEDROCK_MANTLE:
        model_config = decision.model_config
        adapter = registry.get(ProviderType.BEDROCK_MANTLE)
        call_model_id = decision.model_id
        stream_kwargs: dict = {"profile": decision.profile, "endpoint": decision.endpoint}
        nonstream_kwargs: dict = {"profile": decision.profile, "endpoint": decision.endpoint}
        # Rebuild body as standard Anthropic Messages (NOT the bedrock-wrapped body):
        # anthropic-version is a HEADER for Mantle (adapter sets it); model goes in body.
        mantle_body = {k: v for k, v in req_for_bedrock.items() if k in _BEDROCK_ALLOWED_FIELDS}
        mantle_body.pop("anthropic_version", None)
        sanitize_output_config(mantle_body, call_model_id, alias=model_alias, request_id=request_id)
        sanitize_bedrock_messages(mantle_body, call_model_id, alias=model_alias, request_id=request_id)
        mantle_body["model"] = call_model_id
        # Carry user attribution metadata, same as the Bedrock path (line ~138).
        if auth_context and auth_context.sso_subject:
            mantle_body["metadata"] = {"user_id": auth_context.sso_subject}
        if is_stream:
            mantle_body["stream"] = True
        body = json.dumps(mantle_body).encode()
    else:
        # Bedrock native. cross-account(claude-code→374): backend=invoke AND account_role_arn
        # 이면 대상 계정 role 을 assume 한 bedrock-runtime 클라이언트로 호출. account_role_arn
        # NULL(codex/기본 in-account)이면 기존 in-account adapter 그대로(zero-regression).
        _prof = decision.profile
        _xacct = bool(_prof and _prof.backend == "invoke" and _prof.account_role_arn)
        if _xacct:
            _provider = getattr(request.app.state, "bedrock_account_client_provider", None)
            _inacct_adapter = registry.get(ProviderType.BEDROCK)
            if _provider is not None:
                _role, _reg, _ext = _prof.account_role_arn, _prof.region, _prof.external_id
                adapter = BedrockAdapter(
                    bedrock_client=None,
                    client_resolver=lambda: _provider.get_client(_role, _reg, _ext),
                    fallback_client=getattr(_inacct_adapter, "_client", None),  # assume 실패 시 859 폴백
                )
                _region_for_rewrite = _prof.region
            else:
                adapter = _inacct_adapter
                _region_for_rewrite = None
        else:
            adapter = registry.get(ProviderType.BEDROCK)
            _region_for_rewrite = None
        call_model_id = _rewrite_model_id_for_region(
            model_config.provider_model_id, region=_region_for_rewrite
        )
        # body-logging 의 request_body — 실제 wire body(_build_candidate_body)와
        # 같은 판정(해석된 pmid + 실제 전달될 beta)으로 정규화해야 로그가 보낸
        # 내용과 일치한다. 해석 전의 alias 추정으로 미리 지우면 잘못 삭제된 필드를
        # 되살릴 수 없으므로 여기서 한 번만 한다.
        sanitize_output_config(
            bedrock_body, model_config.provider_model_id,
            alias=model_alias, request_id=request_id,
        )
        sanitize_bedrock_messages(
            bedrock_body, model_config.provider_model_id,
            alias=model_alias, request_id=request_id,
            forwarded_betas=[b for b in _client_betas if b in _fwd_map
                             and (_fwd_map[b] is None or _fwd_map[b] in req_for_bedrock)],
        )
        body = json.dumps(
            normalize_thinking(
                bedrock_body, model_config.provider_model_id, request_id=request_id
            )
        ).encode()
        stream_kwargs = {"path_suffix": "invoke-with-response-stream"}
        nonstream_kwargs = {"path_suffix": "invoke"}

    # Invariant: every reachable path above either set model_config or returned 404.
    # Guard it explicitly so a future change can't silently pass None to check_key_scope
    # (which would bypass scope enforcement).
    assert model_config is not None
    state["model_config"] = model_config

    # --- Fallback chain resolution ---
    # For BEDROCK: resolve the chain and attempt fallback on 5xx.
    # For BEDROCK_MANTLE: resolver returns [original] only (no same-provider alternatives).
    cb = getattr(request.app.state, "circuit_breaker", None)
    fallback_resolver = getattr(request.app.state, "fallback_resolver", None)

    original_provider = model_config.provider

    # Use the startup alias→provider map (loaded from model.model_aliases at boot).
    # All known aliases are present so same-provider fallback candidates ARE admitted,
    # while cross-provider aliases (e.g. Bedrock aliases when original is BEDROCK_MANTLE)
    # are excluded because their map entry carries a different provider value.
    # Deny-by-default: aliases absent from the map return None → excluded.
    # We also ensure the original alias is always present (guard for the edge case where
    # it was somehow absent from the DB snapshot; the original is always same-provider).
    _startup_alias_provider_map: dict = getattr(request.app.state, "alias_provider_map", {})
    _alias_provider_map: dict = {**_startup_alias_provider_map, model_alias: original_provider}

    _same_provider_as_original = make_same_provider(_alias_provider_map, original_provider)

    allowed_set: set[str] | None = (
        set(auth_context.allowed_models) if auth_context and auth_context.allowed_models else None
    )

    if fallback_resolver is not None:
        try_order = fallback_resolver.resolve(
            original=model_alias,
            allowed=allowed_set,
            same_provider=_same_provider_as_original,
        )
    else:
        try_order = [model_alias]

    # --- Async model resolver for fallback candidates ---
    async def _resolve_candidate(alias: str) -> ModelConfigSchema:
        if decision.provider == ProviderType.BEDROCK_MANTLE:
            if not is_db_degraded and session_factory is not None:
                async with session_factory() as db:
                    return await _router_service.resolve_mantle_model(redis, db, alias)
            return await _router_service.resolve_mantle_model(redis, None, alias)
        else:
            if not is_db_degraded and session_factory is not None:
                async with session_factory() as db:
                    return await _router_service.resolve_bedrock_model(redis, db, alias)
            return await _router_service.resolve_bedrock_model(redis, None, alias)

    is_mantle = decision.provider == ProviderType.BEDROCK_MANTLE

    def _build_candidate_body(
        req_d: dict, cand_config: ModelConfigSchema, streaming: bool
    ) -> tuple[bytes, dict, dict]:
        """Build invoke body + kwargs for a given candidate model_config.

        ⚠️ 여기가 상류로 나가는 본문이 만들어지는 **단일 지점**이다 — 폴백 후보와 웹서치
           턴이 모두 이 함수를 지난다. 그래서 모델별 본문 정규화도 여기서 한다.
        """
        if is_mantle:
            # Mantle 에는 beta 를 넘기지 않는다(헤더로 넘겨야 하고 아직 시험하지 않았다).
            # safeguards 는 아래 필터에서 빠지므로 Claude Code 는 이전처럼 로컬 분류기를 쓴다.
            # English: no betas to Mantle (they would go as a header; untested). safeguards
            # is filtered out below, so Claude Code keeps its local classifier as before.
            mantle_b = {k: v for k, v in req_d.items() if k in _BEDROCK_ALLOWED_FIELDS}
            mantle_b.pop("anthropic_version", None)
            sanitize_output_config(mantle_b, cand_config.provider_model_id, request_id=request_id)
            sanitize_bedrock_messages(mantle_b, cand_config.provider_model_id, request_id=request_id)
            mantle_b["model"] = cand_config.provider_model_id
            if auth_context and auth_context.sso_subject:
                mantle_b["metadata"] = {"user_id": auth_context.sso_subject}
            if streaming:
                mantle_b["stream"] = True
            # ⚠️ Mantle 경로에서 Anthropic **네이티브** 도구 선언을 걷어낸다.
            #    Cowork 가 `web_search_20250305` / `code_execution_*` / `text_editor_*` /
            #    `computer_*` 를 선언하면 Mantle 은 "tool type is not supported for this
            #    model" 로 400 을 준다. 클라이언트가 고칠 수 없는 거부라 게이트웨이가
            #    걸러야 한다.
            #
            #    ⚠️ 우리가 주입하는 서버사이드 web_search 는 영향받지 않는다: 필터는
            #       `type` 필드로 판정하는데 주입된 도구에는 그 필드가 없다.
            mantle_b = strip_unsupported_tools(mantle_b, request_id=request_id)
            mantle_b = normalize_thinking(
                mantle_b, cand_config.provider_model_id, request_id=request_id
            )
            return (
                json.dumps(mantle_b).encode(),
                {"profile": decision.profile, "endpoint": decision.endpoint},
                {"profile": decision.profile, "endpoint": decision.endpoint},
            )
        else:
            bedrock_b = {k: v for k, v in req_d.items() if k in _BEDROCK_ALLOWED_FIELDS}
            bedrock_b["anthropic_version"] = "bedrock-2023-05-31"
            # ⚠️ Cowork 의 `output_config.format`(구조화 출력)은 Bedrock 이 거부한다
            #    — effort 만 남긴다(haiku 는 지원하므로 그대로).
            sanitize_output_config(bedrock_b, cand_config.provider_model_id, request_id=request_id)
            # 그 필드를 여는 beta 가 실제로 전달될 것만 넘긴다 — 전달되면 후보 모델이
            # 쓸 수 있으므로 정규화가 지우면 안 된다(apply_forwarded_betas 와 같은 판정).
            sanitize_bedrock_messages(
                bedrock_b, cand_config.provider_model_id, request_id=request_id,
                forwarded_betas=[b for b in _client_betas
                                 if b in _fwd_map
                                 and (_fwd_map[b] is None or _fwd_map[b] in req_d)])
            if auth_context and auth_context.sso_subject:
                bedrock_b["metadata"] = {"user_id": auth_context.sso_subject}
            # ⚠️ `thinking` 의 형태를 **후보 모델이 받는 형태로** 맞춘다.
            #
            #    두 계열이 서로를 거부한다: opus-4-7/4-8/opus-5/sonnet-5/fable-5/mythos-5 는
            #    `{"type":"adaptive"}` 만 받고, haiku-4-5 는 `{"type":"enabled"}` 만 받는다.
            #    정규화가 없으면 MAX_THINKING_TOKENS>0 로 설정된 Claude Code 의 **모든**
            #    요청이 opus-4-8 에서 400 이고, 400 은 폴백 대상이 아니라 그대로 사용자에게
            #    간다. 반대 방향도 마찬가지다.
            #
            #    후보 **단위**로 하는 것이 핵심이다: 가용성 폴백과 예산 강등이 계열을 넘어
            #    모델을 바꾸므로, 79% 예산에서 되던 요청이 80% 에서 400 이 되는 경로가
            #    실재한다. 모르는 모델은 그대로 통과시킨다(fail-open).
            bedrock_b = strip_unsupported_tools(bedrock_b, request_id=request_id)
            bedrock_b = normalize_thinking(
                bedrock_b, cand_config.provider_model_id, request_id=request_id
            )
            # 정해 둔 anthropic-beta 와 그 짝 필드(safeguards)를 본문에 넣는다. 다른 정규화가
            # 모두 끝난 뒤, 보내기 직전에 넣어 어느 단계도 이를 지우거나 바꾸지 못하게 한다.
            # 폴백 후보·예산 강등 후보·웹 검색 턴이 모두 이 함수를 지나므로 같이 적용된다.
            # English: add the configured anthropic-beta values and their paired fields
            # (safeguards) last, right before sending, so no normalisation step can drop
            # them; every fallback / downgrade candidate and web-search turn comes here.
            apply_forwarded_betas(bedrock_b, req_d, _client_betas, _fwd_map)
            return (
                json.dumps(bedrock_b).encode(),
                {"path_suffix": "invoke-with-response-stream"},
                {"path_suffix": "invoke"},
            )

    # cross-account(333) 면 profile.region, 아니면 None (분기 전 초기화됨).
    def _rewrite(pmid: str) -> str:
        if is_mantle:
            return pmid
        return _rewrite_model_id_for_region(pmid, region=_region_for_rewrite)

    # --- Server-side web search (Architecture C) — opt-in per routing profile ---
    # When the client's profile enables web search AND the AgentCore MCP client is
    # configured, run the tool-use loop instead of the plain fallback dispatch. The
    # loop injects a web_search tool, intercepts tool_use, calls AgentCore, and stitches
    # the answer. Skipped entirely otherwise → the existing path below is byte-identical.
    mcp_client = getattr(request.app.state, "agentcore_mcp_client", None)
    _profile = decision.profile
    if (
        mcp_client is not None
        and _profile is not None
        and getattr(_profile, "web_search_enabled", False)
    ):
        from app.services.web_search_loop import run_web_search_loop

        # ⚠️ **입장 심사를 여기서 해야 한다.** 이 분기는 아래 `run_fallback_loop` 보다
        #    먼저 리턴하고, 그 폴백 루프가 `/v1/messages` 에서 스코프 2축과 레이트리밋을
        #    거는 **유일한** 지점이다. 그래서 이 분기는 오랫동안 다음을 전부 건너뛰었다:
        #      - check_key_scope            (사용자 × 모델 허용목록)
        #      - check_client_model_scope   (앱 × 모델 허용목록, migration 0035)
        #      - enforce_rate_limits        (RPM / TPM / 비용 한도)
        #    웹서치를 켠 프로파일에서만 그랬으므로 증상이 없었다 — 접근권 없는 모델도
        #    호출되고, 한도를 넘겨도 429 가 나지 않았다.
        #
        #    같은 함수를 폴백 루프도 쓴다(단일 구현). 여기서 거절되면 상류를 호출하지
        #    않으므로 예약도 남지 않는다.
        _admission = await enforce_candidate_admission(
            router_service=_router_service,
            auth_context=auth_context,
            candidate_config=model_config,
            redis=redis,
            req_data=req_data,
            state=state,
            request_id=request_id,
            budget_status=state.get("budget_status"),
        )
        if _admission is not None:
            return Response(
                content=_admission.body,
                status_code=_admission.status,
                headers=_admission.headers or None,
                media_type="application/json",
            )

        _settings_ws = get_settings()

        async def _ws_invoke(turn_body: dict) -> tuple[int, bytes, dict, TokenUsage]:
            body_b, _sk, nsk = _build_candidate_body(turn_body, model_config, False)
            return await adapter.invoke(body_b, _rewrite(model_config.provider_model_id), **nsk)

        async def _ws_invoke_stream(turn_body: dict):
            body_b, sk, _nsk = _build_candidate_body(turn_body, model_config, True)
            return await adapter.invoke_stream(
                body_b, _rewrite(model_config.provider_model_id), **sk
            )

        # 클라이언트 체감 TTFT — 루프의 on_usage 는 1-arg(멀티턴 합산이라 per-turn
        # TTFT 를 안 넘긴다 — web_search_loop.py 의 _stream_on_usage 주석)라 ttft_ms 가
        # 항상 NULL 로 기록돼 모니터링에 "ttft=-" 로 나왔다. 응답 이터레이터의 첫
        # 청크 시각을 잡아 클라이언트가 첫 바이트를 받은 시점으로 기록한다.
        _ws_first_frame: dict[str, float | None] = {"t": None}

        async def _ws_record(usage: TokenUsage) -> None:
            if not auth_context:
                return
            usage.cache_ttl_1h = cache_ttl_1h
            duration_ms = int((time.monotonic() - start_time) * 1000)
            ft = _ws_first_frame["t"]
            ttft_ms = int((ft - start_time) * 1000) if ft is not None else duration_ms
            await cost_recorder.finalize(
                redis, auth_context, model_config, usage, request_id,
                is_stream, duration_ms,
                ttft_ms=ttft_ms,
                rate_limit_state=state.get("rate_limit_state"),
                downgraded_from=state.get("downgraded_from"),
                client=client,
            )

        # Body logging — hooks for the web-search path only. This branch returns BEFORE the
        # body-logging wiring below, so without these hooks requests of a web-search-enabled
        # profile would never reach the logging code and go silently unrecorded.
        #
        # bedrock_request_id is None: the loop makes a separate Bedrock call per turn, so no
        # single request id can serve as the join key (same decision as `_ws_record`).
        async def _ws_log_stream(sse_text: str, log_status: str) -> None:
            bl = getattr(request.app.state, "body_logger", None)
            if bl is None:
                return
            await bl.enqueue(
                build_body_record_for_stream(
                    request_id=request_id,
                    provider=provider_name(model_config),
                    client=client,
                    model_alias=model_config.alias or model_config.provider_model_id,
                    status=log_status,
                    request_body=body,
                    sse_text=sse_text,
                    user_id=auth_context.user_id if auth_context else None,
                    team_id=auth_context.team_id if auth_context else None,
                    sso_subject=auth_context.sso_subject if auth_context else None,
                    bedrock_request_id=None,
                )
            )

        async def _ws_log_nonstream(resp_status: int, resp_body: bytes) -> None:
            bl = getattr(request.app.state, "body_logger", None)
            if bl is None:
                return
            await bl.enqueue(
                build_body_record_for_nonstream(
                    request_id=request_id,
                    provider=provider_name(model_config),
                    client=client,
                    model_alias=model_config.alias or model_config.provider_model_id,
                    status_code=resp_status,
                    request_body=body,
                    response_body=resp_body,
                    is_streaming=False,
                    user_id=auth_context.user_id if auth_context else None,
                    team_id=auth_context.team_id if auth_context else None,
                    sso_subject=auth_context.sso_subject if auth_context else None,
                    bedrock_request_id=None,
                )
            )

        # KI-08 token back-estimation hook — web-search path ONLY.
        #
        # The `_estimate` defined further down cannot be reused: it depends on
        # `call_model_id_final` / `is_mantle`, both computed AFTER the fallback loop, so
        # referencing it here is an UnboundLocalError (caught at runtime — an AST check for
        # "defined inside the function" does not catch it). This path never runs the fallback
        # loop, so model_config is the actual model.
        _ws_tokenizer = getattr(request.app.state, "tokenizer", None)

        async def _ws_estimate(text: str) -> int | None:
            if not _ws_tokenizer:
                return None
            return await _ws_tokenizer.estimate_output_tokens(
                text,
                provider=model_config.provider,
                model_id=model_config.provider_model_id,
            )

        # 사전 게이팅 — 훅을 넘기면 루프가 SSE 전문을 누적한다.
        _ws_logging = await resolve_body_logger(request.app.state, redis, session_factory)

        _ws_resp = await run_web_search_loop(
            dialect="anthropic",
            invoke=_ws_invoke,
            invoke_stream=_ws_invoke_stream,
            initial_req_data=req_data if isinstance(req_data, dict) else {},
            is_stream=is_stream,
            mcp_client=mcp_client,
            request=request,
            on_usage=_ws_record,
            max_iterations=_settings_ws.web_search_max_iterations,
            total_deadline_sec=_settings_ws.web_search_total_deadline_sec,
            default_max_results=_settings_ws.web_search_max_results_default,
            max_result_chars=_settings_ws.web_search_max_result_chars,
            max_searches_per_turn=_settings_ws.web_search_max_searches_per_turn,
            cache_results=_settings_ws.web_search_cache_results,
            result_text_chars=_settings_ws.web_search_result_text_chars,
            handshake_timeout=_settings_ws.agentcore_handshake_timeout,
            tokenizer_hook=_ws_estimate,
            on_stream_complete=_ws_log_stream if _ws_logging else None,
            on_nonstream_complete=_ws_log_nonstream if _ws_logging else None,
        )

        # 스트리밍이면 첫 yield 시각을 잡아 _ws_record 가 ttft_ms 로 쓰게 한다.
        # finalize 는 스트림 소비 중(usage 확정 시점)에 호출되므로 그때는 첫 청크
        # 시각이 이미 잡혀 있다. 비스트리밍(JSONResponse)은 래핑 대상이 없어
        # _ws_record 의 duration_ms 폴백이 적용된다(다른 라우터의 비스트림과 동일).
        if isinstance(_ws_resp, StreamingResponse):
            _ws_body_iter = _ws_resp.body_iterator

            async def _ws_timed_iter():
                # aclosing 없이 async for 하면 클라이언트 끊김 시 내부 generator 가
                # GC 에 맡겨져, 과금 finalize 가 들어 있는 finally 가 늦게/안 도는
                # 경우가 있다 — 명시적으로 닫아 과금 누락을 막는다.
                async with contextlib.aclosing(_ws_body_iter) as it:
                    async for chunk in it:
                        if _ws_first_frame["t"] is None:
                            _ws_first_frame["t"] = time.monotonic()
                        yield chunk

            _ws_resp.body_iterator = _ws_timed_iter()
        return _ws_resp

    # Use a no-op CB when the service is not wired (e.g. tests that don't configure it)
    if cb is None:
        from app.services.circuit_breaker import CircuitBreakerService

        cb = CircuitBreakerService()

    result: FallbackResult = await run_fallback_loop(
        try_order=try_order,
        original_alias=model_alias,
        is_stream=is_stream,
        # The fallback loop goes out without our web_search tool, so it gets the body whose
        # replayed native search blocks were turned into text (req_for_bedrock); the original
        # req_data is for the web-search loop only.
        req_data=req_for_bedrock if isinstance(req_for_bedrock, dict) else {},
        redis=redis,
        auth_context=auth_context,
        state=state,
        request_id=request_id,
        budget_status=state.get("budget_status"),
        adapter=adapter,
        stream_kwargs=stream_kwargs,
        nonstream_kwargs=nonstream_kwargs,
        cb=cb,
        router_service=_router_service,
        session_factory=session_factory,
        is_db_degraded=is_db_degraded,
        original_model_config=model_config,
        resolve_model_config=_resolve_candidate,
        build_candidate_body=_build_candidate_body,
        rewrite_model_id=_rewrite,
        metrics=getattr(request.app.state, "metrics", None),
    )

    # --- Handle the result ---
    status = result.status
    effective_model_config = result.model_config
    availability_fallback_from = result.availability_fallback_from
    tokenizer = getattr(request.app.state, "tokenizer", None)

    # Synthetic all-open 503
    if result.all_open:
        _settings = get_settings()
        return JSONResponse(
            status_code=503,
            content=json.loads(result.payload[0]),
            headers={"Retry-After": str(_settings.cb_open_sec)},
        )

    if is_stream:
        chunk_iter, headers, aws_request_id = result.payload

        # When every fallback candidate failed (fallback_loop exhausted all
        # streaming candidates), the payload[0] is raw JSON error *bytes*, not
        # an async chunk iterator. Streaming that through the SSE stream would
        # raise (bytes has no __anext__). Return it as a JSON error response,
        # mirroring the all_open 503 branch above.
        if isinstance(chunk_iter, (bytes, bytearray)):
            try:
                err_content = json.loads(chunk_iter)
            except (json.JSONDecodeError, ValueError):
                err_content = {
                    "error": {
                        "type": "service_unavailable",
                        "message": "All fallback models failed",
                    }
                }
            return JSONResponse(status_code=status, content=err_content)

        # If the fallback loop returned a non-200 status for streaming, the
        # chunk_iter is an error generator from the adapter — just stream it.
        call_model_id_final = _rewrite(effective_model_config.provider_model_id)
        rate_limit_state = state.get("rate_limit_state")

        async def _estimate(text: str) -> int | None:
            if not tokenizer or is_mantle:
                return None
            return await tokenizer.estimate_output_tokens(
                text,
                provider=ProviderType.BEDROCK,
                model_id=call_model_id_final,
            )

        async def _record(usage: TokenUsage, first_token_time: float | None) -> None:
            if not auth_context:
                return
            usage.cache_ttl_1h = cache_ttl_1h
            duration_ms = int((time.monotonic() - start_time) * 1000)
            if first_token_time is not None:
                ttft_ms = int((first_token_time - start_time) * 1000)
            else:
                ttft_ms = duration_ms  # fallback: 첫 콘텐츠 델타 미검출
            await cost_recorder.finalize(
                redis,
                auth_context,
                effective_model_config,
                usage,
                request_id,
                True,
                duration_ms,
                ttft_ms=ttft_ms,
                rate_limit_state=rate_limit_state,
                downgraded_from=state.get("downgraded_from"),
                availability_fallback_from=availability_fallback_from,
                bedrock_request_id=aws_request_id,
                client=client,
            )

        response_headers: dict = {}
        if availability_fallback_from:
            response_headers["x-llm-gateway-fallback-from"] = availability_fallback_from

        async def _log_body_stream(sse_text: str, log_status: str) -> None:
            """스트림 종료 시 본문을 큐에 넣는다. 절대 블로킹하지 않는다(enqueue 만)."""
            bl = getattr(request.app.state, "body_logger", None)
            if bl is None:
                return
            await bl.enqueue(
                build_body_record_for_stream(
                    request_id=request_id,
                    provider=provider_name(effective_model_config),
                    client=client,
                    model_alias=(
                        effective_model_config.alias
                        or effective_model_config.provider_model_id
                    ),
                    status=log_status,
                    request_body=body,
                    sse_text=sse_text,
                    user_id=auth_context.user_id if auth_context else None,
                    team_id=auth_context.team_id if auth_context else None,
                    sso_subject=auth_context.sso_subject if auth_context else None,
                )
            )

        # ⚠️ **사전** 게이팅이다. on_complete 를 넘기면 제너레이터가 SSE 프레임 전문을
        #    메모리에 누적하므로(services/streaming.py OnComplete 주석), 로깅이 꺼져
        #    있을 때 그 비용을 내지 않으려면 스트림이 시작되기 **전에** 판정해야 한다.
        #    끝나고 물어보면 모든 요청에 대해 응답 본문을 버릴 목적으로 버퍼링하게 된다.
        _on_complete = (
            _log_body_stream
            if await resolve_body_logger(request.app.state, redis, session_factory)
            else None
        )

        return StreamingResponse(
            bedrock_anthropic_sse_stream(
                request,
                chunk_iter,
                on_usage=_record,
                tokenizer_hook=_estimate,
                on_complete=_on_complete,
            ),
            status_code=status,
            media_type="text/event-stream",
            headers=response_headers,
        )
    else:
        response_body, headers, usage = result.payload
        rate_limit_state = state.get("rate_limit_state")

        if auth_context and isinstance(usage, TokenUsage) and (usage.input_tokens + usage.output_tokens) > 0:
            usage.cache_ttl_1h = cache_ttl_1h
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await cost_recorder.finalize(
                redis,
                auth_context,
                effective_model_config,
                usage,
                request_id,
                False,
                duration_ms,
                ttft_ms=duration_ms,
                rate_limit_state=rate_limit_state,
                downgraded_from=state.get("downgraded_from"),
                availability_fallback_from=availability_fallback_from,
                # Join key to the Bedrock model-invocation log record. The streaming
                # branch above has always recorded it; this one did not, so every
                # non-streaming Claude call landed with a NULL id and was unauditable
                # even though AWS had logged it. `headers` comes from adapter.invoke via
                # FallbackResult.payload and is never sent to the client.
                bedrock_request_id=(headers or {}).get("x-amzn-requestid"),
                client=client,
            )

        # 본문 로깅(성공 **및** 오류). enqueue 만 하므로 응답을 지연시키지 않는다.
        # effective_model_config 를 쓴다 — 폴백이 일어났으면 실제로 응답한 모델이 남아야 한다.
        body_logger = await resolve_body_logger(request.app.state, redis, session_factory)
        if body_logger is not None:
            await body_logger.enqueue(
                build_body_record_for_nonstream(
                    request_id=request_id,
                    provider=provider_name(effective_model_config),
                    client=client,
                    model_alias=(
                        effective_model_config.alias
                        or effective_model_config.provider_model_id
                    ),
                    status_code=status,
                    request_body=body,
                    response_body=response_body,
                    is_streaming=False,
                    user_id=auth_context.user_id if auth_context else None,
                    team_id=auth_context.team_id if auth_context else None,
                    sso_subject=auth_context.sso_subject if auth_context else None,
                    bedrock_request_id=(headers or {}).get("x-amzn-requestid"),
                )
            )

        response_headers_out: dict = {}
        if availability_fallback_from:
            response_headers_out["x-llm-gateway-fallback-from"] = availability_fallback_from

        # For the synthetic 429 / 403 paths returned via FallbackResult.payload,
        # response_body is already JSON bytes.
        try:
            content = json.loads(response_body)
        except Exception:
            content = {"error": {"type": "api_error", "message": "Invalid response from provider"}}
        return JSONResponse(
            status_code=status,
            content=content,
            headers=response_headers_out if response_headers_out else None,
        )


@router.post("/v1/messages/count_tokens", response_model=None)
async def count_tokens(request: Request) -> JSONResponse:
    """Anthropic count_tokens — proxies to Bedrock CountTokens (no inference, no cost)."""
    # count_tokens 는 Bedrock native 경로 유지(Mantle/Cowork 는 Phase 3에서 VK scope 후 라우팅).
    # claude-code→374 cross-account: CountTokens 도 invoke 와 동일 계정(333)에서 실행되도록
    # 라우팅 프로파일을 읽어 _xacct 면 cross-account adapter 사용(assume 실패 시 859 투명 폴백).
    # → invoke=374 / count_tokens=859 로 갈리는 무음 계정 스플릿 방지(어드버서리 리뷰 #4).
    state = request.scope.get("state", {})
    auth_context = state.get("auth_context")
    client = state.get("client")
    redis = state.get("_redis")
    session_factory = state.get("_session_factory")
    registry = request.app.state.provider_registry
    routing_loader = getattr(request.app.state, "routing_profile_loader", None)

    body = await request.body()

    try:
        req_data = json.loads(body)
        # Same bridge as /v1/messages: CountTokens rejects Anthropic-only server tools too.
        req_data, _ = strip_unsupported_server_tools(
            req_data,
            unsupported_tool_prefixes(get_settings().bedrock_unsupported_tool_type_prefixes))
        # 되돌아온 native 검색 블록은 CountTokens 도 거부한다 — /v1/messages 와 같은 환원.
        if isinstance(req_data, dict):
            req_data = normalize_inbound_native_blocks(req_data)
        model_alias = req_data.get("model", "")
        bedrock_body = {k: v for k, v in req_data.items() if k in _BEDROCK_ALLOWED_FIELDS}
        bedrock_body["anthropic_version"] = "bedrock-2023-05-31"
        # 정규화는 모델 해석(resolve_bedrock_model) 뒤에 한다 — alias 추정이 실제
        # 모델을 오판하면 필드가 잘못 지워져 카운트가 틀어진다(팀-haiku alias +
        # opus 대상 → effort 유실, sonnet-4-6 alias + 5-5 대상 → 신형 필드 유실).
        # Bedrock CountTokens requires max_tokens in the wrapped Anthropic body
        # even though it doesn't generate output; inject a placeholder when absent.
        bedrock_body.setdefault("max_tokens", 1)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request_error", "message": "Invalid JSON body"}},
        )

    if not model_alias:
        return JSONResponse(
            status_code=400,
            content={
                "error": {"type": "invalid_request_error", "message": "model field is required"}
            },
        )

    dm = state.get("_degradation_manager")
    is_db_degraded = dm and dm.level in (
        DegradationLevel.DB_DEGRADED,
        DegradationLevel.BOTH_DEGRADED,
    )

    _ct_profile = None
    try:
        if not is_db_degraded and session_factory is not None:
            async with session_factory() as db:
                model_config = await _router_service.resolve_bedrock_model(redis, db, model_alias)
                if routing_loader is not None:
                    _ct_profile = await routing_loader.load(redis, db, client)
        else:
            model_config = await _router_service.resolve_bedrock_model(redis, None, model_alias)
            if routing_loader is not None:
                _ct_profile = await routing_loader.load(redis, None, client)
    except LookupError as e:
        return JSONResponse(
            status_code=404,
            content={"error": {"type": "not_found_error", "message": str(e)}},
        )

    if auth_context:
        try:
            _router_service.check_key_scope(auth_context, model_config)
            # 모델 × 앱 축(migration 0035). 위 게이트(사용자 × 모델)와 AND 로 걸린다.
            # allowed_clients: None=제한 없음 / []=어떤 앱도 불가 / 목록=그 앱만.
            _router_service.check_client_model_scope(model_config, state.get("client"))
        except PermissionError:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "type": "invalid_request_error",
                        "message": f"Your account does not have access to model '{model_config.alias}'. Contact your administrator to request access.",
                    }
                },
            )

    # CountTokens rejects cross-region inference profile IDs (global./us./apac./eu.)
    # — use the base foundation-model ID instead.
    count_tokens_model_id = _strip_region_prefix(model_config.provider_model_id)

    # 해석된 실제 모델로 정규화한다 — invoke 의 _build_candidate_body 와 같은 규약.
    # forwarded_betas 는 비워 둔다: CountTokens 요청엔 beta 필드를 주입하지 않으므로
    # (apply_forwarded_betas 를 타지 않는 경로) 필드가 "열리지" 않아, beta 로 열리는
    # 필드는 지우는 게 상류 400 을 막는 방향과 일치한다.
    sanitize_output_config(
        bedrock_body, model_config.provider_model_id, alias=model_alias
    )
    sanitize_bedrock_messages(
        bedrock_body, model_config.provider_model_id, alias=model_alias
    )
    body = json.dumps(
        normalize_thinking(bedrock_body, model_config.provider_model_id)
    ).encode()

    # cross-account(claude-code→374): invoke 와 동일 계정에서 CountTokens 실행 → 계정 스플릿 방지.
    # account_role_arn NULL(codex/기본)이면 in-account adapter 그대로(무회귀). assume 실패 시 859 폴백.
    _ct_xacct = bool(
        _ct_profile and _ct_profile.backend == "invoke" and _ct_profile.account_role_arn
    )
    if _ct_xacct:
        _provider = getattr(request.app.state, "bedrock_account_client_provider", None)
        _inacct_adapter = registry.get(ProviderType.BEDROCK)
        if _provider is not None:
            _role, _reg, _ext = (
                _ct_profile.account_role_arn,
                _ct_profile.region,
                _ct_profile.external_id,
            )
            adapter = BedrockAdapter(
                bedrock_client=None,
                client_resolver=lambda: _provider.get_client(_role, _reg, _ext),
                fallback_client=getattr(_inacct_adapter, "_client", None),
            )
        else:
            adapter = _inacct_adapter
    else:
        adapter = registry.get(ProviderType.BEDROCK)
    status, input_tokens = await adapter.count_tokens(body, count_tokens_model_id)

    if status != 200:
        return JSONResponse(
            status_code=status,
            content={"error": {"type": "provider_error", "message": "count_tokens failed"}},
        )

    return JSONResponse(status_code=200, content={"input_tokens": input_tokens})
