# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Unit tests for /admin/apps router."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.clients import VALID_CLIENTS
from app.core.exceptions import ValidationError, NotFoundError
from app.routers.apps import (
    get_app_policy,
    set_default_model,
    toggle_app_model,
    DefaultModelPatchRequest,
    ModelToggleRequest,
    AppPolicyResponse,
    _next_allowed_clients,
)


def _make_request(client_name: str = "claude-code") -> MagicMock:
    req = MagicMock()
    req.app.state.cache_mgr = MagicMock()
    req.app.state.cache_mgr.invalidate = AsyncMock()
    return req


def _reachable_alias(
    alias: str = "claude-sonnet",
    *,
    provider=None,
    status=None,
    allowed_clients=None,
) -> MagicMock:
    """`_default_model_rejection` 를 통과하는(=런타임에 실제로 풀리는) alias 행 더블.

    MagicMock 은 아무 속성이나 MagicMock 으로 돌려주므로, 새로 추가된 세 판정
    (status / allowed_clients / provider)이 읽는 필드를 **명시적으로** 채워야 한다.
    """
    from app.models.model import ModelAlias as ModelAliasModel
    from app.models.model import ModelStatus, Provider

    row = MagicMock(spec=ModelAliasModel)
    row.alias = alias
    row.provider_model_id = f"pmid.{alias}"
    row.provider = provider if provider is not None else Provider.BEDROCK
    row.status = status if status is not None else ModelStatus.ACTIVE
    row.allowed_clients = allowed_clients
    return row


def _make_admin_user():
    from app.core.auth import CurrentUser
    from app.models.auth import UserRole
    return CurrentUser(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        email="admin@test.com",
        role=UserRole.ADMIN,
        team_id=uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
    )


@pytest.mark.asyncio
async def test_get_app_policy_invalid_client(mock_session: AsyncMock):
    with pytest.raises(ValidationError):
        await get_app_policy("unknown-app", _make_request(), _make_admin_user(), mock_session)


@pytest.mark.asyncio
async def test_get_app_policy_returns_allowed_models(mock_session: AsyncMock):
    """GET /admin/apps/claude-code returns the models accessible to claude-code.

    Execute call order:
      1. text SQL: allowed_models  → fetchall() returns list of (alias,) tuples
      2. text SQL: all_models      → fetchall() returns list of (alias, allowed_clients) tuples
      3. text SQL: routing_profile → fetchone() returns a row tuple
      4. ORM select: allowed users → all() returns (uuid, email, explicit) tuples
    """
    user_uuid = uuid.UUID("00000000-0000-0000-0000-000000000099")
    open_uuid = uuid.UUID("00000000-0000-0000-0000-000000000088")
    call_count = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # allowed_models text query: fetchall returns list of (alias,) tuples
            result.fetchall.return_value = [("claude-haiku",), ("claude-sonnet",)]
        elif call_count == 2:
            # all_models text query: fetchall returns (alias, allowed_clients) tuples
            result.fetchall.return_value = [("m1", ["cowork"]), ("m2", None)]
        elif call_count == 3:
            # routing_profiles text query (default_model, web_search_enabled)
            result.fetchone.return_value = ("claude-sonnet", True)
        else:
            # ORM select for allowed users: all() returns (uuid, email, explicit) rows.
            # explicit=False = 명시 행이 없는 사용자 — fail-open(전체 앱 허용)으로 포함.
            result.all.return_value = [
                (user_uuid, "user@example.com", True),
                (open_uuid, "open@example.com", False),
            ]
        return result

    mock_session.execute = AsyncMock(side_effect=_execute)

    resp = await get_app_policy("claude-code", _make_request(), _make_admin_user(), mock_session)

    assert resp.client == "claude-code"
    assert "claude-sonnet" in resp.allowed_models
    assert "claude-haiku" in resp.allowed_models
    assert resp.default_model == "claude-sonnet"
    assert len(resp.allowed_users) == 2
    assert resp.allowed_users[0].user_id == str(user_uuid)
    assert resp.allowed_users[0].email == "user@example.com"
    assert resp.allowed_users[0].explicit is True
    assert resp.allowed_users[1].user_id == str(open_uuid)
    assert resp.allowed_users[1].explicit is False
    assert resp.web_search_enabled is True
    # all_models assertions
    assert len(resp.all_models) == 2
    assert resp.all_models[0].allowed_clients == ["cowork"]
    assert resp.all_models[1].allowed_clients is None


