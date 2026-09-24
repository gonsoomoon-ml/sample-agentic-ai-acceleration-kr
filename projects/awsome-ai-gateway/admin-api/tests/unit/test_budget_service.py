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

        # 리더는 team_id 소속이 아니라 DB 의 leader_user_id 로 범위가 정해진다 —
        # 리더인 팀 집합에 other_team_id 가 없으면 거부.
        with patch("app.services.budget_service.UserRepository") as MockUserRepo, \
             patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value={uuid.uuid4()})):
            MockUserRepo.return_value.get_user = AsyncMock(return_value=user)

            with pytest.raises(ForbiddenError):
                await budget_service.set_user_budget(mock_session, user_id=user_id, data=data, actor=team_leader_user)

    async def test_user_budget_sum_exceeds_team_budget(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        """BR-BUD-01은 TEAM_LEADER 에만 적용 — 리더는 팀 풀을 초과해 배분 불가."""
        user_id = uuid.uuid4()
        team_id = uuid.uuid4()
        data = SetBudgetRequest(max_budget_usd=Decimal("600.00"))

        user = MagicMock(spec=User)
        user.team_id = team_id

        team_config = MagicMock(spec=BudgetConfig)
        team_config.max_budget_usd = Decimal("1000.00")

        with patch("app.services.budget_service.UserRepository") as MockUserRepo, \
             patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo, \
             patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value={team_id})):
            MockUserRepo.return_value.get_user = AsyncMock(return_value=user)
            repo = MockBudgetRepo.return_value
            repo.get_active_config = AsyncMock(side_effect=[team_config, None])
            repo.sum_member_budgets = AsyncMock(return_value=Decimal("500.00"))

            with pytest.raises(ValidationError, match="exceeds team budget"):
                await budget_service.set_user_budget(mock_session, user_id=user_id, data=data, actor=team_leader_user)

    async def test_admin_can_exceed_member_sum(
        self, budget_service: BudgetService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """ADMIN 은 BR-BUD-01을 우회한다 — 초과된 멤버의 한도 상향이 막히지 않아야 한다."""
        user_id = uuid.uuid4()
        team_id = uuid.uuid4()
        data = SetBudgetRequest(max_budget_usd=Decimal("600.00"))

        user = MagicMock(spec=User)
        user.team_id = team_id

        with patch("app.services.budget_service.UserRepository") as MockUserRepo, \
             patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo, \
             patch("app.services.budget_service.audit_logger") as mock_audit:
            MockUserRepo.return_value.get_user = AsyncMock(return_value=user)
            repo = MockBudgetRepo.return_value
            repo.upsert_config = AsyncMock()
            mock_audit.log = AsyncMock()

            await budget_service.set_user_budget(mock_session, user_id=user_id, data=data, actor=admin_user)

        repo.upsert_config.assert_called_once()
        # admin 우회 시 멤버 합계 조회 자체를 하지 않는다.
        repo.sum_member_budgets.assert_not_called()

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

        # 리더인 팀 집합(led)에 없는 팀이면 거부 — 소속과 무관.
        with patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value={uuid.uuid4()})):
            with pytest.raises(ForbiddenError):
                await budget_service.allocate_team_budget(
                    mock_session, team_id=other_team_id, data=data, actor=team_leader_user
                )

    async def test_team_leader_can_allocate_led_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        data = AllocateBudgetRequest(allocations=[
            AllocateBudgetItem(user_id=str(uuid.uuid4()), allocated_usd=Decimal("100.00")),
        ])

        team_config = MagicMock(spec=BudgetConfig)
        team_config.max_budget_usd = Decimal("1000.00")
        team_config.policy = BudgetPolicy.HARD_BLOCK

        with patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value={team_id})), \
             patch("app.services.budget_service.BudgetRepository") as MockBudgetRepo, \
             patch("app.services.budget_service.audit_logger") as mock_audit:
            repo = MockBudgetRepo.return_value
            repo.get_active_config = AsyncMock(return_value=team_config)
            repo.upsert_config = AsyncMock()
            mock_audit.log = AsyncMock()

            await budget_service.allocate_team_budget(
                mock_session, team_id=team_id, data=data, actor=team_leader_user
            )

        repo.upsert_config.assert_called_once()

    async def test_get_team_allocation_forbidden_for_non_led_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        """get_team_allocation 은 이전에 actor 검사가 없어 임의 team_id 로 타 팀
        배정을 읽을 수 있었다 — 리더인 팀 외에는 403."""
        with patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value={uuid.uuid4()})):
            with pytest.raises(ForbiddenError):
                await budget_service.get_team_allocation(
                    mock_session,
                    team_id=uuid.uuid4(),
                    period="2026-09",
                    actor=team_leader_user,
                )

    async def test_get_team_allocation_allowed_for_led_team(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        team_id = uuid.uuid4()
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

        with patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value={team_id})), \
             patch("app.services.budget_service.UserRepository") as MockUserRepo, \
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

    async def test_get_my_allocations_returns_only_led_teams(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        led_a, led_b, not_led = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        with patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value={led_a, led_b})), \
             patch.object(budget_service, "get_team_allocation",
                          new=AsyncMock(side_effect=[MagicMock(), MagicMock()])) as mock_get:
            result = await budget_service.get_my_allocations(
                mock_session, actor=team_leader_user, period="2026-09"
            )

        assert len(result) == 2
        called_ids = {c.kwargs["team_id"] for c in mock_get.await_args_list}
        assert called_ids == {led_a, led_b}
        assert not_led not in called_ids

    async def test_get_my_allocations_leader_with_no_led_teams(
        self, budget_service: BudgetService, mock_session: AsyncMock, team_leader_user: CurrentUser
    ):
        with patch("app.services.budget_service.led_team_ids",
                   new=AsyncMock(return_value=set())):
            result = await budget_service.get_my_allocations(
                mock_session, actor=team_leader_user, period="2026-09"
            )
        assert result == []

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
        self, budget_service: BudgetService, mock_session: AsyncMock
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
                mock_session, scope=None, target_id=None, period="2026-04"
            )

        target_types = sorted({i.target_type for i in result.summary})
        assert target_types == ["team", "user"]
        team_row = next(i for i in result.summary if i.target_type == "team")
        user_row = next(i for i in result.summary if i.target_type == "user")
        assert team_row.limit_usd == Decimal("1000")
        assert user_row.limit_usd is None  # 미설정 user → limit 없음


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
