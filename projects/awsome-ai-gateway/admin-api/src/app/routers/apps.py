# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_admin
from app.core.clients import VALID_CLIENTS as _VALID_CLIENTS
from app.core.db import get_db_session
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import User, UserAllowedClient
from app.models.model import ModelAlias, ModelStatus, Provider

logger = structlog.get_logger()

router = APIRouter(prefix="/admin/apps", tags=["App Policy"])

#: ``POST /v1/responses`` 에서 도달 가능한 provider 집합 — gateway-proxy
#: ``services/router_service.py::_RESPONSES_PROVIDERS`` 의 미러. 이 두 provider 만
#: ``resolve_responses_model`` 이 받아준다.
_RESPONSES_PROVIDERS: frozenset[str] = frozenset(
    {Provider.BEDROCK_MANTLE_OPENAI.value, Provider.BEDROCK_RUNTIME_OPENAI.value}
)


# ── Response / Request schemas ──


class AppUserRef(BaseModel):
    user_id: str
    email: str | None = None
    #: True = user_allowed_clients 에 이 client 행이 명시됨.
    #: False = 명시 행이 없는 사용자 — fail-open 의미로 "허용"에 포함(effective policy 와 동일).
    explicit: bool = True


class AppModelRef(BaseModel):
    alias: str
    # None = 제한 없음(전체 앱 허용, 나중에 추가되는 앱까지)
    # []   = 명시적 공집합 = 허용 앱 없음(전면 거부)
    # 비어있지 않음 = 그 목록만. gateway-proxy check_client_model_scope 와 동일 의미.
    allowed_clients: list[str] | None


class AppPolicyResponse(BaseModel):
    client: str
    allowed_models: list[str]
    all_models: list[AppModelRef]
    default_model: str | None
    allowed_users: list[AppUserRef]
    # 같은 routing_profiles 행의 앱 정책 — 한 화면에서 같이 보고 바꾼다.
    web_search_enabled: bool = False


class DefaultModelPatchRequest(BaseModel):
    default_model: str


class ModelToggleRequest(BaseModel):
    alias: str
    allowed: bool


# ── Pure helper ──


def _next_allowed_clients(current: list[str] | None, client: str, allow: bool) -> list[str] | None:
    """Compute the next allowed_clients value after toggling `client` to `allow`.

    None means all-apps-allowed. Removing a client from None materializes to the
    explicit full client set minus `client` (other apps keep access; model is no
    longer 'all-apps-allowed').

    마지막 앱까지 해제하면 `[]` 를 돌려준다 — 그리고 그건 **의도된 값**이다:
    canonical 의미로 `[]` = "허용 앱 없음"(전면 거부)이고, NULL 만이 "제한 없음" 이다.
    예전에는 gateway-proxy 의 `check_client_model_scope` 가 `[]` 를 falsy 로 보고
    allow-all 로 읽어서, 운영자의 "전부 해제" 가 "전부 허용" 이 됐다(보안 결함).
    지금은 enforcement/조회 SQL/콘솔 표시가 모두 같은 의미를 쓴다.
    """
    if allow:
        if current is None:
            return None  # already all-allowed — no-op
        return current if client in current else [*current, client]
    # allow=False: remove client
    base = sorted(_VALID_CLIENTS) if current is None else list(current)
    return [c for c in base if c != client]


def _enum_value(v) -> str:
    """ORM Enum 컬럼은 Enum 인스턴스를, raw SQL 은 str 을 준다. 문자열로 통일."""
    return getattr(v, "value", v)


def _route_family(provider: str) -> str:
    """`routing_profiles.default_model` 을 푸는 resolver 기준의 "경로 계열".

    계열이 다른 alias 를 default 로 박으면 그 client 의 요청이 **런타임에** 깨진다.
    각 경로가 provider 를 고정해서 alias 를 풀기 때문이다(budget_service 의
    cross-provider 다운그레이드 금지와 같은 이유, 같은 판정 기준):

        /v1/messages  → resolve_bedrock_model(BEDROCK)
        /v1/messages  → resolve_mantle_model(BEDROCK_MANTLE)   cowork Rule A
        /v1/responses → resolve_responses_model(...)           codex

    responses 계열만 두 provider(mantle bearer / bedrock-runtime SigV4)를 한 계열로
    묶는다 — `resolve_responses_model` 이 둘 다 받아주기 때문(INV-2). 그래서 codex 의
    default 를 mantle↔runtime 트랙 사이에서 옮기는 것은 지원되는 운영 액션이고(런타임
    트랙 전환이 바로 그 방식이다), responses 계열 **밖으로** 옮기는 것만 거부된다.
    """
    return "openai-responses" if provider in _RESPONSES_PROVIDERS else provider


