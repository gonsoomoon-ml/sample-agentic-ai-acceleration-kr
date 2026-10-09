# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import Department, Team, User, UserRole
from app.services.key_service import KeyService
from app.services.user_team_service import UserTeamService


@pytest.fixture
def user_team_service() -> UserTeamService:
    cache_mgr = MagicMock(spec=CacheInvalidationManager)
    cache_mgr._redis = MagicMock()
    key_service = MagicMock(spec=KeyService)
    return UserTeamService(cache_mgr=cache_mgr, key_service=key_service)


def _team(team_id: uuid.UUID, *, members: list | None = None, leader_user_id=None) -> MagicMock:
    team = MagicMock(spec=Team)
    team.id = team_id
    team.name = "Test Team"
    team.dept_id = uuid.uuid4()
    team.leader_user_id = leader_user_id
    team.members = members or []
    team.created_at = MagicMock()
    return team


def _user(
    user_id: uuid.UUID, team_id: uuid.UUID | None, role: UserRole = UserRole.DEVELOPER
) -> MagicMock:
    user = MagicMock(spec=User)
    user.id = user_id
    user.team_id = team_id
    user.role = role
    user.email = "dev@test.com"
    user.display_name = "Dev"
    user.is_active = True
    user.created_at = MagicMock()
    user.team = None
    return user