@pytest.mark.asyncio
async def test_get_app_policy_no_routing_profile(mock_session: AsyncMock):
    """When no routing_profile row exists, default_model is None."""
    call_count = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # allowed_models
            result.fetchall.return_value = []
        elif call_count == 2:
            # all_models
            result.fetchall.return_value = []
        elif call_count == 3:
            # routing_profiles
            result.fetchone.return_value = None
        else:
            # ORM JOIN select for user_allowed_clients: all() returns (uuid, email) rows
            result.all.return_value = []
        return result

    mock_session.execute = AsyncMock(side_effect=_execute)

    resp = await get_app_policy("cowork", _make_request(), _make_admin_user(), mock_session)

    assert resp.client == "cowork"
    assert resp.default_model is None
    assert resp.web_search_enabled is False


@pytest.mark.asyncio
async def test_set_default_model_invalid_client(mock_session: AsyncMock):
    body = DefaultModelPatchRequest(default_model="some-model")
    with pytest.raises(ValidationError):
        await set_default_model("bad-client", body, _make_request(), _make_admin_user(), mock_session)


@pytest.mark.asyncio
async def test_set_default_model_alias_not_found(mock_session: AsyncMock):
    """If the alias does not exist, 404 NotFoundError should be raised."""
    mock_session.get = AsyncMock(return_value=None)

    body = DefaultModelPatchRequest(default_model="nonexistent-model")
    with pytest.raises(NotFoundError):
        await set_default_model("claude-code", body, _make_request(), _make_admin_user(), mock_session)


@pytest.mark.asyncio
async def test_set_default_model_no_routing_profile_returns_404(mock_session: AsyncMock):
    """PATCH on a client with no routing_profiles row must raise NotFoundError (FIX 2).

    Sequence:
      1. session.get(ModelAlias, ...) → returns a valid alias object (alias exists).
      2. session.execute(SELECT client FROM routing_profiles ...) → fetchone() is None
         (no routing_profiles row for this client).
    Expect: NotFoundError raised so the endpoint returns 404.
    """
    from app.models.model import ModelAlias as ModelAliasModel

    # Alias lookup succeeds — the alias itself is valid.
    fake_alias = MagicMock(spec=ModelAliasModel)
    fake_alias.alias = "claude-sonnet"
    mock_session.get = AsyncMock(return_value=fake_alias)

    # routing_profiles check: no row found.
    rp_result = MagicMock()
    rp_result.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=rp_result)

    body = DefaultModelPatchRequest(default_model="claude-sonnet")
    with pytest.raises(NotFoundError):
        await set_default_model("claude-code", body, _make_request(), _make_admin_user(), mock_session)


@pytest.mark.asyncio
async def test_set_default_model_commits_before_invalidating(mock_session: AsyncMock):
    """The routing_profile DEL must land AFTER the commit, not after `flush()`.

    `get_db_session` commits on teardown (core/db.py:34), i.e. after the handler has already
    returned — so a flush-then-DEL leaves a window in which a gateway-proxy profile load
    misses the cache, re-reads `routing_profiles` in its own transaction (not seeing our
    UPDATE), and SETEXes the PREVIOUS default_model back for another ROUTING_CACHE_TTL. Every
    request for this client in that window is served the old default while this endpoint
    returned 200 with the new one. `CacheInvalidationManager.invalidate` deletes immediately
    (core/cache_invalidation.py:33-35); its `session=` argument only records failures.
    """
    from app.routers import apps as apps_module

    # `_default_model_rejection` 를 통과하는 alias 여야 한다(ACTIVE + 이 앱에 허용).
    fake_alias = _reachable_alias("claude-sonnet")
    mock_session.get = AsyncMock(return_value=fake_alias)

    rp_result = MagicMock()
    # 현재 default 가 곧 새 default(no-op PATCH) → 경로 계열 비교 기준이 자기 자신.
    rp_result.fetchone.return_value = ("claude-sonnet",)  # routing_profiles row exists
    mock_session.execute = AsyncMock(return_value=rp_result)

    order: list[str] = []
    mock_session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    mock_session.flush = AsyncMock(side_effect=lambda: order.append("flush"))

    req = _make_request()
    req.app.state.cache_mgr.invalidate = AsyncMock(
        side_effect=lambda *_a, **_k: order.append("invalidate")
    )

    dummy = AppPolicyResponse(
        client="claude-code",
        allowed_models=[],
        all_models=[],
        default_model="claude-sonnet",
        allowed_users=[],
    )
    with patch.object(apps_module, "_build_app_policy", new=AsyncMock(return_value=dummy)):
        body = DefaultModelPatchRequest(default_model="claude-sonnet")
        await set_default_model("claude-code", body, req, _make_admin_user(), mock_session)

    assert order == ["commit", "invalidate"], (
        f"expected commit-then-invalidate; got {order}"
    )
    req.app.state.cache_mgr.invalidate.assert_awaited_once_with(
        ["routing_profile:claude-code"], session=mock_session
    )