def _default_model_rejection(
    *,
    client: str,
    alias: str,
    status: str,
    allowed_clients: list[str] | None,
    provider: str,
    current_default_alias: str | None,
    current_default_provider: str | None,
) -> str | None:
    """이 alias 가 이 client 의 `default_model` 이 될 수 **없는** 이유, 없으면 None.

    "alias 행이 존재한다" 는 것만으로는 부족하다. default_model 은 요청이 모델을
    지정하지 않았거나(cowork Rule A) 지정한 이름이 우리 alias 가 아닐 때(codex 는 항상
    upstream 이름을 보내므로 **거의 모든 요청**) 실제로 쓰이는 값이라, 여기서 통과시킨
    잘못된 값은 콘솔에선 200 OK 로 저장되고 그 앱의 모든 요청을 런타임에 실패시킨다.
    저장 시점이 유일하게 안전한 차단 지점이다(런타임에는 이미 늦다).

    순수 함수로 분리한 이유: 세 판정이 각각 다른 런타임 실패 모드에 대응하므로
    (404 / 400-app-scope / resolve 실패) 단위테스트로 하나씩 못박을 수 있어야 한다.
    """
    # 1) INACTIVE = 운영자 kill switch. resolver 는 ModelInactiveError 를 던지고
    #    호출부는 이 경우 **폴백하지 않는다**(openai_compat.py 가 명시적으로 re-raise).
    if _enum_value(status) != ModelStatus.ACTIVE.value:
        return (
            f"Model '{alias}' is INACTIVE and cannot be the default model for "
            f"'{client}': every request that falls back to the default would be refused "
            f"(status=INACTIVE is a kill switch and is deliberately not fallen back on). "
            f"Activate the model first."
        )

    # 2) 앱별 허용목록(model_aliases.allowed_clients) 게이트. NULL = 제한 없음,
    #    [] = 허용 앱 없음, 그 외 = 목록. 여기서 막히면 런타임에 매 요청 400/403.
    if allowed_clients is not None and client not in allowed_clients:
        where = (
            "no app is allowed to use it (allowed_clients is an explicitly empty list)"
            if not allowed_clients
            else f"it is restricted to {sorted(allowed_clients)}"
        )
        return (
            f"Model '{alias}' is not available to app '{client}': {where}. "
            f"Allow '{client}' for this model in the app policy first, then set it as "
            f"the default."
        )

    # 3) 경로 계열. 현재 default 가 없거나(dangling 포함) 같은 alias 면 비교할 기준이
    #    없으므로 통과시킨다 — 데이터로 반증할 수 없는 것을 막지는 않는다.
    if current_default_provider is None:
        return None
    if _route_family(_enum_value(provider)) != _route_family(_enum_value(current_default_provider)):
        return (
            f"Model '{alias}' ({_enum_value(provider)}) is not reachable on the route that "
            f"serves '{client}': that client's default model is currently "
            f"'{current_default_alias}' ({_enum_value(current_default_provider)}), and the "
            f"two are resolved by different resolvers — the alias would not resolve at "
            f"request time. Pick a model on the same route "
            f"(the two OpenAI Responses providers are interchangeable with each other)."
        )
    return None


# ── Shared query helper ──


