# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import OrgAllowedClient, TeamAllowedClient


class TeamAllowedClientRepository:
    """auth.team_allowed_clients — 팀 앱 접근 정책(0행 = 상위 폴백, alembic 0038)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_team(self, team_id: uuid.UUID) -> list[str]:
        stmt = select(TeamAllowedClient.client).where(TeamAllowedClient.team_id == team_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def replace_for_team(
        self, team_id: uuid.UUID, clients: list[str], created_by: uuid.UUID
    ) -> None:
        # replace-all: 0개면 모두 삭제(= 정책 없음 → 상위로 폴백). user_allowed_clients 와 동일 관례.
        await self._session.execute(
            delete(TeamAllowedClient).where(TeamAllowedClient.team_id == team_id)
        )
        for c in clients:
            self._session.add(
                TeamAllowedClient(team_id=team_id, client=c, created_by=created_by)
            )
        await self._session.flush()

    async def clear_for_team(self, team_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(TeamAllowedClient).where(TeamAllowedClient.team_id == team_id)
        )
        await self._session.flush()


class OrgAllowedClientRepository:
    """auth.org_allowed_clients — 조직 기본 앱 접근 정책(0행 = 제한 없음)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_org(self, org_id: uuid.UUID) -> list[str]:
        stmt = select(OrgAllowedClient.client).where(OrgAllowedClient.org_id == org_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def replace_for_org(
        self, org_id: uuid.UUID, clients: list[str], created_by: uuid.UUID
    ) -> None:
        await self._session.execute(
            delete(OrgAllowedClient).where(OrgAllowedClient.org_id == org_id)
        )
        for c in clients:
            self._session.add(
                OrgAllowedClient(org_id=org_id, client=c, created_by=created_by)
            )
        await self._session.flush()

    async def clear_for_org(self, org_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(OrgAllowedClient).where(OrgAllowedClient.org_id == org_id)
        )
        await self._session.flush()
