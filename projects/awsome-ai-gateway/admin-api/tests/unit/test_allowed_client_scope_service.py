# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""팀/조직 앱 접근 정책(alembic 0038) 서비스 단위 테스트.

검증 포인트:
  * 존재하지 않는 스코프 → NotFoundError
  * VALID_CLIENTS 밖 값 → ValidationError
  * set/clear 가 repository replace/clear 로 위임되고 audit 로그가 남는다
  * 캐시 무효화는 set/clear 가 **하지 않고** invalidate_for_team/org 가 한다
    (라우터가 commit 후 호출하는 계약 — DEL→commit 재캐시 방지)
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import Team
from app.services.allowed_client_scope_service import ScopedAllowedClientService

SVC = "app.services.allowed_client_scope_service"


@pytest.fixture
def svc(cache_mgr: CacheInvalidationManager) -> ScopedAllowedClientService:
    return ScopedAllowedClientService(cache_mgr=cache_mgr)


def _team(team_id: uuid.UUID) -> MagicMock:
    t = MagicMock(spec=Team)
    t.id = team_id
    t.name = "T"
    t.dept_id = uuid.uuid4()
    return t


def _org_found(session: AsyncMock) -> None:
    """_get_org 용 — scalar_one_or_none 이 Organization 을 돌려주게 한다."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = MagicMock()
    session.execute = AsyncMock(return_value=r)


class TestTeamPolicy:
    async def test_list_not_found(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock
    ):
        with patch(f"{SVC}.UserRepository") as MockUser:
            MockUser.return_value.get_team = AsyncMock(return_value=None)
            with pytest.raises(NotFoundError):
                await svc.list_for_team(mock_session, team_id=uuid.uuid4())

    async def test_list_returns_clients(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock
    ):
        team_id = uuid.uuid4()
        with patch(f"{SVC}.UserRepository") as MockUser, patch(
            f"{SVC}.TeamAllowedClientRepository"
        ) as MockRepo:
            MockUser.return_value.get_team = AsyncMock(return_value=_team(team_id))
            MockRepo.return_value.list_by_team = AsyncMock(
                return_value=["claude-code", "cowork"]
            )
            resp = await svc.list_for_team(mock_session, team_id=team_id)

        assert resp.scope_id == str(team_id)
        assert resp.clients == ["claude-code", "cowork"]

    async def test_set_validates_clients(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        with patch(f"{SVC}.UserRepository") as MockUser:
            MockUser.return_value.get_team = AsyncMock(return_value=_team(team_id))
            with pytest.raises(ValidationError):
                await svc.set_for_team(
                    mock_session,
                    team_id=team_id,
                    clients=["not-a-client"],
                    actor=admin_user,
                )

    async def test_set_replaces_and_audits(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        with patch(f"{SVC}.UserRepository") as MockUser, patch(
            f"{SVC}.TeamAllowedClientRepository"
        ) as MockRepo, patch(f"{SVC}.audit_logger") as mock_audit:
            MockUser.return_value.get_team = AsyncMock(return_value=_team(team_id))
            repo = MockRepo.return_value
            repo.list_by_team = AsyncMock(side_effect=[[], ["codex"]])
            repo.replace_for_team = AsyncMock()
            mock_audit.log = AsyncMock()

            resp = await svc.set_for_team(
                mock_session,
                team_id=team_id,
                clients=["codex"],
                actor=admin_user,
            )

        repo.replace_for_team.assert_awaited_once_with(
            team_id, ["codex"], admin_user.user_id
        )
        assert resp.clients == ["codex"]
        assert mock_audit.log.call_args.kwargs["action"] == "SET_TEAM_ALLOWED_CLIENTS"
        assert mock_audit.log.call_args.kwargs["changes"] == {
            "before": [],
            "after": ["codex"],
        }

    async def test_clear_deletes_rows(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        with patch(f"{SVC}.UserRepository") as MockUser, patch(
            f"{SVC}.TeamAllowedClientRepository"
        ) as MockRepo, patch(f"{SVC}.audit_logger") as mock_audit:
            MockUser.return_value.get_team = AsyncMock(return_value=_team(team_id))
            repo = MockRepo.return_value
            repo.list_by_team = AsyncMock(return_value=["codex"])
            repo.clear_for_team = AsyncMock()
            mock_audit.log = AsyncMock()

            resp = await svc.clear_for_team(
                mock_session, team_id=team_id, actor=admin_user
            )

        repo.clear_for_team.assert_awaited_once_with(team_id)
        assert resp.clients == []
        assert mock_audit.log.call_args.kwargs["action"] == "CLEAR_TEAM_ALLOWED_CLIENTS"


class TestOrgPolicy:
    async def test_list_org_not_found(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock
    ):
        # _get_org → scalar_one_or_none() = None
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=r)
        with pytest.raises(NotFoundError):
            await svc.list_for_org(mock_session, org_id=uuid.uuid4())

    async def test_set_org(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        org_id = uuid.uuid4()
        _org_found(mock_session)
        with patch(f"{SVC}.OrgAllowedClientRepository") as MockRepo, patch(
            f"{SVC}.audit_logger"
        ) as mock_audit:
            repo = MockRepo.return_value
            repo.list_by_org = AsyncMock(side_effect=[[], ["claude-code"]])
            repo.replace_for_org = AsyncMock()
            mock_audit.log = AsyncMock()

            resp = await svc.set_for_org(
                mock_session,
                org_id=org_id,
                clients=["claude-code"],
                actor=admin_user,
            )

        repo.replace_for_org.assert_awaited_once_with(
            org_id, ["claude-code"], admin_user.user_id
        )
        assert resp.clients == ["claude-code"]
        assert mock_audit.log.call_args.kwargs["action"] == "SET_ORG_ALLOWED_CLIENTS"


class TestInvalidationIsPostCommitContract:
    """set/clear 가 캐시를 직접 건드리지 않고 invalidate_for_* 가 DEL 한다."""

    async def test_set_does_not_invalidate(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, admin_user: CurrentUser,
        mock_redis: AsyncMock,
    ):
        team_id = uuid.uuid4()
        with patch(f"{SVC}.UserRepository") as MockUser, patch(
            f"{SVC}.TeamAllowedClientRepository"
        ) as MockRepo, patch(f"{SVC}.audit_logger") as mock_audit:
            MockUser.return_value.get_team = AsyncMock(return_value=_team(team_id))
            repo = MockRepo.return_value
            repo.list_by_team = AsyncMock(side_effect=[[], ["codex"]])
            repo.replace_for_team = AsyncMock()
            mock_audit.log = AsyncMock()

            await svc.set_for_team(
                mock_session, team_id=team_id, clients=["codex"], actor=admin_user
            )

        # commit 전 DEL 이 없어야 한다 — DEL 은 invalidate_for_team 만 한다.
        mock_redis.delete.assert_not_called()

    async def test_invalidate_for_team_deletes_member_contexts(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, mock_redis: AsyncMock
    ):
        team_id = uuid.uuid4()
        uid = uuid.uuid4()
        r = MagicMock()
        r.scalars.return_value.all.return_value = [uid]
        mock_session.execute = AsyncMock(return_value=r)
        mock_redis.smembers = AsyncMock(return_value=set())

        await svc.invalidate_for_team(mock_session, team_id)

        # invalidate() 는 키별로 redis.delete(key) 를 호출한다.
        deleted = {c.args[0] for c in mock_redis.delete.call_args_list}
        assert deleted == {f"user_context:{uid}"}
        mock_redis.smembers.assert_awaited_once_with(f"team:vk_hashes:{team_id}")

    async def test_invalidate_for_team_deletes_vk_cache_via_reverse_index(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, mock_redis: AsyncMock
    ):
        team_id = uuid.uuid4()
        r = MagicMock()
        r.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=r)
        mock_redis.smembers = AsyncMock(return_value={b"hash1", b"hash2"})

        await svc.invalidate_for_team(mock_session, team_id)

        deleted = {c.args[0] for c in mock_redis.delete.call_args_list}
        assert deleted == {"key:cache:vk:hash1", "key:cache:vk:hash2"}

    async def test_invalidate_for_org_scopes_to_org_users(
        self, svc: ScopedAllowedClientService, mock_session: AsyncMock, mock_redis: AsyncMock
    ):
        org_id, tid = uuid.uuid4(), uuid.uuid4()
        uid1, uid2 = uuid.uuid4(), uuid.uuid4()
        teams_r = MagicMock()
        teams_r.scalars.return_value.all.return_value = [tid]
        users_r = MagicMock()
        users_r.scalars.return_value.all.return_value = [uid1, uid2]
        mock_session.execute = AsyncMock(side_effect=[teams_r, users_r])
        mock_redis.smembers = AsyncMock(return_value=set())

        await svc.invalidate_for_org(mock_session, org_id)

        deleted = {c.args[0] for c in mock_redis.delete.call_args_list}
        assert deleted == {f"user_context:{uid1}", f"user_context:{uid2}"}
        mock_redis.smembers.assert_awaited_once_with(f"team:vk_hashes:{tid}")