async def _build_app_policy(session: AsyncSession, client: str) -> AppPolicyResponse:
    """Query and assemble AppPolicyResponse for `client`.

    Caller is responsible for validating that `client` is in _VALID_CLIENTS.
    """
    # allowed_models: ACTIVE aliases where allowed_clients IS NULL OR client = ANY(allowed_clients)
    # Use raw SQL for the ARRAY ANY operator to avoid needing the pg-dialect ARRAY type at import.
    result = await session.execute(
        text(
            "SELECT alias FROM model.model_aliases"
            " WHERE status = 'ACTIVE'"
            " AND (allowed_clients IS NULL OR :client = ANY(allowed_clients))"
            " ORDER BY alias"
        ),
        {"client": client},
    )
    allowed_models = [row[0] for row in result.fetchall()]

    # all_models: every ACTIVE alias with its allowed_clients restriction
    all_result = await session.execute(
        text(
            "SELECT alias, allowed_clients FROM model.model_aliases"
            " WHERE status = 'ACTIVE' ORDER BY alias"
        )
    )
    all_models = [
        AppModelRef(alias=row[0], allowed_clients=row[1]) for row in all_result.fetchall()
    ]

    # default_model + web_search_enabled: model.routing_profiles WHERE client = :client
    rp_result = await session.execute(
        text(
            "SELECT default_model, web_search_enabled"
            " FROM model.routing_profiles WHERE client = :client"
        ),
        {"client": client},
    )
    rp_row = rp_result.fetchone()
    default_model: str | None = rp_row[0] if rp_row else None
    web_search_enabled = bool(rp_row[1]) if rp_row else False

    # allowed_users: effective policy 의미(fail-open)에 맞춘다.
    #   명시 행이 없는 사용자 = 전체 앱 허용 → 이 client 도 허용에 포함해야 한다.
    #   행이 있는 사용자 = 이 client 행이 있을 때만 허용.
    explicit_row = exists().where(
        and_(
            UserAllowedClient.user_id == User.id,
            UserAllowedClient.client == client,
        )
    )
    no_restriction = ~exists().where(UserAllowedClient.user_id == User.id)
    uac_stmt = (
        select(User.id, User.email, explicit_row.label("explicit"))
        .where(User.is_active.is_(True))
        .where(or_(no_restriction, explicit_row))
        .order_by(User.email)
    )
    uac_result = await session.execute(uac_stmt)
    allowed_users = [
        AppUserRef(user_id=str(uid), email=email, explicit=bool(exp))
        for uid, email, exp in uac_result.all()
    ]

    return AppPolicyResponse(
        client=client,
        allowed_models=allowed_models,
        all_models=all_models,
        default_model=default_model,
        allowed_users=allowed_users,
        web_search_enabled=web_search_enabled,
    )


# ── Endpoints ──