class TestSetTeamLeader:
    async def test_set_team_leader_updates_role(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        user_id = uuid.uuid4()

        team = _team(team_id)
        user = _user(user_id, team_id)

        with patch("app.services.user_team_service.UserRepository") as MockRepo, \
             patch("app.services.user_team_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.get_team = AsyncMock(return_value=team)
            repo.get_user = AsyncMock(return_value=user)
            repo.update_user_role = AsyncMock()
            mock_audit.log = AsyncMock()

            result = await user_team_service.set_team_leader(
                mock_session, team_id=team_id, user_id=user_id, actor=admin_user
            )

        repo.update_user_role.assert_called_once_with(user_id, UserRole.TEAM_LEADER)
        assert team.leader_user_id == user_id
        assert result.leader_user_id == str(user_id)

    async def test_set_team_leader_not_found(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        with patch("app.services.user_team_service.UserRepository") as MockRepo:
            MockRepo.return_value.get_team = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await user_team_service.set_team_leader(
                    mock_session, team_id=uuid.uuid4(), user_id=uuid.uuid4(), actor=admin_user
                )

    async def test_set_team_leader_rejects_other_team_user(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """다른 팀 소속 사용자는 리더로 지정할 수 없다 — 이관 후 지정해야."""
        team_id = uuid.uuid4()
        with patch("app.services.user_team_service.UserRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_team = AsyncMock(return_value=_team(team_id))
            repo.get_user = AsyncMock(return_value=_user(uuid.uuid4(), uuid.uuid4()))
            repo.update_user_role = AsyncMock()

            with pytest.raises(ValidationError):
                await user_team_service.set_team_leader(
                    mock_session, team_id=team_id, user_id=uuid.uuid4(), actor=admin_user
                )
        repo.update_user_role.assert_not_called()

    async def test_set_team_leader_rejects_admin(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """ADMIN 을 팀 리더로 지정하면 다음 로그인 때 ADMIN_GROUPS 로 role 이 되돌아
        꼬이므로 지정 자체를 거부한다."""
        team_id = uuid.uuid4()
        with patch("app.services.user_team_service.UserRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_team = AsyncMock(return_value=_team(team_id))
            repo.get_user = AsyncMock(
                return_value=_user(uuid.uuid4(), team_id, role=UserRole.ADMIN)
            )
            repo.update_user_role = AsyncMock()

            with pytest.raises(ValidationError):
                await user_team_service.set_team_leader(
                    mock_session, team_id=team_id, user_id=uuid.uuid4(), actor=admin_user
                )
        repo.update_user_role.assert_not_called()


class TestUnsetTeamLeader:
    async def test_unset_team_leader_demotes_role(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        user_id = uuid.uuid4()

        target = _user(user_id, team_id, role=UserRole.TEAM_LEADER)
        team = _team(team_id, members=[target], leader_user_id=user_id)

        with patch("app.services.user_team_service.UserRepository") as MockRepo, \
             patch("app.services.user_team_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.get_team = AsyncMock(return_value=team)
            repo.get_user = AsyncMock(return_value=target)
            repo.update_user_role = AsyncMock()
            mock_audit.log = AsyncMock()

            result = await user_team_service.unset_team_leader(
                mock_session, team_id=team_id, user_id=user_id, actor=admin_user
            )

        repo.update_user_role.assert_called_once_with(user_id, UserRole.DEVELOPER)
        # 남은 리더가 없으면 표시 포인터도 비운다
        assert team.leader_user_id is None
        assert result.leader_user_id is None

    async def test_unset_team_leader_moves_pointer_to_remaining_leader(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """다중 리더: 해제한 사람을 가리키던 leader_user_id 는 남은 리더로 옮긴다."""
        team_id = uuid.uuid4()
        removed_id = uuid.uuid4()
        remaining_id = uuid.uuid4()

        removed = _user(removed_id, team_id, role=UserRole.TEAM_LEADER)
        remaining = _user(remaining_id, team_id, role=UserRole.TEAM_LEADER)
        team = _team(team_id, members=[removed, remaining], leader_user_id=removed_id)

        with patch("app.services.user_team_service.UserRepository") as MockRepo, \
             patch("app.services.user_team_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.get_team = AsyncMock(return_value=team)
            repo.get_user = AsyncMock(return_value=removed)
            repo.update_user_role = AsyncMock()
            mock_audit.log = AsyncMock()

            await user_team_service.unset_team_leader(
                mock_session, team_id=team_id, user_id=removed_id, actor=admin_user
            )

        assert team.leader_user_id == remaining_id

    async def test_unset_team_leader_rejects_non_leader(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        with patch("app.services.user_team_service.UserRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_team = AsyncMock(return_value=_team(team_id))
            repo.get_user = AsyncMock(
                return_value=_user(uuid.uuid4(), team_id, role=UserRole.DEVELOPER)
            )

            with pytest.raises(ValidationError):
                await user_team_service.unset_team_leader(
                    mock_session, team_id=team_id, user_id=uuid.uuid4(), actor=admin_user
                )

    async def test_unset_team_leader_rejects_other_team_user(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        with patch("app.services.user_team_service.UserRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_team = AsyncMock(return_value=_team(team_id))
            repo.get_user = AsyncMock(
                return_value=_user(uuid.uuid4(), uuid.uuid4(), role=UserRole.TEAM_LEADER)
            )

            with pytest.raises(NotFoundError):
                await user_team_service.unset_team_leader(
                    mock_session, team_id=team_id, user_id=uuid.uuid4(), actor=admin_user
                )


class TestTransferUser:
    async def test_transfer_deactivates_budget_and_invalidates_cache(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        user_id = uuid.uuid4()
        old_team_id = uuid.uuid4()
        new_team_id = uuid.uuid4()

        user = MagicMock(spec=User)
        user.id = user_id
        user.team_id = old_team_id
        user.email = "dev@test.com"
        user.display_name = "Dev"
        user.role = UserRole.DEVELOPER
        user.is_active = True
        user.created_at = MagicMock()
        user.team = None

        transferred_user = MagicMock(spec=User)
        transferred_user.id = user_id
        transferred_user.team_id = new_team_id
        transferred_user.email = "dev@test.com"
        transferred_user.display_name = "Dev"
        transferred_user.role = UserRole.DEVELOPER
        transferred_user.is_active = True
        transferred_user.created_at = MagicMock()
        transferred_user.team = None

        with patch("app.services.user_team_service.UserRepository") as MockUserRepo, \
             patch("app.services.user_team_service.BudgetRepository") as MockBudgetRepo, \
             patch("app.services.user_team_service.RateLimitConfigRepository") as MockRLRepo, \
             patch("app.services.user_team_service.audit_logger") as mock_audit:
            user_repo = MockUserRepo.return_value
            user_repo.get_user = AsyncMock(return_value=user)
            user_repo.update_user_team = AsyncMock(return_value=transferred_user)
            MockBudgetRepo.return_value.deactivate_configs = AsyncMock()
            MockRLRepo.return_value.deactivate_configs = AsyncMock()
            mock_audit.log = AsyncMock()

            # Stub KeyService: no VK hashes (minimal case)
            user_team_service._key_service.list_active_vk_hashes_for_user = AsyncMock(
                return_value=[]
            )
            cache_mgr = user_team_service._cache_mgr
            cache_mgr.invalidate = AsyncMock()
            cache_mgr.swap_reverse_index_membership = AsyncMock()

            result = await user_team_service.transfer_user(
                mock_session, user_id=user_id, new_team_id=new_team_id, actor=admin_user
            )

        # Budget deactivated
        MockBudgetRepo.return_value.deactivate_configs.assert_awaited_once()
        # Cache invalidation called
        cache_mgr.invalidate.assert_awaited_once()
        assert result.team_id == str(new_team_id)

    async def test_transfer_user_not_found(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        with patch("app.services.user_team_service.UserRepository") as MockRepo:
            MockRepo.return_value.get_user = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await user_team_service.transfer_user(
                    mock_session, user_id=uuid.uuid4(), new_team_id=uuid.uuid4(), actor=admin_user
                )

    async def test_transfer_user_demotes_team_leader(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """TEAM_LEADER 를 다른 팀으로 이관하면 DEVELOPER 로 강등하고, 옛 팀을 가리키던
        leader_user_id 는 남은 리더로 옮긴다(없으면 비움). 리더십은 팀별 속성이므로
        이관되지 않는다 — 안 그러면 새 팀에서 지정 없이 리더 권한을 얻는다."""
        user_id = uuid.uuid4()
        old_team_id = uuid.uuid4()
        new_team_id = uuid.uuid4()
        remaining_id = uuid.uuid4()

        user = _user(user_id, old_team_id, role=UserRole.TEAM_LEADER)
        remaining = _user(remaining_id, old_team_id, role=UserRole.TEAM_LEADER)
        old_team = _team(old_team_id, members=[user, remaining], leader_user_id=user_id)
        transferred = _user(user_id, new_team_id, role=UserRole.DEVELOPER)

        with patch("app.services.user_team_service.UserRepository") as URepo, \
             patch("app.services.user_team_service.BudgetRepository") as BRepo, \
             patch("app.services.user_team_service.RateLimitConfigRepository") as RLRepo, \
             patch("app.services.user_team_service.audit_logger") as mock_audit:
            URepo.return_value.get_user = AsyncMock(return_value=user)
            URepo.return_value.get_team = AsyncMock(return_value=old_team)
            URepo.return_value.update_user_team = AsyncMock(return_value=transferred)
            URepo.return_value.update_user_role = AsyncMock()
            BRepo.return_value.deactivate_configs = AsyncMock()
            RLRepo.return_value.deactivate_configs = AsyncMock()
            mock_audit.log = AsyncMock()

            user_team_service._key_service.list_active_vk_hashes_for_user = AsyncMock(
                return_value=[]
            )
            user_team_service._cache_mgr.invalidate = AsyncMock()

            await user_team_service.transfer_user(
                mock_session, user_id=user_id, new_team_id=new_team_id, actor=admin_user
            )

        URepo.return_value.update_user_role.assert_called_once_with(user_id, UserRole.DEVELOPER)
        assert old_team.leader_user_id == remaining_id

    async def test_transfer_user_same_team_keeps_leader(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """같은 팀으로의 no-op 이관은 리더를 강등시키지 않는다."""
        user_id = uuid.uuid4()
        team_id = uuid.uuid4()

        user = _user(user_id, team_id, role=UserRole.TEAM_LEADER)
        transferred = _user(user_id, team_id, role=UserRole.TEAM_LEADER)

        with patch("app.services.user_team_service.UserRepository") as URepo, \
             patch("app.services.user_team_service.BudgetRepository") as BRepo, \
             patch("app.services.user_team_service.RateLimitConfigRepository") as RLRepo, \
             patch("app.services.user_team_service.audit_logger") as mock_audit:
            URepo.return_value.get_user = AsyncMock(return_value=user)
            URepo.return_value.update_user_team = AsyncMock(return_value=transferred)
            URepo.return_value.update_user_role = AsyncMock()
            URepo.return_value.get_team = AsyncMock()
            BRepo.return_value.deactivate_configs = AsyncMock()
            RLRepo.return_value.deactivate_configs = AsyncMock()
            mock_audit.log = AsyncMock()

            user_team_service._key_service.list_active_vk_hashes_for_user = AsyncMock(
                return_value=[]
            )
            user_team_service._cache_mgr.invalidate = AsyncMock()

            await user_team_service.transfer_user(
                mock_session, user_id=user_id, new_team_id=team_id, actor=admin_user
            )

        URepo.return_value.update_user_role.assert_not_called()
        URepo.return_value.get_team.assert_not_called()

    @pytest.mark.asyncio
    async def test_transfer_user_invalidates_caches_and_swaps_reverse_index(
        self, user_team_service: UserTeamService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        user_id = uuid.uuid4()
        old_team_id = uuid.uuid4()
        new_team_id = uuid.uuid4()

        user_mock = MagicMock(team_id=old_team_id, id=user_id)
        user_mock_after = MagicMock(
            team_id=new_team_id, id=user_id,
            email="x@y", display_name="X",
            role=UserRole.DEVELOPER,
            is_active=True,
            created_at=MagicMock(),
            team=None,
        )

        with patch("app.services.user_team_service.UserRepository") as URepo, \
             patch("app.services.user_team_service.BudgetRepository") as BRepo, \
             patch("app.services.user_team_service.RateLimitConfigRepository") as RLRepo, \
             patch("app.services.user_team_service.audit_logger") as mock_audit:

            URepo.return_value.get_user = AsyncMock(return_value=user_mock)
            URepo.return_value.update_user_team = AsyncMock(return_value=user_mock_after)
            BRepo.return_value.deactivate_configs = AsyncMock(return_value=1)
            RLRepo.return_value.deactivate_configs = AsyncMock(return_value=1)
            mock_audit.log = AsyncMock()

            # Stub KeyService method on the injected instance
            user_team_service._key_service.list_active_vk_hashes_for_user = AsyncMock(
                return_value=["h1", "h2"]
            )

            cache_mgr = user_team_service._cache_mgr
            cache_mgr.invalidate = AsyncMock()
            cache_mgr.swap_reverse_index_membership = AsyncMock()

            await user_team_service.transfer_user(
                mock_session, user_id=user_id,
                new_team_id=new_team_id, actor=admin_user,
            )

            # Verify USER scope deactivations
            BRepo.return_value.deactivate_configs.assert_awaited_once()
            RLRepo.return_value.deactivate_configs.assert_awaited_once()

            # Verify cache invalidate keys
            invalidated = set(cache_mgr.invalidate.call_args.args[0])
            assert f"user_context:{user_id}" in invalidated
            assert f"budget:config:user:{{{user_id}}}" in invalidated
            assert "key:cache:vk:h1" in invalidated
            assert "key:cache:vk:h2" in invalidated

            # Verify reverse index swap
            swap_kwargs = cache_mgr.swap_reverse_index_membership.call_args.kwargs
            assert swap_kwargs["old_key"] == f"team:vk_hashes:{old_team_id}"
            assert swap_kwargs["new_key"] == f"team:vk_hashes:{new_team_id}"
            assert swap_kwargs["members"] == ["h1", "h2"]


class TestListUsers:
    async def test_list_users_pagination(
        self, user_team_service: UserTeamService, mock_session: AsyncMock
    ):
        users = [MagicMock(spec=User) for _ in range(3)]
        for i, u in enumerate(users):
            u.id = uuid.uuid4()
            u.email = f"user{i}@test.com"
            u.display_name = f"User {i}"
            u.role = UserRole.DEVELOPER
            u.team_id = None
            u.is_active = True
            u.created_at = MagicMock()
            u.team = None

        with patch("app.services.user_team_service.UserRepository") as MockRepo:
            MockRepo.return_value.list_users = AsyncMock(return_value=users)

            result, has_more = await user_team_service.list_users(mock_session, limit=2)

        assert len(result) == 2
        assert has_more is True


class TestReleaseStaleLeaderPointer:
    """``leader_user_id`` 스테일 포인터 정리 헬퍼 — Cognito sync·OIDC 로그인 등
    ``transfer_user`` 를 거치지 않는 팀/역할 변경 경로가 쓴다."""

    def _session_with_teams(self, teams: list) -> AsyncMock:
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = teams
        session.execute = AsyncMock(return_value=result)
        # 헬퍼는 autoflush 실패 격리용 SAVEPOINT 로 감싼다 — MagicMock 이면
        # __aenter__/__aexit__ 가 AsyncMock 으로 자동 배선된다.
        session.begin_nested = MagicMock(return_value=MagicMock())
        return session

    async def test_clears_pointer_when_user_left_the_team(self):
        """팀 이동한 사용자를 가리키는 옛 팀 포인터는 남은 리더로 옮긴다."""
        from app.services.user_team_service import release_stale_leader_pointer

        team_id = uuid.uuid4()
        moved_id = uuid.uuid4()
        remaining_id = uuid.uuid4()

        moved = _user(moved_id, uuid.uuid4(), role=UserRole.DEVELOPER)  # 이미 다른 팀
        remaining = _user(remaining_id, team_id, role=UserRole.TEAM_LEADER)
        team = _team(team_id, members=[remaining], leader_user_id=moved_id)

        session = self._session_with_teams([team])
        fixed = await release_stale_leader_pointer(
            session, user_id=moved_id, team_id=moved.team_id, role=moved.role
        )

        assert fixed == 1
        assert team.leader_user_id == remaining_id

    async def test_keeps_valid_pointer(self):
        """같은 팀의 TEAM_LEADER 를 가리키는 유효한 포인터는 건드리지 않는다."""
        from app.services.user_team_service import release_stale_leader_pointer

        team_id = uuid.uuid4()
        leader_id = uuid.uuid4()
        leader = _user(leader_id, team_id, role=UserRole.TEAM_LEADER)
        team = _team(team_id, members=[leader], leader_user_id=leader_id)

        session = self._session_with_teams([team])
        fixed = await release_stale_leader_pointer(
            session, user_id=leader_id, team_id=leader.team_id, role=leader.role
        )

        assert fixed == 0
        assert team.leader_user_id == leader_id

    async def test_clears_pointer_when_user_deactivated(self):
        """같은 팀·role 도 TEAM_LEADER 여도 비활성화됐으면 포인터는 스테일이다."""
        from app.services.user_team_service import release_stale_leader_pointer

        team_id = uuid.uuid4()
        leader_id = uuid.uuid4()
        remaining_id = uuid.uuid4()
        leader = _user(leader_id, team_id, role=UserRole.TEAM_LEADER)
        remaining = _user(remaining_id, team_id, role=UserRole.TEAM_LEADER)
        team = _team(team_id, members=[leader, remaining], leader_user_id=leader_id)

        session = self._session_with_teams([team])
        fixed = await release_stale_leader_pointer(
            session, user_id=leader_id, team_id=team_id,
            role=UserRole.TEAM_LEADER, is_active=False,
        )

        assert fixed == 1
        assert team.leader_user_id == remaining_id

    async def test_repoint_skips_inactive_remaining_leader(self):
        """남은 리더가 비활성이면 포인터로 택하지 않는다 — 비활성→비활성 이월 방지."""
        from app.services.user_team_service import release_stale_leader_pointer

        team_id = uuid.uuid4()
        moved_id = uuid.uuid4()
        inactive_id = uuid.uuid4()
        moved = _user(moved_id, uuid.uuid4(), role=UserRole.DEVELOPER)
        inactive_leader = _user(inactive_id, team_id, role=UserRole.TEAM_LEADER)
        inactive_leader.is_active = False
        team = _team(team_id, members=[inactive_leader], leader_user_id=moved_id)

        session = self._session_with_teams([team])
        fixed = await release_stale_leader_pointer(
            session, user_id=moved_id, team_id=moved.team_id, role=moved.role
        )

        assert fixed == 1
        assert team.leader_user_id is None


class TestRepointInactiveLeaderPointers:
    """bulk 비활성화 뒤 비활성 사용자를 가리키는 포인터 일괄 재지정."""

    def _session_with_teams(self, teams: list) -> AsyncMock:
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = teams
        session.execute = AsyncMock(return_value=result)
        session.begin_nested = MagicMock(return_value=MagicMock())
        return session

    async def test_repoints_teams_pointed_at_inactive_users(self):
        from app.services.user_team_service import repoint_inactive_leader_pointers

        team_id = uuid.uuid4()
        inactive_id = uuid.uuid4()
        remaining_id = uuid.uuid4()
        remaining = _user(remaining_id, team_id, role=UserRole.TEAM_LEADER)
        team = _team(team_id, members=[remaining], leader_user_id=inactive_id)

        session = self._session_with_teams([team])
        fixed = await repoint_inactive_leader_pointers(session)

        assert fixed == 1
        assert team.leader_user_id == remaining_id

    async def test_clears_pointer_when_no_active_leader_left(self):
        from app.services.user_team_service import repoint_inactive_leader_pointers

        inactive_id = uuid.uuid4()
        team = _team(uuid.uuid4(), members=[], leader_user_id=inactive_id)

        session = self._session_with_teams([team])
        fixed = await repoint_inactive_leader_pointers(session)

        assert fixed == 1
        assert team.leader_user_id is None
