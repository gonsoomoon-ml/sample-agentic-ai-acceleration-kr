# Copyright 2026 © Amazon.com and Affiliates.
"""EffectivePolicy — 사용자에게 적용되는 정책 합성 뷰.

판정 코어는 순수 함수 compute_cells 로 분리돼 있다. 게이트웨이의
check_client_scope / check_client_model_scope 와 같은 의미를 따르는지가
검증 대상이다:

  * user_app  축: 빈 목록/None = 전체 허용 (fail-open)
  * model_app 축: None = 전체 허용, [] = 전면 거부 (fail-closed)
  * user_model 축: None = 전체 허용, 목록 = 화이트리스트
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.auth import User
from app.services.effective_policy_service import (
    EffectivePolicyService,
    compute_cells,
)

CLIENTS = ["claude-code", "codex", "cowork"]  # sorted(VALID_CLIENTS)


def _cell(cells, client, alias):
    return next(c for c in cells if c.client == client and c.model_alias == alias)


class TestComputeCells:
    MODELS = [("opus-5", None), ("sonnet-5", ["cowork"]), ("haiku-4.5", [])]

    def test_all_unrestricted_allows_everything(self):
        cells = compute_cells(None, None, [("opus-5", None), ("sonnet-5", None)])
        assert all(c.allowed for c in cells)
        assert len(cells) == len(CLIENTS) * 2

    def test_user_app_axis_blocks_unlisted_clients(self):
        cells = compute_cells(["cowork"], None, self.MODELS)
        assert _cell(cells, "cowork", "opus-5").allowed
        assert _cell(cells, "codex", "opus-5").blocked_by == ["user_app"]
        assert _cell(cells, "claude-code", "opus-5").blocked_by == ["user_app"]

    def test_model_app_axis_empty_list_denies_everywhere(self):
        # model_aliases.allowed_clients = [] → 명시적 전면 거부 (사용자 축과 반대 의미)
        cells = compute_cells(None, None, self.MODELS)
        for client in CLIENTS:
            assert _cell(cells, client, "haiku-4.5").blocked_by == ["model_app"]

    def test_model_app_axis_list_restricts_to_listed(self):
        cells = compute_cells(None, None, self.MODELS)
        assert _cell(cells, "cowork", "sonnet-5").allowed
        assert _cell(cells, "codex", "sonnet-5").blocked_by == ["model_app"]

    def test_user_model_axis_whitelists(self):
        cells = compute_cells(None, ["opus-5"], self.MODELS)
        assert _cell(cells, "cowork", "opus-5").allowed
        assert _cell(cells, "cowork", "sonnet-5").blocked_by == ["user_model"]

    def test_multiple_axes_can_block_same_cell(self):
        # cowork 미허용 사용자 × cowork 전용 모델 × 모델 목록 밖 모델
        cells = compute_cells(["codex"], ["opus-5"], self.MODELS)
        cell = _cell(cells, "cowork", "haiku-4.5")
        assert not cell.allowed
        assert set(cell.blocked_by) == {"user_app", "user_model", "model_app"}


def _exec_result(*, scalars=None, scalar=None, rows=None):
    """session.execute 반환값 모사 — scalars()/scalar_one_or_none()/all() 3종."""
    r = MagicMock()
    r.scalars.return_value = scalars if scalars is not None else []
    r.scalar_one_or_none.return_value = scalar
    r.all.return_value = rows if rows is not None else []
    return r


class TestGetForUser:
    """서비스 조립 — session.execute 호출 순서대로 canned 결과를 돌려준다.

    순서: User → Team → user_allowed_clients → user_allowed_models →
    (user 행 없을 때만) team_allowed_models → ModelAlias → BudgetConfig →
    BudgetUsage → RateLimitConfig → DowngradePolicy → RoutingProfile.
    """

    async def test_user_not_found_raises(self):
        session = MagicMock()
        session.execute = AsyncMock(return_value=_exec_result(scalar=None))
        svc = EffectivePolicyService(session)
        from app.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await svc.get_for_user(uuid.uuid4())

    async def test_composes_all_axes(self):
        user = MagicMock(spec=User)
        user.id = uuid.uuid4()
        user.email = "dev@example.com"
        user.team_id = None

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                _exec_result(scalar=user),                    # User
                _exec_result(scalars=["cowork"]),             # user_allowed_clients
                _exec_result(scalars=[]),                     # user_allowed_models
                _exec_result(rows=[("opus-5", None)]),        # ModelAlias
                _exec_result(scalars=[]),                     # BudgetConfig
                _exec_result(scalars=[]),                     # BudgetUsage
                _exec_result(scalars=[]),                     # RateLimitConfig
                _exec_result(scalars=[]),                     # DowngradePolicy
                _exec_result(scalars=[]),                     # RoutingProfile
            ]
        )
        res = await EffectivePolicyService(session).get_for_user(user.id)

        assert res.email == "dev@example.com"
        assert res.allowed_clients == ["cowork"]
        assert res.allowed_models_source == "none"
        assert _cell(res.cells, "cowork", "opus-5").allowed
        assert _cell(res.cells, "codex", "opus-5").blocked_by == ["user_app"]


def _user_with_team(*, dept_id=None):
    """team/dept 체인이 있는 사용자·팀 모사."""
    from app.models.auth import Team

    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.email = "dev@example.com"
    user.team_id = uuid.uuid4()

    team = MagicMock(spec=Team)
    team.id = user.team_id
    team.name = "T"
    team.dept_id = dept_id if dept_id is not None else uuid.uuid4()
    return user, team


class TestAllowedClientsFallback:
    """축 1 폴백: user > team > organization > none (alembic 0038).

    execute 호출 순서(팀 소속 사용자, 개인/팀/조직 정책 모두 조회하는 최대 경로):
      User → Team → user_allowed_clients → team_allowed_clients →
      Department.org_id → org_allowed_clients → user_allowed_models →
      team_allowed_models → ModelAlias → BudgetConfig → BudgetUsage →
      RateLimitConfig → DowngradePolicy → RoutingProfile
    """

    def _tail(self):
        return [
            _exec_result(scalars=[]),          # user_allowed_models
            _exec_result(scalars=[]),          # team_allowed_models
            _exec_result(rows=[]),             # ModelAlias
            _exec_result(scalars=[]),          # BudgetConfig
            _exec_result(scalars=[]),          # BudgetUsage
            _exec_result(scalars=[]),          # RateLimitConfig
            _exec_result(scalars=[]),          # DowngradePolicy
            _exec_result(scalars=[]),          # RoutingProfile
        ]

    async def test_user_policy_wins(self):
        user, team = _user_with_team()
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                _exec_result(scalar=user),
                _exec_result(scalar=team),
                _exec_result(scalars=["cowork"]),  # user rows → 바로 확정
                *self._tail(),
            ]
        )
        res = await EffectivePolicyService(session).get_for_user(user.id)
        assert res.allowed_clients == ["cowork"]
        assert res.allowed_clients_source == "user"
        # team/org 조회 자체를 생략했다 — execute 3 + tail 8 = 11회
        assert session.execute.await_count == 11

    async def test_team_policy_applies_when_no_user_rows(self):
        user, team = _user_with_team()
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                _exec_result(scalar=user),
                _exec_result(scalar=team),
                _exec_result(scalars=[]),                     # user rows 없음
                _exec_result(scalars=["claude-code"]),        # team rows
                *self._tail(),
            ]
        )
        res = await EffectivePolicyService(session).get_for_user(user.id)
        assert res.allowed_clients == ["claude-code"]
        assert res.allowed_clients_source == "team"

    async def test_org_policy_applies_when_no_user_or_team_rows(self):
        user, team = _user_with_team()
        org_id = uuid.uuid4()
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                _exec_result(scalar=user),
                _exec_result(scalar=team),
                _exec_result(scalars=[]),               # user
                _exec_result(scalars=[]),               # team
                _exec_result(scalar=org_id),            # dept → org_id
                _exec_result(scalars=["codex"]),        # org rows
                *self._tail(),
            ]
        )
        res = await EffectivePolicyService(session).get_for_user(user.id)
        assert res.allowed_clients == ["codex"]
        assert res.allowed_clients_source == "organization"

    async def test_no_policy_anywhere_is_unrestricted(self):
        user, team = _user_with_team()
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                _exec_result(scalar=user),
                _exec_result(scalar=team),
                _exec_result(scalars=[]),          # user
                _exec_result(scalars=[]),          # team
                _exec_result(scalar=uuid.uuid4()), # dept → org_id
                _exec_result(scalars=[]),          # org
                *self._tail(),
            ]
        )
        res = await EffectivePolicyService(session).get_for_user(user.id)
        assert res.allowed_clients is None
        assert res.allowed_clients_source == "none"

    async def test_teamless_user_skips_team_and_org(self):
        # team_id 가 없으면 org 정책도 타지 않는다 (user→team→dept→org 체인 단절).
        user = MagicMock(spec=User)
        user.id = uuid.uuid4()
        user.email = "dev@example.com"
        user.team_id = None

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                _exec_result(scalar=user),
                _exec_result(scalars=[]),   # user rows 없음 — team/org 조회 생략
                _exec_result(scalars=[]),   # user_allowed_models
                _exec_result(rows=[]),      # ModelAlias (team 없어 team_models 생략)
                _exec_result(scalars=[]),   # BudgetConfig
                _exec_result(scalars=[]),   # BudgetUsage
                _exec_result(scalars=[]),   # RateLimitConfig
                _exec_result(scalars=[]),   # DowngradePolicy
                _exec_result(scalars=[]),   # RoutingProfile
            ]
        )
        res = await EffectivePolicyService(session).get_for_user(user.id)
        assert res.allowed_clients is None
        assert res.allowed_clients_source == "none"