@router.get("/{client}", response_model=AppPolicyResponse)
async def get_app_policy(
    client: str,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """Return the app-centric policy view for a given client.

    - allowed_models: ACTIVE model aliases accessible to this client
      (NULL allowed_clients means no client restriction = accessible to all).
    - default_model: from model.routing_profiles (may be null).
    - allowed_users: users allowed for this client — explicit rows OR users with
      no restrictions at all (fail-open, matching effective policy semantics).
    """
    if client not in _VALID_CLIENTS:
        raise ValidationError(
            f"Unknown client '{client}'. Valid values: {sorted(_VALID_CLIENTS)}"
        )

    return await _build_app_policy(session, client)


@router.patch("/{client}/default-model", response_model=AppPolicyResponse)
async def set_default_model(
    client: str,
    body: DefaultModelPatchRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """Set the default model for a client in model.routing_profiles.

    Validates that the alias exists in model_aliases AND that it is actually
    **reachable** for this client (ACTIVE, allowed for the app, and on the same route
    family as the client's current default) before writing — see
    ``_default_model_rejection``. Existence alone used to be the whole check, so the
    console could save a 200-OK default that made every request for that app fail at
    request time.
    Invalidates the routing_profile:{client} cache key after a successful update.
    """
    if client not in _VALID_CLIENTS:
        raise ValidationError(
            f"Unknown client '{client}'. Valid values: {sorted(_VALID_CLIENTS)}"
        )

    # Validate that the alias exists
    model = await session.get(ModelAlias, body.default_model)
    if model is None:
        raise NotFoundError("ModelAlias", body.default_model)

    # Ensure a routing_profiles row already exists for this client — we only UPDATE,
    # never INSERT, to avoid guessing the backend/region values and violating the
    # CHECK (backend IN ('invoke','mantle')) constraint.
    # `default_model` 도 같이 읽는다(별도 SELECT 를 추가하지 않기 위해): 현재 default 의
    # provider 가 이 client 의 경로 계열을 알려주는 유일한 데이터 신호다 —
    # `routing_profiles.backend` 는 세 client 모두 'mantle' 이라 계열을 구분하지 못한다
    # (routers/openai_compat.py::_is_responses_capable 의 설명과 같은 이유).
    rp_check = await session.execute(
        text("SELECT default_model FROM model.routing_profiles WHERE client = :client"),
        {"client": client},
    )
    rp_row = rp_check.fetchone()
    if rp_row is None:
        raise NotFoundError("routing_profiles", client)

    current_default_alias: str | None = rp_row[0]
    current_default_provider: str | None = None
    if current_default_alias and current_default_alias != body.default_model:
        current_row = await session.get(ModelAlias, current_default_alias)
        # dangling default(행이 지워진 alias)면 비교 기준이 없다 → 계열 검사는 건너뛴다.
        if current_row is not None:
            current_default_provider = _enum_value(current_row.provider)

    rejection = _default_model_rejection(
        client=client,
        alias=body.default_model,
        status=model.status,
        allowed_clients=model.allowed_clients,
        provider=model.provider,
        current_default_alias=current_default_alias,
        current_default_provider=current_default_provider,
    )
    if rejection is not None:
        raise ValidationError(rejection)

    await session.execute(
        text(
            "UPDATE model.routing_profiles"
            " SET default_model = :default_model, updated_at = NOW()"
            " WHERE client = :client"
        ),
        {"client": client, "default_model": body.default_model},
    )
    # COMMIT BEFORE THE DEL, same rule as routers/routing.py:89-93 and routers/models.py.
    # `flush()` alone leaves the UPDATE uncommitted until `get_db_session` tears down AFTER
    # this handler returns, so a gateway-proxy profile load landing in the DEL→commit window
    # misses the cache, re-reads the row in its own transaction (which cannot see our
    # UPDATE), and SETEXes the PREVIOUS default back for another ROUTING_CACHE_TTL (300 s).
    # Every request for this client in that window is then served the old default model,
    # while this endpoint returned 200 and echoed the new one back.
    # `CacheInvalidationManager.invalidate` DELs immediately (core/cache_invalidation.py:33-35)
    # — its `session=` argument only records failures, it does not defer the delete.
    await session.commit()

    # Invalidate routing_profile cache so gateway-proxy picks up the change
    cache_mgr = request.app.state.cache_mgr
    await cache_mgr.invalidate([f"routing_profile:{client}"], session=session)

    logger.info(
        "admin.set_default_model",
        client=client,
        default_model=body.default_model,
        actor=str(admin.user_id),
    )

    # Return updated policy view (re-read from DB for consistency)
    return await _build_app_policy(session, client)


@router.patch("/{client}/models", response_model=AppPolicyResponse)
async def toggle_app_model(
    client: str,
    body: ModelToggleRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """Toggle whether a model alias is allowed for a given client.

    Mutates the model's allowed_clients array using NULL-materialization semantics:
    NULL (all-apps-allowed) → removing one client materializes the explicit full
    set minus that client so all other clients retain access.

    Invalidates gateway-proxy Redis cache keys for the affected model so the
    change takes effect immediately.
    """
    if client not in _VALID_CLIENTS:
        raise ValidationError(
            f"Unknown client '{client}'. Valid values: {sorted(_VALID_CLIENTS)}"
        )

    model = await session.get(ModelAlias, body.alias)
    if model is None:
        raise NotFoundError("ModelAlias", body.alias)

    model.allowed_clients = _next_allowed_clients(model.allowed_clients, client, body.allowed)
    # COMMIT BEFORE THE DEL — see set_default_model above for the mechanism. It matters more
    # here because this endpoint changes ACCESS CONTROL: revoking `cowork` from an alias and
    # then DELing pre-commit lets a concurrent gateway-proxy read re-SETEX the row with
    # `cowork` still in `allowed_clients` for up to MODEL_CACHE_TTL (300 s). The console
    # reports the revoke as applied while the client keeps access — a change reported as
    # applied but not applied. Safe to commit early: AsyncSessionLocal is built with
    # expire_on_commit=False (core/db.py:27), so `model.allowed_clients` below is still
    # readable, and `_build_app_policy` re-reads from the DB anyway.
    await session.commit()

    cache_mgr = request.app.state.cache_mgr
    await cache_mgr.invalidate(
        [f"model:{body.alias}", f"model:{model.provider_model_id}", "model:list"],
        session=session,
    )
    logger.info(
        "admin.toggle_app_model",
        client=client,
        alias=body.alias,
        allowed=body.allowed,
        result=model.allowed_clients,
    )

    return await _build_app_policy(session, client)
