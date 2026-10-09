# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.auth import Team, User, UserRole
from app.models.budget import BudgetConfig, BudgetPolicy, BudgetScope
from app.schemas.budgets import AllocateBudgetItem, AllocateBudgetRequest, SetBudgetRequest
from app.services.budget_service import BUDGET_CONFIG_CACHE_TTL, BudgetService

# ── BUDGET_CONFIG_CACHE_TTL 값 확인 ──
assert BUDGET_CONFIG_CACHE_TTL == 300, "BUDGET_CONFIG_CACHE_TTL 은 300초 (5분) 여야 함"


@pytest.fixture
def budget_service(cache_mgr: CacheInvalidationManager) -> BudgetService:
    return BudgetService(cache_mgr=cache_mgr)


class TestSetTeamBudget:
    async def test_set_team_budget_success(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        data = SetBudgetRequest(max_budget_usd=Decimal("1000.00"))

        with patch("app.services.budget_service.UserRepository") as MockUserRepo, \
             patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo, \
             patch("app.services.budget_service.audit_logger") as mock_audit:
            MockUserRepo.return_value.get_team = AsyncMock(return_value=MagicMock(spec=Team))
            MockBudgetRepo.return_value.upsert_config = AsyncMock()
            mock_audit.log = AsyncMock()

            await budget_service.set_team_budget(mock_session, team_id=team_id, data=data, actor=admin_user)

        MockBudgetRepo.return_value.upsert_config.assert_called_once()

    async def test_set_team_budget_not_found(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        data = SetBudgetRequest(max_budget_usd=Decimal("1000.00"))

        with patch("app.services.budget_service.UserRepository") as MockUserRepo:
            MockUserRepo.return_value.get_team = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await budget_service.set_team_budget(mock_session, team_id=team_id, data=data, actor=admin_user)


class TestSetUserBudget:
    async def test_team_leader_cannot_set_other_team_budget(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        user_id = uuid.uuid4()
        data = SetBudgetRequest(max_budget_usd=Decimal("100.00"))

        other_team_id = uuid.uuid4()
        user = MagicMock(spec=User)
        user.team_id = other_team_id  # Different team

        with patch("app.services.budget_service.UserRepository") as MockUserRepo:
            MockUserRepo.return_value.get_user = AsyncMock(return_value=user)

            with pytest.raises(ForbiddenError):
                await budget_service.set_user_budget(mock_session, user_id=user_id, data=data, actor=team_leader_user)

    async def test_user_budget_sum_exceeds_team_budget(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        user_id = uuid.uuid4()
        team_id = uuid.uuid4()
        data = SetBudgetRequest(max_budget_usd=Decimal("600.00"))

        user = MagicMock(spec=User)
        user.team_id = team_id

        team_config = MagicMock(spec=BudgetConfig)
        team_config.max_budget_usd = Decimal("1000.00")

        with patch("app.services.budget_service.UserRepository") as MockUserRepo, \
             patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo:
            MockUserRepo.return_value.get_user = AsyncMock(return_value=user)
            repo = MockBudgetRepo.return_value
            repo.get_active_config = AsyncMock(side_effect=[team_config, None])
            repo.sum_member_budgets = AsyncMock(return_value=Decimal("500.00"))

            with pytest.raises(ValidationError, match="exceeds team budget"):
                await budget_service.set_user_budget(mock_session, user_id=user_id, data=data, actor=admin_user)

    async def test_user_budget_replaces_existing(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        user_id = uuid.uuid4()
        team_id = uuid.uuid4()
        data = SetBudgetRequest(max_budget_usd=Decimal("200.00"))

        user = MagicMock(spec=User)
        user.team_id = team_id

        team_config = MagicMock(spec=BudgetConfig)
        team_config.max_budget_usd = Decimal("1000.00")

        existing_config = MagicMock(spec=BudgetConfig)
        existing_config.max_budget_usd = Decimal("150.00")

        with patch("app.services.budget_service.UserRepository") as MockUserRepo, \
             patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo, \
             patch("app.services.budget_service.audit_logger") as mock_audit:
            MockUserRepo.return_value.get_user = AsyncMock(return_value=user)
            repo = MockBudgetRepo.return_value
            repo.get_active_config = AsyncMock(side_effect=[team_config, existing_config])
            repo.sum_member_budgets = AsyncMock(return_value=Decimal("300.00"))
            repo.upsert_config = AsyncMock()
            mock_audit.log = AsyncMock()

            # current_sum(300) - existing(150) + new(200) = 350 <= 1000 → OK
            await budget_service.set_user_budget(mock_session, user_id=user_id, data=data, actor=admin_user)

        repo.upsert_config.assert_called_once()


class TestAllocateTeamBudget:
    async def test_team_leader_cannot_allocate_other_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        other_team_id = uuid.uuid4()
        data = AllocateBudgetRequest(allocations=[
            AllocateBudgetItem(user_id=str(uuid.uuid4()), allocated_usd=Decimal("100.00")),
        ])

        with pytest.raises(ForbiddenError):
            await budget_service.allocate_team_budget(
                mock_session, team_id=other_team_id, data=data, actor=team_leader_user
            )

    async def test_team_leader_can_allocate_own_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        team_id = team_leader_user.team_id
        data = AllocateBudgetRequest(allocations=[
            AllocateBudgetItem(user_id=str(uuid.uuid4()), allocated_usd=Decimal("100.00")),
        ])

        team_config = MagicMock(spec=BudgetConfig)
        team_config.max_budget_usd = Decimal("1000.00")
        team_config.policy = BudgetPolicy.HARD_BLOCK

        with patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo, \
             patch("app.services.budget_service.audit_logger") as mock_audit:
            repo = MockBudgetRepo.return_value
            repo.get_active_config = AsyncMock(return_value=team_config)
            repo.upsert_config = AsyncMock()
            mock_audit.log = AsyncMock()

            await budget_service.allocate_team_budget(
                mock_session, team_id=team_id, data=data, actor=team_leader_user
            )

        repo.upsert_config.assert_called_once()

    async def test_get_team_allocation_forbidden_for_other_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        """get_team_allocation 은 이전에 actor 검사가 없어 임의 team_id 로 타 팀
        배정을 읽을 수 있었다 — 소속 팀 외에는 403."""
        with pytest.raises(ForbiddenError):
            await budget_service.get_team_allocation(
                mock_session,
                team_id=uuid.uuid4(),
                period="2026-09",
                actor=team_leader_user,
            )

    async def test_get_team_allocation_allowed_for_own_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        team_id = team_leader_user.team_id
        team = MagicMock(spec=Team)
        team.id = team_id
        team.name = "Dev"
        team.dept_id = uuid.uuid4()
        team.department = None
        team.members = []

        team_config = MagicMock(spec=BudgetConfig)
        team_config.max_budget_usd = Decimal("500.00")
        usage = MagicMock()
        usage.used_usd = Decimal("10.00")

        with patch("app.services.budget_service.UserRepository") as MockUserRepo, \
             patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo:
            MockUserRepo.return_value.get_team = AsyncMock(return_value=team)
            repo = MockBudgetRepo.return_value
            repo.get_first_active_config = AsyncMock(return_value=team_config)
            repo.get_usage = AsyncMock(return_value=usage)

            result = await budget_service.get_team_allocation(
                mock_session, team_id=team_id, period="2026-09", actor=team_leader_user
            )

        assert result is not None
        assert result.team_id == str(team_id)
        assert result.total_budget_usd == Decimal("500.00")

    async def test_allocation_exceeds_team_budget(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        data = AllocateBudgetRequest(allocations=[
            AllocateBudgetItem(user_id=str(uuid.uuid4()), allocated_usd=Decimal("600.00")),
            AllocateBudgetItem(user_id=str(uuid.uuid4()), allocated_usd=Decimal("600.00")),
        ])

        team_config = MagicMock(spec=BudgetConfig)
        team_config.max_budget_usd = Decimal("1000.00")

        with patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo:
            MockBudgetRepo.return_value.get_active_config = AsyncMock(return_value=team_config)

            with pytest.raises(ValidationError, match="exceeds team budget"):
                await budget_service.allocate_team_budget(
                    mock_session, team_id=team_id, data=data, actor=admin_user
                )

    async def test_allocation_requires_team_budget_first(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        data = AllocateBudgetRequest(allocations=[
            AllocateBudgetItem(user_id=str(uuid.uuid4()), allocated_usd=Decimal("100.00")),
        ])

        with patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo:
            MockBudgetRepo.return_value.get_active_config = AsyncMock(return_value=None)

            with pytest.raises(ValidationError, match="Team budget must be set"):
                await budget_service.allocate_team_budget(
                    mock_session, team_id=team_id, data=data, actor=admin_user
                )


class TestGetBudgetSummary:
    @pytest.mark.asyncio
    async def test_budget_summary_returns_user_and_team_rows(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        user_id = uuid.uuid4()

        team_cfg = MagicMock(spec=BudgetConfig)
        team_cfg.scope = BudgetScope.TEAM
        team_cfg.scope_id = team_id
        team_cfg.max_budget_usd = Decimal("1000")

        user_obj = MagicMock()
        user_obj.id = user_id
        user_obj.display_name = "Alice"
        user_obj.email = "a@b"
        team_obj = MagicMock()
        team_obj.id = team_id
        team_obj.name = "Eng"
        team_obj.department = None

        # _resolve_used falls through to session.execute when redis=None;
        # configure the awaited result so scalar_one() returns a Decimal-safe string
        execute_result = MagicMock()
        execute_result.scalar_one = MagicMock(return_value="0")
        mock_session.execute = AsyncMock(return_value=execute_result)

        with patch("app.services.budget_service.BudgetRepository") as BRepo, \
             patch("app.repositories.user_repository.UserRepository") as URepo:
            BRepo.return_value.list_configs = AsyncMock(return_value=[team_cfg])
            # ⚠️ iter_all_users 다 — 예전엔 list_users(limit=500) 이었고, 그건
            #    created_at desc 로 정렬한 뒤 앞에서 잘라서 500번째 이후 사용자의 예산
            #    행이 조용히 빠졌다(오류 없이 틀린 사용률). 전수 조회로 바뀌었다.
            URepo.return_value.iter_all_users = AsyncMock(return_value=[user_obj])
            URepo.return_value.list_all_teams = AsyncMock(return_value=[team_obj])

            result = await budget_service.get_budget_summary(
                mock_session, scope=None, target_id=None, period="2026-04",
                actor=admin_user,
            )

        target_types = sorted({i.target_type for i in result.summary})
        assert target_types == ["team", "user"]
        team_row = next(i for i in result.summary if i.target_type == "team")
        user_row = next(i for i in result.summary if i.target_type == "user")
        assert team_row.limit_usd == Decimal("1000")
        assert user_row.limit_usd is None  # 미설정 user → limit 없음

    @pytest.mark.asyncio
    async def test_budget_summary_requires_actor(
        self, budget_service: BudgetService, mock_session: AsyncMock
    ):
        """actor 는 필수다 — 기본값 None 이면 actor 를 빠뜨린 호출이 TEAM_LEADER 필터 없이
        전사 예산을 돌려준다(fail-open). 빠뜨리면 호출 시점에 TypeError 로 실패해야 한다."""
        with pytest.raises(TypeError):
            await budget_service.get_budget_summary(mock_session, period="2026-04")

    @pytest.mark.asyncio
    async def test_budget_summary_team_leader_sees_only_own_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        """TEAM_LEADER 는 소속 팀 행과 소속 팀 사용자 행만 받는다 — scope/target_id 를
        비워 전사 요약을 요청해도 타 팀 예산·사용액이 나오면 안 된다(IDOR)."""
        own_team_id = team_leader_user.team_id
        other_team_id = uuid.uuid4()

        def _team(tid: uuid.UUID, name: str) -> MagicMock:
            t = MagicMock()
            t.id = tid
            t.name = name
            t.department = None
            t.members = []
            return t

        def _user(team_id: uuid.UUID, name: str) -> MagicMock:
            u = MagicMock()
            u.id = uuid.uuid4()
            u.team_id = team_id
            u.display_name = name
            u.email = f"{name.lower()}@b"
            u.is_active = True
            return u

        own_user = _user(own_team_id, "Own")
        other_user = _user(other_team_id, "Other")

        execute_result = MagicMock()
        execute_result.scalar_one = MagicMock(return_value="0")
        mock_session.execute = AsyncMock(return_value=execute_result)

        with patch("app.services.budget_service.BudgetRepository") as BRepo, \
             patch("app.repositories.user_repository.UserRepository") as URepo:
            BRepo.return_value.list_configs = AsyncMock(return_value=[])
            URepo.return_value.iter_all_users = AsyncMock(return_value=[own_user, other_user])
            URepo.return_value.list_all_teams = AsyncMock(
                return_value=[_team(own_team_id, "Mine"), _team(other_team_id, "Theirs")]
            )

            result = await budget_service.get_budget_summary(
                mock_session, scope=None, target_id=None, period="2026-04",
                actor=team_leader_user,
            )

        team_ids = {i.target_id for i in result.summary if i.target_type == "team"}
        user_ids = {i.target_id for i in result.summary if i.target_type == "user"}
        assert team_ids == {str(own_team_id)}
        assert user_ids == {str(own_user.id)}

    @pytest.mark.asyncio
    async def test_budget_summary_cluster_uses_mget_nonatomic(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """prod 는 RedisCluster — 카운터 키의 해시태그 {sid} 가 대상마다 달라
        multi-key mget 은 CROSSSLOT 으로 실패하므로 클러스터 클라이언트에서는
        mget_nonatomic(fan-out)으로 읽어야 한다. standalone 경로는 mget 을 유지."""
        from redis.asyncio.cluster import RedisCluster

        user_id = uuid.uuid4()
        user_cfg = MagicMock(spec=BudgetConfig)
        user_cfg.scope = BudgetScope.USER
        user_cfg.scope_id = user_id
        user_cfg.max_budget_usd = Decimal("100")
        user_cfg.max_requests = None
        user_cfg.enabled = True

        user_obj = MagicMock()
        user_obj.id = user_id
        user_obj.display_name = "Alice"
        user_obj.email = "a@b"

        cluster = MagicMock(spec=RedisCluster)
        cluster.mget = AsyncMock(side_effect=AssertionError("mget called on cluster"))
        cluster.mget_nonatomic = AsyncMock(return_value=[b"7.50"])

        execute_result = MagicMock()
        execute_result.scalar_one = MagicMock(return_value="0")
        mock_session.execute = AsyncMock(return_value=execute_result)

        with patch("app.services.budget_service.BudgetRepository") as BRepo, \
             patch("app.repositories.user_repository.UserRepository") as URepo:
            BRepo.return_value.list_configs = AsyncMock(return_value=[user_cfg])
            URepo.return_value.iter_all_users = AsyncMock(return_value=[user_obj])
            URepo.return_value.list_all_teams = AsyncMock(return_value=[])

            result = await budget_service.get_budget_summary(
                mock_session, scope=None, target_id=None, period="2026-04",
                actor=admin_user, redis=cluster,
            )

        cluster.mget_nonatomic.assert_awaited_once()
        cluster.mget.assert_not_called()
        row = next(i for i in result.summary if i.target_id == str(user_id))
        assert row.used_usd == Decimal("7.50")

    @pytest.mark.asyncio
    async def test_budget_summary_standalone_still_uses_mget(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """standalone Redis(및 기존 테스트의 평범한 Mock)은 한 번의 mget 유지 —
        클러스터 분기가 회귀로 퍼지지 않는지 가드."""
        user_id = uuid.uuid4()
        user_cfg = MagicMock(spec=BudgetConfig)
        user_cfg.scope = BudgetScope.USER
        user_cfg.scope_id = user_id
        user_cfg.max_budget_usd = Decimal("100")
        user_cfg.max_requests = None
        user_cfg.enabled = True

        user_obj = MagicMock()
        user_obj.id = user_id
        user_obj.display_name = "Bob"
        user_obj.email = "b@c"

        standalone = MagicMock()  # spec 없음 → isinstance(RedisCluster) False
        standalone.mget = AsyncMock(return_value=["2.25"])

        execute_result = MagicMock()
        execute_result.scalar_one = MagicMock(return_value="0")
        mock_session.execute = AsyncMock(return_value=execute_result)

        with patch("app.services.budget_service.BudgetRepository") as BRepo, \
             patch("app.repositories.user_repository.UserRepository") as URepo:
            BRepo.return_value.list_configs = AsyncMock(return_value=[user_cfg])
            URepo.return_value.iter_all_users = AsyncMock(return_value=[user_obj])
            URepo.return_value.list_all_teams = AsyncMock(return_value=[])

            result = await budget_service.get_budget_summary(
                mock_session, scope=None, target_id=None, period="2026-04",
                actor=admin_user, redis=standalone,
            )

        standalone.mget.assert_awaited_once()
        row = next(i for i in result.summary if i.target_id == str(user_id))
        assert row.used_usd == Decimal("2.25")


@pytest.mark.asyncio
async def test_sync_redis_thresholds_sets_5min_ttl(budget_service):
    """USER 예산 설정 캐시는 5분 TTL 이어야 한다(Z 정책).

    ⚠️ USER 경로는 이제 `redis.set` 이 아니라 **Lua(`redis.eval`)** 로 쓴다 —
       app_clients 를 애플리케이션에서 GET-modify-SET 하면 로그인/동시 요청이 서로의
       필드를 지웠기 때문이다(core/budget_cache.py 참조). TTL 은 스크립트의 ARGV[2] 로
       넘어가므로 그 인자를 검사한다. 계약은 그대로다: 5분.
    """
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock()
    fake_redis.eval = AsyncMock(return_value=1)
    budget_service._cache_mgr._redis = fake_redis

    data = SetBudgetRequest(
        max_budget_usd=Decimal("100"),
        policy=BudgetPolicy.HARD_BLOCK,
    )
    await budget_service._sync_redis_thresholds(
        "user", uuid.UUID("00000000-0000-4000-a000-000000000001"), data
    )

    fake_redis.eval.assert_awaited_once()
    args = fake_redis.eval.await_args.args
    # eval(script, numkeys, key, payload_json, ttl_str)
    assert args[1] == 1, "단일 키 스크립트여야 한다(클러스터 슬롯 안전)"
    assert str(BUDGET_CONFIG_CACHE_TTL) in args[4], (
        f"USER budget config cache 는 5분 TTL 이어야 함 (Z 정책). 받은 인자: {args[4]!r}"
    )
    # ⚠️ 이 경로에서 redis.set 을 쓰면 안 된다 — 그게 클로버의 형태다.
    fake_redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_warm_team_budget_cache_writes_all_active_team_configs(
    budget_service: BudgetService, mock_session: AsyncMock
):
    """startup warmup: 활성 TEAM BudgetConfig 전체를 Redis에 캐싱."""
    team_id_1 = uuid.uuid4()
    team_id_2 = uuid.uuid4()

    cfg1 = MagicMock(spec=BudgetConfig)
    cfg1.scope = BudgetScope.TEAM
    cfg1.scope_id = team_id_1
    cfg1.max_budget_usd = Decimal("5000")
    cfg1.policy = BudgetPolicy.HARD_BLOCK

    cfg2 = MagicMock(spec=BudgetConfig)
    cfg2.scope = BudgetScope.TEAM
    cfg2.scope_id = team_id_2
    cfg2.max_budget_usd = Decimal("1000")
    cfg2.policy = BudgetPolicy.HARD_BLOCK

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock()
    budget_service._cache_mgr._redis = fake_redis

    with patch("app.services.budget_service.BudgetRepository") as BRepo:
        BRepo.return_value.list_configs = AsyncMock(return_value=[cfg1, cfg2])
        count = await budget_service.warm_team_budget_cache(mock_session)

    assert count == 2
    assert fake_redis.set.call_count == 2

    # Redis Cluster hash-tag braces 포함 여부 확인
    keys_called = [c.args[0] for c in fake_redis.set.call_args_list]
    assert f"budget:config:team:{{{team_id_1}}}" in keys_called
    assert f"budget:config:team:{{{team_id_2}}}" in keys_called

    # EX TTL = BUDGET_CONFIG_CACHE_TTL (300s) 확인
    for c in fake_redis.set.call_args_list:
        assert c.kwargs.get("ex") == BUDGET_CONFIG_CACHE_TTL, (
            f"warm_team_budget_cache 는 {BUDGET_CONFIG_CACHE_TTL}초 TTL 이어야 함"
        )


@pytest.mark.asyncio
async def test_warm_team_budget_cache_empty_returns_zero(
    budget_service: BudgetService, mock_session: AsyncMock
):
    """활성 TEAM BudgetConfig 없으면 0 반환, Redis SET 없음."""
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock()
    budget_service._cache_mgr._redis = fake_redis

    with patch("app.services.budget_service.BudgetRepository") as BRepo:
        BRepo.return_value.list_configs = AsyncMock(return_value=[])
        count = await budget_service.warm_team_budget_cache(mock_session)

    assert count == 0
    fake_redis.set.assert_not_called()


class TestGetBudgetSummaryUsage:
    """예산 미설정 대상의 사용액 집계($0.00 버그 수정)와 Redis MGET 경로."""

    async def test_no_config_target_gets_real_usage(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """BudgetConfig 없는 사용자도 usage_logs 실사용액이 표시된다 — 이전엔
        cfg 없음 → used=0 하드코딩으로 사용액이 있어도 $0.00 으로 나왔다."""
        user_id = uuid.uuid4()

        user_obj = MagicMock()
        user_obj.id = user_id
        user_obj.display_name = "Alice"
        user_obj.email = "a@b"
        user_obj.is_active = True
        user_obj.team_id = None

        # usage GROUP BY 결과 — 사용자 행만, 팀 행은 없음
        usage_rows = MagicMock()
        usage_rows.all = MagicMock(return_value=[(user_id, "12.50")])

        with patch("app.services.budget_service.BudgetRepository") as BRepo, \
             patch("app.repositories.user_repository.UserRepository") as URepo:
            BRepo.return_value.list_configs = AsyncMock(return_value=[])
            URepo.return_value.iter_all_users = AsyncMock(return_value=[user_obj])
            URepo.return_value.list_all_teams = AsyncMock(return_value=[])
            mock_session.execute = AsyncMock(return_value=usage_rows)

            result = await budget_service.get_budget_summary(
                mock_session, scope="user", target_id=None, period="2026-04",
                actor=admin_user, redis=None,
            )

        row = next(i for i in result.summary if i.target_id == str(user_id))
        assert row.used_usd == Decimal("12.50")
        assert row.limit_usd is None  # 미설정 — 하지만 사용액은 실값

    async def test_redis_mget_single_call_beats_per_target_get(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """대상마다 개별 GET 이 아니라 MGET 한 번으로 enforcement 카운터를 읽는다."""
        user_id = uuid.uuid4()

        user_obj = MagicMock()
        user_obj.id = user_id
        user_obj.display_name = "Alice"
        user_obj.email = "a@b"
        user_obj.is_active = True
        user_obj.team_id = None

        redis = MagicMock()
        redis.mget = AsyncMock(return_value=[b"7.25"])
        redis.get = AsyncMock()

        # Redis 가 전부 커버 → SQL 미스분 없음(execute 는 호출돼도 빈 집계 경로)
        empty_rows = MagicMock()
        empty_rows.all = MagicMock(return_value=[])

        with patch("app.services.budget_service.BudgetRepository") as BRepo, \
             patch("app.repositories.user_repository.UserRepository") as URepo:
            BRepo.return_value.list_configs = AsyncMock(return_value=[])
            URepo.return_value.iter_all_users = AsyncMock(return_value=[user_obj])
            URepo.return_value.list_all_teams = AsyncMock(return_value=[])
            mock_session.execute = AsyncMock(return_value=empty_rows)

            result = await budget_service.get_budget_summary(
                mock_session, scope="user", target_id=None, period="2026-04",
                actor=admin_user, redis=redis,
            )

        redis.mget.assert_awaited_once()
        redis.get.assert_not_called()
        row = next(i for i in result.summary if i.target_id == str(user_id))
        assert row.used_usd == Decimal("7.25")
