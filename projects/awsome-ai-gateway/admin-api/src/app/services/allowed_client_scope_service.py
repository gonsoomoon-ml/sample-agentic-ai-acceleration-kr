# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_logger
from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.clients import VALID_CLIENTS
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import Department, Organization, Team, User
from app.repositories.allowed_client_repository import (
    OrgAllowedClientRepository,
    TeamAllowedClientRepository,
)
from app.repositories.user_repository import UserRepository
from app.schemas.users import ScopedAllowedClientsResponse

logger = structlog.get_logger()


class ScopedAllowedClientService:
    """팀/조직 단위 앱 접근 정책 (alembic 0038).

    우선순위 user > team > org > 제한없음 — 개인 행이 있으면 하위 정책을 무시한다.

    ⚠️ 캐시 무효화 순서: set/clear 는 무효화를 **하지 않는다**. 라우터가
    ``session.commit()`` **뒤에** ``invalidate_for_team/org`` 를 호출해야 한다 —
    DEL→commit 창에 들어온 게이트웨이 요청이 옛(느슨한) 정책을 재캐시하면 VK 캐시
    TTL(~300s) 동안 살아 있다. user_allowed_clients 경로의 주석과 같은 규칙.
    """

    def __init__(self, cache_mgr: CacheInvalidationManager) -> None:
        self._cache_mgr = cache_mgr

    # ── team ────────────────────────────────────────────────────────────────

    async def list_for_team(
        self, session: AsyncSession, *, team_id: uuid.UUID
    ) -> ScopedAllowedClientsResponse:
        if await UserRepository(session).get_team(team_id) is None:
            raise NotFoundError("Team", str(team_id))
        clients = await TeamAllowedClientRepository(session).list_by_team(team_id)
        return ScopedAllowedClientsResponse(scope_id=str(team_id), clients=clients)

    async def set_for_team(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        clients: list[str],
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ScopedAllowedClientsResponse:
        if await UserRepository(session).get_team(team_id) is None:
            raise NotFoundError("Team", str(team_id))
        self._validate(clients)

        repo = TeamAllowedClientRepository(session)
        before = await repo.list_by_team(team_id)
        await repo.replace_for_team(team_id, sorted(set(clients)), actor.user_id)
        after = await repo.list_by_team(team_id)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="SET_TEAM_ALLOWED_CLIENTS",
            resource_type="TeamAllowedClients",
            resource_id=str(team_id),
            changes={"before": before, "after": after},
            ip_address=ip_address,
            request_id=request_id,
        )
        return ScopedAllowedClientsResponse(scope_id=str(team_id), clients=after)

    async def clear_for_team(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ScopedAllowedClientsResponse:
        if await UserRepository(session).get_team(team_id) is None:
            raise NotFoundError("Team", str(team_id))
        repo = TeamAllowedClientRepository(session)
        before = await repo.list_by_team(team_id)
        await repo.clear_for_team(team_id)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="CLEAR_TEAM_ALLOWED_CLIENTS",
            resource_type="TeamAllowedClients",
            resource_id=str(team_id),
            changes={"before": before, "after": []},
            ip_address=ip_address,
            request_id=request_id,
        )
        return ScopedAllowedClientsResponse(scope_id=str(team_id), clients=[])

    # ── org ─────────────────────────────────────────────────────────────────

    async def list_for_org(
        self, session: AsyncSession, *, org_id: uuid.UUID
    ) -> ScopedAllowedClientsResponse:
        if await self._get_org(session, org_id) is None:
            raise NotFoundError("Organization", str(org_id))
        clients = await OrgAllowedClientRepository(session).list_by_org(org_id)
        return ScopedAllowedClientsResponse(scope_id=str(org_id), clients=clients)

    async def set_for_org(
        self,
        session: AsyncSession,
        *,
        org_id: uuid.UUID,
        clients: list[str],
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ScopedAllowedClientsResponse:
        if await self._get_org(session, org_id) is None:
            raise NotFoundError("Organization", str(org_id))
        self._validate(clients)

        repo = OrgAllowedClientRepository(session)
        before = await repo.list_by_org(org_id)
        await repo.replace_for_org(org_id, sorted(set(clients)), actor.user_id)
        after = await repo.list_by_org(org_id)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="SET_ORG_ALLOWED_CLIENTS",
            resource_type="OrgAllowedClients",
            resource_id=str(org_id),
            changes={"before": before, "after": after},
            ip_address=ip_address,
            request_id=request_id,
        )
        return ScopedAllowedClientsResponse(scope_id=str(org_id), clients=after)

    async def clear_for_org(
        self,
        session: AsyncSession,
        *,
        org_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ScopedAllowedClientsResponse:
        if await self._get_org(session, org_id) is None:
            raise NotFoundError("Organization", str(org_id))
        repo = OrgAllowedClientRepository(session)
        before = await repo.list_by_org(org_id)
        await repo.clear_for_org(org_id)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="CLEAR_ORG_ALLOWED_CLIENTS",
            resource_type="OrgAllowedClients",
            resource_id=str(org_id),
            changes={"before": before, "after": []},
            ip_address=ip_address,
            request_id=request_id,
        )
        return ScopedAllowedClientsResponse(scope_id=str(org_id), clients=[])

    # ── post-commit 캐시 무효화 (라우터가 commit 후 호출) ──────────────────────

    async def invalidate_for_team(self, session: AsyncSession, team_id: uuid.UUID) -> None:
        """팀 멤버 전원의 AuthContext 캐시(`key:cache:vk:*`, `user_context:*`) 무효화
        — **commit 후**에 호출. team_allowed_models 의 `_invalidate_team_vk_cache`
        와 같은 키 집합을 지운다.
        """
        await self._invalidate_users(
            session, select(User.id).where(User.team_id == team_id)
        )
        await self._invalidate_team_vk_index(team_id)

    async def invalidate_for_org(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        """조직 산하(dept→team) 전체 사용자 무효화 — **commit 후**에 호출.

        팀 정책이 있는 멤버는 org 변경의 영향을 받지 않지만 무효화해도 안전하다
        (다음 요청 시 재해결). teamless 사용자는 org 정책 대상이 아니므로 제외.
        """
        team_ids = list(
            (
                await session.execute(
                    select(Team.id)
                    .join(Department, Team.dept_id == Department.id)
                    .where(Department.org_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        if team_ids:
            # in_([]) 은 SQLAlchemy 가 WHERE false 로 접는다 — 가드로 명확히.
            await self._invalidate_users(
                session, select(User.id).where(User.team_id.in_(team_ids))
            )
        for tid in team_ids:
            await self._invalidate_team_vk_index(tid)

    # ── internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _validate(clients: list[str]) -> None:
        invalid = [c for c in clients if c not in VALID_CLIENTS]
        if invalid:
            raise ValidationError(
                f"invalid clients: {invalid}; allowed={sorted(VALID_CLIENTS)}"
            )

    @staticmethod
    async def _get_org(session: AsyncSession, org_id: uuid.UUID) -> Organization | None:
        return (
            await session.execute(select(Organization).where(Organization.id == org_id))
        ).scalar_one_or_none()

    async def _invalidate_users(self, session: AsyncSession, user_id_stmt) -> None:
        result = await session.execute(user_id_stmt)
        keys = [f"user_context:{uid}" for uid in result.scalars().all()]
        if keys:
            await self._cache_mgr.invalidate(keys, session=session)

    async def _invalidate_team_vk_index(self, team_id: uuid.UUID) -> None:
        """`key:cache:vk:*` 는 raw 해시를 모르므로 발급 시 저장한 reverse index
        `team:vk_hashes:{team_id}` 경유로 DEL. 인덱스 없음/읽기 실패 → TTL(300s)
        자연 만료(team_allowed_models 와 같은 tradeoff)."""
        try:
            vk_hashes = await self._cache_mgr._redis.smembers(
                f"team:vk_hashes:{team_id}"
            )
            keys = [
                f"key:cache:vk:{h.decode() if isinstance(h, bytes) else h}"
                for h in vk_hashes
            ]
        except Exception:
            keys = []
            logger.warning(
                "allowed_clients.vk_index_read_failed",
                team_id=str(team_id),
                exc_info=True,
            )
        if keys:
            await self._cache_mgr.invalidate(keys)