def test_next_allowed_clients_transitions():
    # allow=True
    assert _next_allowed_clients(None, "cowork", True) is None            # already all-allowed → no-op
    assert _next_allowed_clients(["claude-code"], "cowork", True) == ["claude-code", "cowork"]
    assert _next_allowed_clients(["cowork"], "cowork", True) == ["cowork"] # already present
    assert _next_allowed_clients(["claude-code"], "codex", True) == ["claude-code", "codex"]
    # allow=False (remove) — NULL materializes to explicit full set minus this client.
    # Derived from VALID_CLIENTS, not hardcoded: denying one app must leave EVERY other
    # app's access intact, so a newly added client has to appear here automatically.
    assert _next_allowed_clients(None, "cowork", False) == sorted(
        set(VALID_CLIENTS) - {"cowork"}
    )
    assert _next_allowed_clients(None, "codex", False) == sorted(
        set(VALID_CLIENTS) - {"codex"}
    )
    assert _next_allowed_clients(["claude-code", "cowork"], "cowork", False) == ["claude-code"]
    # 마지막 앱 해제 → [] = "허용 앱 없음". None(제한 없음)으로 접으면 안 된다:
    # 운영자의 "전부 해제" 가 "전부 허용" 이 되는 게 정확히 이 결함이었다.
    assert _next_allowed_clients(["cowork"], "cowork", False) == []
    assert _next_allowed_clients(["cowork"], "cowork", False) is not None
    # 그리고 다시 켜면 그 앱만 담긴 목록으로 복귀한다([] 에서 되돌릴 수 있어야 한다).
    assert _next_allowed_clients([], "cowork", True) == ["cowork"]


@pytest.mark.asyncio
async def test_toggle_app_model_null_materialization(mock_session: AsyncMock):
    """PATCH /admin/apps/cowork/models with allowed=False and allowed_clients=None
    materializes to every OTHER client and invalidates the 3 model cache keys.

    NOTE the semantics: allowed_clients=None already means "all apps allowed", so an
    all-allowed alias was ALWAYS reachable by every client, codex included. Materializing
    the complement preserves that — it does not widen access. Narrowing it to just
    claude-code would be the bug (it would silently revoke codex along with cowork).
    """
    from app.models.model import ModelAlias as ModelAliasModel
    from app.routers import apps as apps_module

    alias = "claude-opus"
    provider_model_id = "anthropic.claude-opus-4-5"

    # Fake model with allowed_clients=None (all-apps-allowed)
    fake_model = MagicMock(spec=ModelAliasModel)
    fake_model.alias = alias
    fake_model.provider_model_id = provider_model_id
    fake_model.allowed_clients = None

    mock_session.get = AsyncMock(return_value=fake_model)

    # Record the ORDER of commit vs the cache DEL, not merely that both happened. This
    # endpoint changes access control, and a DEL that lands before the commit lets a
    # concurrent gateway-proxy read re-cache the PRE-revoke allowed_clients for another
    # MODEL_CACHE_TTL — the revoke is reported as applied while the client keeps access.
    order: list[str] = []
    mock_session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    mock_session.flush = AsyncMock(side_effect=lambda: order.append("flush"))

    req = _make_request()
    req.app.state.cache_mgr.invalidate = AsyncMock(
        side_effect=lambda *_a, **_k: order.append("invalidate")
    )

    # Patch _build_app_policy to isolate toggle logic from DB queries
    dummy_response = AppPolicyResponse(
        client="cowork",
        allowed_models=[],
        all_models=[],
        default_model=None,
        allowed_users=[],
    )
    with patch.object(apps_module, "_build_app_policy", new=AsyncMock(return_value=dummy_response)):
        body = ModelToggleRequest(alias=alias, allowed=False)
        result = await toggle_app_model("cowork", body, req, _make_admin_user(), mock_session)

    # allowed_clients should have been mutated to exclude "cowork" and keep the rest
    assert fake_model.allowed_clients == sorted(set(VALID_CLIENTS) - {"cowork"})

    # The write must be COMMITTED before the cache is invalidated.
    assert order == ["commit", "invalidate"], (
        "expected commit-then-invalidate (routers/routing.py:89-93 and routers/models.py "
        f"precedent); got {order}. A DEL before the commit can be re-seeded stale by a "
        "concurrent gateway-proxy read, so the revoke silently does not take effect."
    )

    # cache_mgr.invalidate must have been called with the 3 model keys
    expected_keys = [f"model:{alias}", f"model:{provider_model_id}", "model:list"]
    req.app.state.cache_mgr.invalidate.assert_awaited_once_with(
        expected_keys, session=mock_session
    )

    # endpoint returns the policy response
    assert result is dummy_response


