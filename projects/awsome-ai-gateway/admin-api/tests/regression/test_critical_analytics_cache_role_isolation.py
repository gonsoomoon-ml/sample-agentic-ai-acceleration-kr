# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Regression: /admin/analytics 캐시가 role 격리를 깨뜨리지 않아야 한다.

`GET /admin/analytics` 는 `require_admin_or_team_leader` 이고, **같은 파라미터가 행위자에
따라 다른 데이터를 뜻한다** — `scope='all'` 이 ADMIN 에겐 전사(GLOBAL), TEAM_LEADER 에겐
본인 팀이다(`analytics_service.get_analytics` 의 role 분기).

그래서 캐시 키에 행위자를 넣지 않으면 이런 유출이 난다:

    1. ADMIN 이 GET /admin/analytics?period=2026-09&group_by=team&scope=all
       → 키 `analytics:2026-09:team:all` 에 **전사** cost_summary/by_team/by_user/trends 저장
    2. TTL 안에 TEAM_LEADER 가 **바이트 단위로 동일한** 요청
       → 캐시 적중, 전사 데이터를 그대로 수신

이건 ForbiddenError 격리와 서비스의 fail-closed 불변식을 통째로 무력화한다. 참조 구현도
이 형태로 배포했다가 4분 뒤 스스로 고쳤다.

우리 선택: **ADMIN + scope='all' 에만 캐시**하고 그 외(TEAM_LEADER 전부, team: 범위 전부)는
캐시를 아예 쓰지 않는다. role 을 키에 섞는 대안은 TEAM_LEADER 가 팀마다 달라 team_id 까지
넣어야 하고 그러면 적중률이 사실상 0 이라 복잡도만 늘고 이득이 없다.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.models.auth import UserRole
from app.routers import analytics as an

SOURCE = Path(an.__file__)


# ──────────────────────────────────────────────────────────────────────────────
# 0) 공허성 대조군
# ──────────────────────────────────────────────────────────────────────────────


def test_the_handler_really_consults_role_and_scope_before_caching():
    """핸들러 본문에 role/scope 게이트가 실제로 있는지 — 없으면 아래가 공허하다."""
    src = inspect.getsource(an.get_analytics)
    assert "UserRole.ADMIN" in src, "role 검사가 없다"
    assert 'scope == "all"' in src, "scope 검사가 없다"
    assert "cache_key = None" in src, "기본이 '캐시 안 함' 이 아니다"


# ──────────────────────────────────────────────────────────────────────────────
# 1) 캐시 게이트 — AST 로 조건을 검사한다
# ──────────────────────────────────────────────────────────────────────────────


def _cache_guard_conditions() -> list[str]:
    """`cache_key = ...` 대입을 감싸는 if 조건들을 문자열로 뽑는다."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_analytics"
    )
    conds: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        body_src = "\n".join(ast.unparse(b) for b in node.body)
        if "cache_key = _analytics_cache_key" in body_src:
            conds.append(ast.unparse(node.test))
    return conds


def test_cache_is_gated_on_both_admin_role_and_global_scope():
    """⚠️ 두 조건이 **모두** 있어야 한다. 하나만 있으면 유출이다.

    - role 만 검사 → ADMIN 의 `scope=team:X` 응답이 `scope=team:Y` 요청에 반환된다
      (키에 scope 가 없으므로).
    - scope 만 검사 → TEAM_LEADER 의 `scope=all` 이 ADMIN 의 전사 응답을 받는다.
    """
    conds = _cache_guard_conditions()
    assert conds, "cache_key 대입을 감싸는 if 를 찾지 못했다 — 무조건 캐시하고 있다"
    joined = " ".join(conds)
    assert "UserRole.ADMIN" in joined, f"role 게이트 없음: {conds}"
    assert '"all"' in joined or "'all'" in joined, f"scope 게이트 없음: {conds}"


def test_cache_key_does_not_pretend_to_carry_scope():
    """키가 scope 를 담은 척하면 안 된다 — 담지 않는 대신 호출부가 role 을 검사한다."""
    key = an._analytics_cache_key(period="2026-09", group_by="team", client="all")
    assert "global" in key, f"전사 전용임이 키에 드러나야 한다: {key}"
    assert "2026-09" in key and "team" in key, f"파라미터 누락: {key}"
    # 응답을 바꾸는 group_by, period, client 는 각각 다른 키
    assert key != an._analytics_cache_key(period="2026-09", group_by="user", client="all")
    assert key != an._analytics_cache_key(period="2026-08", group_by="team", client="all")
    assert key != an._analytics_cache_key(period="2026-09", group_by="team", client="codex")
    assert key == an._analytics_cache_key(period="2026-09", group_by="team", client=None)


def test_the_leaky_key_shape_is_not_used():
    """참조 구현이 처음 배포한 유출 키 형태가 재도입되지 않았는지."""
    src = SOURCE.read_text(encoding="utf-8")
    # `analytics:{period}:{group_by}:{scope}` — scope 를 키에 넣고 role 은 안 보는 형태
    assert 'f"analytics:{period}:{group_by}:{scope}"' not in src, (
        "행위자 없는 캐시 키가 재도입됐다 — TEAM_LEADER 가 ADMIN 의 전사 응답을 받는다"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2) 행동 증명 — TEAM_LEADER 요청이 캐시를 읽지도 쓰지도 않는다
# ──────────────────────────────────────────────────────────────────────────────


class _SpyRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.gets: list[str] = []
        self.sets: list[str] = []

    async def get(self, key):
        self.gets.append(key)
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.sets.append(key)
        self.store[key] = value


class _Req:
    def __init__(self, redis, svc) -> None:
        state = type("S", (), {})()
        state.redis = redis
        state.analytics_service = svc
        self.app = type("A", (), {"state": state})()


class _Svc:
    """actor 에 따라 다른 본문을 돌려주는 대역 — 유출이 나면 눈에 보인다."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_analytics(self, session, *, period, group_by, scope, client, actor, **_kw):
        self.calls += 1
        return {"who": actor.role.value if hasattr(actor.role, "value") else str(actor.role),
                "scope": scope, "secret": "ORG_WIDE" if actor.role == UserRole.ADMIN else "TEAM_ONLY"}


