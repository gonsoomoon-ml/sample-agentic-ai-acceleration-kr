# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Regression: 대시보드 백엔드 Redis 캐시 — 정합성이 성능보다 먼저다.

대시보드가 관리자 1명당 4개 API 를 매번 DB 로 때려서 느렸다. 백엔드에 30초 TTL 캐시를
두어 해결하는데, 캐시는 **잘못 만들면 데이터 유출 장치**가 되므로 다음을 못박는다:

1. **행위자 무관성이 캐시의 전제다.** /admin/dashboard/* 는 전부 `require_admin` 이라
   같은 파라미터면 누가 불러도 같은 응답이다. 그래서 키에 actor 가 없어도 안전하다.
   ⚠️ 대조: /admin/analytics 는 `require_admin_or_team_leader` 라 `scope='all'` 이
   ADMIN 에겐 전사·TEAM_LEADER 에겐 팀 범위를 뜻한다. 거기서 actor 없는 키를 쓰면
   ADMIN 이 채운 전사 데이터를 30초 안에 TEAM_LEADER 가 그대로 받는다(실제 사고 패턴).
   이 테스트는 그 전제(=전부 require_admin)가 깨지는 순간 실패한다.
2. **응답을 바꾸는 파라미터가 전부 키에 있어야 한다.** 하나라도 빠지면 다른 질의의
   결과가 반환된다(team_id 누락 → A팀 화면에 B팀 점유율).
3. **None 과 'all' 은 같은 키여야 한다.** admin-ui 는 전체를 'all' 로 보내고 라우터
   기본값은 None 이다. 정규화하지 않으면 적중률이 반토막 나고 무효화가 한쪽만 된다.
4. **Redis 장애는 요청 실패가 아니다**(fail-open). 캐시는 성능 장치일 뿐이다.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


from app.routers import dashboard as dash

SOURCE = Path(dash.__file__)


class _FakeRedis:
    """setex/get 만 있는 최소 대역. 호출 횟수를 세어 적중을 증명한다."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail = fail
        self.gets = 0
        self.sets = 0

    async def get(self, key: str):
        self.gets += 1
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.sets += 1
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = value


class _FakeApp:
    def __init__(self, redis) -> None:
        self.state = type("S", (), {"redis": redis})()


class _FakeRequest:
    def __init__(self, redis) -> None:
        self.app = _FakeApp(redis)


# ──────────────────────────────────────────────────────────────────────────────
# 0) 공허성 대조군
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_fake_redis_actually_round_trips():
    """하네스가 저장/조회를 실제로 하는지 — 아니면 아래 단정이 전부 공허해진다."""
    redis = _FakeRedis()
    req = _FakeRequest(redis)
    assert await dash._cache_get(req, "k") is None
    await dash._cache_set(req, "k", {"a": 1})
    assert redis.sets == 1
    assert await dash._cache_get(req, "k") == {"a": 1}


# ──────────────────────────────────────────────────────────────────────────────
# 1) 키 설계
# ──────────────────────────────────────────────────────────────────────────────


def test_none_and_all_collapse_to_the_same_key():
    """admin-ui 의 'all' 과 라우터 기본값 None 이 같은 캐시 항목을 써야 한다."""
    a = dash._cache_key("summary", period="2026-09", client=None)
    b = dash._cache_key("summary", period="2026-09", client="all")
    c = dash._cache_key("summary", period="2026-09", client="")
    assert a == b == c, f"정규화 실패: {a!r} {b!r} {c!r}"


def test_distinct_filters_get_distinct_keys():
    """값이 다르면 키가 달라야 한다 — 안 그러면 남의 데이터가 반환된다."""
    base = dash._cache_key("model-share", period="2026-09", team_id="all", client=None)
    variants = [
        dash._cache_key("model-share", period="2026-08", team_id="all", client=None),
        dash._cache_key("model-share", period="2026-09", team_id="team-a", client=None),
        dash._cache_key("model-share", period="2026-09", team_id="all", client="codex"),
        dash._cache_key("summary", period="2026-09", team_id="all", client=None),
    ]
    assert len(set(variants)) == len(variants), f"키가 충돌한다: {variants}"
    assert base not in variants, "다른 파라미터가 같은 키를 만든다"


def test_key_is_order_independent():
    """kwargs 순서가 키를 바꾸면 같은 질의가 두 항목으로 갈린다."""
    assert dash._cache_key("s", a=1, b=2) == dash._cache_key("s", b=2, a=1)


def test_every_response_shaping_param_is_in_the_key():
    """⚠️ AST 가드 — 핸들러의 Query 파라미터가 모두 캐시 키에 들어가는지.

    파라미터를 새로 추가하고 키에 넣는 걸 잊으면, 그 파라미터를 바꿔도 이전 응답이
    반환된다. 문자열 grep 이 아니라 함수 시그니처와 _cache_key 호출 인자를 대조한다.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    checked = 0
    for fn in tree.body:
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        # _cache_key 호출이 있는 핸들러만 대상
        keys: set[str] = set()
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_cache_key"
            ):
                keys |= {kw.arg for kw in node.keywords if kw.arg}
        if not keys:
            continue
        checked += 1
        # Query(...) 기본값을 가진 파라미터 = 응답을 바꾸는 입력
        params = {
            a.arg
            for a, d in zip(fn.args.args[-len(fn.args.defaults):], fn.args.defaults)
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Name)
            and d.func.id == "Query"
        }
        missing = params - keys
        assert not missing, (
            f"{fn.name}: Query 파라미터 {sorted(missing)} 가 캐시 키에 없다 — 그 값을 "
            f"바꿔도 이전 응답이 반환된다"
        )
    # 대조군 — 캐시를 쓰는 핸들러를 하나도 못 찾았으면 위 루프가 공허하다.
    assert checked >= 2, f"캐시 사용 핸들러를 {checked}개만 찾았다"


# ──────────────────────────────────────────────────────────────────────────────
# 2) fail-open
# ──────────────────────────────────────────────────────────────────────────────


async def test_redis_failure_is_a_miss_not_an_error():
    """Redis 가 죽어도 요청은 살아야 한다."""
    redis = _FakeRedis(fail=True)
    req = _FakeRequest(redis)
    assert await dash._cache_get(req, "k") is None  # 예외가 올라오지 않는다
    await dash._cache_set(req, "k", {"a": 1})  # 여기서도 안 터진다
    assert redis.gets == 1 and redis.sets == 1


async def test_absent_redis_is_a_miss_not_an_error():
    """app.state.redis 가 아예 없는 배치(테스트/로컬)에서도 동작해야 한다."""
    req = _FakeRequest(None)
    assert await dash._cache_get(req, "k") is None
    await dash._cache_set(req, "k", {"a": 1})


async def test_corrupt_cache_entry_is_a_miss():
    """캐시에 JSON 이 아닌 값이 들어 있어도 500 이 아니라 miss 여야 한다."""
    redis = _FakeRedis()
    redis.store["k"] = "not-json{"
    assert await dash._cache_get(_FakeRequest(redis), "k") is None


# ──────────────────────────────────────────────────────────────────────────────
# 3) TTL 과 직렬화
# ──────────────────────────────────────────────────────────────────────────────


async def test_ttl_is_bounded_and_short():
    """TTL 은 편집 후 반영 지연의 상한이다 — 무기한/과도하게 길면 안 된다."""
    assert 0 < dash._DASHBOARD_CACHE_TTL <= 60, dash._DASHBOARD_CACHE_TTL
    captured: list[int] = []

    class _R(_FakeRedis):
        async def setex(self, key, ttl, value):
            captured.append(ttl)
            return await super().setex(key, ttl, value)

    await dash._cache_set(_FakeRequest(_R()), "k", {"a": 1})
    assert captured == [dash._DASHBOARD_CACHE_TTL], captured


async def test_decimal_payload_survives_serialization():
    """집계 응답에는 Decimal 이 섞일 수 있다 — json.dumps 가 터지면 캐시 저장이 실패한다."""
    from decimal import Decimal

    redis = _FakeRedis()
    req = _FakeRequest(redis)
    await dash._cache_set(req, "k", {"cost": Decimal("1.23")})
    assert redis.sets == 1, "Decimal 때문에 저장이 실패했다"
    assert json.loads(redis.store["k"])["cost"] == "1.23"


# ──────────────────────────────────────────────────────────────────────────────
# 4) 캐시의 전제: require_admin 이거나, 리더면 키에 유효 scope 가 있어야 한다
# ──────────────────────────────────────────────────────────────────────────────


def test_all_cached_handlers_are_admin_only():
    """⚠️ 캐시 유출 방지 조건을 고정한다.

    require_admin_or_team_leader 핸들러는 응답이 행위자 scope 에 따라 달라지므로
    (ADMIN=전사, TEAM_LEADER=본인 팀), 캐시 키에 scope 판별자가 없으면 ADMIN 이 채운
    전사 데이터를 TEAM_LEADER 가 받는다. 리더를 여는 핸들러는 캐시 키에 **유효 scope**
    (eff_scope / eff_team — 파라미터가 아니라 강제 적용된 값)를 반드시 넣어야 한다.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    offenders = []
    checked = 0
    for fn in tree.body:
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        src = ast.unparse(fn)
        if "_cache_key(" not in src:
            continue
        checked += 1
        if "require_admin" not in src:
            offenders.append(f"{fn.name}: require_admin 의존성 없음")
        if "require_admin_or_team_leader" in src and not (
            "eff_scope" in src or "eff_team" in src
        ):
            offenders.append(
                f"{fn.name}: require_admin_or_team_leader 인데 캐시 키에 유효 scope "
                f"(eff_scope/eff_team)가 없다 — TEAM_LEADER 가 ADMIN 의 전사 응답을 받는다"
            )
    assert checked >= 2, f"캐시 사용 핸들러를 {checked}개만 찾았다"
    assert offenders == [], "\n  ".join(offenders)