# ===========================================================================
# PATCH /admin/apps/{client}/default-model — reachability validation
#
# 존재 확인만으로는 부족하다: default_model 은 요청이 모델을 안 보냈거나(cowork Rule A)
# 보낸 이름이 우리 alias 가 아닐 때(codex 는 항상 upstream 이름을 보내므로 사실상 모든
# 요청) 실제로 쓰이는 값이라, 콘솔에서 200 OK 로 저장된 잘못된 default 는 그 앱의 모든
# 요청을 런타임에 실패시킨다.
# ===========================================================================


def test_route_family_groups_only_the_two_responses_providers():
    from app.models.model import Provider
    from app.routers.apps import _route_family

    # responses 두 provider = 한 계열(resolve_responses_model 이 둘 다 받는다, INV-2).
    assert _route_family(Provider.BEDROCK_MANTLE_OPENAI.value) == _route_family(
        Provider.BEDROCK_RUNTIME_OPENAI.value
    )
    # 그 밖에는 provider 하나가 곧 계열 — 서로 섞이면 resolver 가 못 푼다.
    assert _route_family(Provider.BEDROCK.value) != _route_family(Provider.BEDROCK_MANTLE.value)
    assert _route_family(Provider.BEDROCK.value) != _route_family(
        Provider.BEDROCK_MANTLE_OPENAI.value
    )


def _rejection(**over):
    from app.models.model import ModelStatus, Provider
    from app.routers.apps import _default_model_rejection

    kwargs = dict(
        client="codex",
        alias="codex-rt-gpt-5.6-terra",
        status=ModelStatus.ACTIVE,
        allowed_clients=["codex"],
        provider=Provider.BEDROCK_RUNTIME_OPENAI,
        current_default_alias="codex-gpt-5.6-terra",
        current_default_provider=Provider.BEDROCK_MANTLE_OPENAI,
    )
    kwargs.update(over)
    return _default_model_rejection(**kwargs)


def test_default_model_accepts_mantle_to_runtime_track_swap():
    """codex default 를 mantle↔runtime 트랙 사이에서 옮기는 것은 지원되는 운영 액션."""
    assert _rejection() is None


def test_default_model_rejects_inactive_alias():
    from app.models.model import ModelStatus

    msg = _rejection(status=ModelStatus.INACTIVE)
    assert msg is not None and "INACTIVE" in msg


def test_default_model_rejects_alias_not_allowed_for_this_app():
    msg = _rejection(allowed_clients=["cowork"])
    assert msg is not None
    assert "not available to app 'codex'" in msg
    assert "cowork" in msg  # 어디에 제한돼 있는지 알려준다