def _actor(role: UserRole, team_id=None):
    import uuid as _u
    return type("U", (), {"role": role, "team_id": team_id, "user_id": _u.uuid4()})()


@pytest.mark.asyncio
async def test_team_leader_never_reads_the_admin_cache_entry():
    """⚠️ 핵심 — ADMIN 이 캐시를 채운 뒤 TEAM_LEADER 가 같은 요청을 해도 전사 데이터가 안 간다."""
    redis, svc = _SpyRedis(), _Svc()
    req = _Req(redis, svc)

    admin_out = await an.get_analytics(
        req, period="2026-09", group_by="team", scope="all",
        user=_actor(UserRole.ADMIN), session=None,
    )
    assert admin_out["secret"] == "ORG_WIDE"
    assert redis.sets, "ADMIN 응답이 캐시되지 않았다 — 캐시가 아예 동작하지 않는 것"

    import uuid
    tl_out = await an.get_analytics(
        req, period="2026-09", group_by="team", scope="all",
        user=_actor(UserRole.TEAM_LEADER, team_id=uuid.uuid4()), session=None,
    )
    assert tl_out["secret"] == "TEAM_ONLY", (
        f"TEAM_LEADER 가 ADMIN 의 전사 응답을 받았다 — 캐시 유출: {tl_out}"
    )
    assert svc.calls == 2, "TEAM_LEADER 요청이 서비스를 우회했다(캐시 적중)"


@pytest.mark.asyncio
async def test_team_leader_request_touches_redis_not_at_all():
    """TEAM_LEADER 경로는 캐시를 읽지도 쓰지도 않아야 한다(키 설계 실수의 여지 제거)."""
    import uuid
    redis, svc = _SpyRedis(), _Svc()
    req = _Req(redis, svc)
    await an.get_analytics(
        req, period="2026-09", group_by="team", scope="all",
        user=_actor(UserRole.TEAM_LEADER, team_id=uuid.uuid4()), session=None,
    )
    assert redis.gets == [], f"TEAM_LEADER 가 캐시를 조회했다: {redis.gets}"
    assert redis.sets == [], f"TEAM_LEADER 응답이 캐시에 저장됐다: {redis.sets}"


@pytest.mark.asyncio
async def test_admin_team_scoped_request_is_not_cached():
    """ADMIN 이라도 `scope=team:X` 는 캐시하지 않는다 — 키에 scope 가 없기 때문."""
    import uuid
    redis, svc = _SpyRedis(), _Svc()
    req = _Req(redis, svc)
    await an.get_analytics(
        req, period="2026-09", group_by="team", scope=f"team:{uuid.uuid4()}",
        user=_actor(UserRole.ADMIN), session=None,
    )
    assert redis.gets == [] and redis.sets == [], (
        f"team: 범위가 캐시됐다 — 다른 팀 요청에 이 응답이 반환된다 gets={redis.gets} sets={redis.sets}"
    )


@pytest.mark.asyncio
async def test_admin_global_request_is_cached_and_hits():
    """대조군 — 안전한 조합에서는 캐시가 실제로 동작해야 한다(성능 목적 달성)."""
    redis, svc = _SpyRedis(), _Svc()
    req = _Req(redis, svc)
    a = await an.get_analytics(
        req, period="2026-09", group_by="model", scope="all",
        user=_actor(UserRole.ADMIN), session=None,
    )
    b = await an.get_analytics(
        req, period="2026-09", group_by="model", scope="all",
        user=_actor(UserRole.ADMIN), session=None,
    )
    assert a == b
    assert svc.calls == 1, f"두 번째 요청이 캐시를 타지 않았다 (calls={svc.calls})"