def test_default_model_rejects_alias_with_explicitly_empty_allowlist():
    """allowed_clients=[] = 허용 앱 없음 → 어떤 앱의 default 도 될 수 없다.

    예전 게이트웨이 의미([] = allow-all)에서는 이게 통과했고, 의미를 바로잡은 뒤에는
    "저장은 되는데 매 요청 거부" 가 되므로 저장 시점에 막아야 한다.
    """
    msg = _rejection(allowed_clients=[])
    assert msg is not None
    assert "explicitly empty" in msg


def test_default_model_accepts_unrestricted_alias():
    """allowed_clients=None = 제한 없음 → 앱 게이트는 통과(계열만 맞으면 된다)."""
    from app.models.model import Provider

    assert (
        _rejection(
            allowed_clients=None,
            provider=Provider.BEDROCK_MANTLE_OPENAI,
        )
        is None
    )


def test_default_model_rejects_cross_route_family():
    """codex default 를 Bedrock alias 로 바꾸면 /v1/responses 가 그걸 못 푼다."""
    from app.models.model import Provider

    msg = _rejection(alias="claude-opus-4-8", provider=Provider.BEDROCK, allowed_clients=None)
    assert msg is not None
    assert "not reachable on the route" in msg
    assert "codex-gpt-5.6-terra" in msg  # 현재 값도 같이 보여준다


def test_default_model_allows_when_no_current_default_to_compare_against():
    """현재 default 가 없으면(또는 dangling) 계열을 데이터로 반증할 수 없다 → 통과.

    반증 불가능한 것을 막으면 처음 default 를 세팅하는 정상 흐름이 막힌다.
    """
    from app.models.model import Provider

    assert (
        _rejection(
            provider=Provider.BEDROCK,
            allowed_clients=None,
            current_default_alias=None,
            current_default_provider=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_set_default_model_rejects_cross_family_without_writing(mock_session: AsyncMock):
    """핸들러 레벨: 계열이 다른 default 는 400 이고, UPDATE/commit 이 일어나지 않는다."""
    from app.models.model import Provider

    new_alias = _reachable_alias("claude-opus-4-8", provider=Provider.BEDROCK)
    current_alias = _reachable_alias(
        "codex-gpt-5.6-terra",
        provider=Provider.BEDROCK_MANTLE_OPENAI,
        allowed_clients=["codex"],
    )
    mock_session.get = AsyncMock(side_effect=[new_alias, current_alias])

    executed: list[str] = []

    async def _execute(stmt, *args, **kwargs):
        executed.append(str(stmt))
        result = MagicMock()
        result.fetchone.return_value = ("codex-gpt-5.6-terra",)
        return result

    mock_session.execute = AsyncMock(side_effect=_execute)
    mock_session.commit = AsyncMock()

    req = _make_request()
    body = DefaultModelPatchRequest(default_model="claude-opus-4-8")
    with pytest.raises(ValidationError) as exc:
        await set_default_model("codex", body, req, _make_admin_user(), mock_session)

    assert "not reachable on the route" in str(exc.value)
    assert not any("UPDATE" in s for s in executed), f"must not write; executed={executed}"
    mock_session.commit.assert_not_awaited()
    req.app.state.cache_mgr.invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_default_model_rejects_app_denied_alias_without_writing(mock_session: AsyncMock):
    """핸들러 레벨: 그 앱에 허용되지 않은 alias([] 포함)는 400 이고 쓰지 않는다."""
    denied = _reachable_alias("codex-gpt-5.6-sol", allowed_clients=[])
    mock_session.get = AsyncMock(return_value=denied)

    executed: list[str] = []

    async def _execute(stmt, *args, **kwargs):
        executed.append(str(stmt))
        result = MagicMock()
        result.fetchone.return_value = ("codex-gpt-5.6-terra",)
        return result

    mock_session.execute = AsyncMock(side_effect=_execute)
    mock_session.commit = AsyncMock()

    body = DefaultModelPatchRequest(default_model="codex-gpt-5.6-sol")
    with pytest.raises(ValidationError) as exc:
        await set_default_model("codex", body, _make_request(), _make_admin_user(), mock_session)

    assert "explicitly empty" in str(exc.value)
    assert not any("UPDATE" in s for s in executed), f"must not write; executed={executed}"
    mock_session.commit.assert_not_awaited()
