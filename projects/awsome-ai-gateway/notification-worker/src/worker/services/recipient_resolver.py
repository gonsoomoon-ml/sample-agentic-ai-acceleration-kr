# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from worker.models.auth import Team, User
from worker.schemas.recipients import Recipient, RecipientRole

logger = structlog.get_logger(__name__)


class RecipientResolver:
    """이벤트 수신자를 역할 기반으로 결정한다 (BR-RCP).

    역할 → 사용자 매핑:
    - affected_user: payload.user_id → auth.users 조회
    - team_leader:   payload.team_id (또는 user의 team_id) → role=TEAM_LEADER 인 활성 멤버 전원
    - admin:         auth.users에서 roles 배열에 'ADMIN' 포함 전체 조회

    중복 이메일 제거 후 반환 (BR-RCP-03).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(
        self,
        roles: list[str],
        payload: dict,
    ) -> list[Recipient]:
        """주어진 역할 목록과 이벤트 페이로드로 최종 수신자 목록을 반환한다."""
        seen_emails: set[str] = set()
        recipients: list[Recipient] = []

        async with self._session_factory() as session:
            for role in roles:
                try:
                    new = await self._resolve_role(role, payload, session)
                except Exception:
                    logger.exception("recipient_resolve_error", role=role)
                    continue

                for r in new:
                    if r.email not in seen_emails:
                        seen_emails.add(r.email)
                        recipients.append(r)

        return recipients

    async def _resolve_role(
        self,
        role: str,
        payload: dict,
        session: AsyncSession,
    ) -> list[Recipient]:
        if role == RecipientRole.AFFECTED_USER:
            return await self._resolve_affected_user(payload, session)
        if role == RecipientRole.TEAM_LEADER:
            return await self._resolve_team_leader(payload, session)
        if role == RecipientRole.ADMIN:
            # 일괄 작업(force_reauth 등)이 멤버마다 이벤트를 쏘면 admin 은
            # N 통씩 받는다 — 발행자가 이미 작업을 아는 자리라 bulk 표시 이벤트의
            # admin 역할은 생략한다(affected_user/team_leader 는 그대로).
            if payload.get("bulk"):
                logger.debug("admin_recipients_skipped_bulk")
                return []
            return await self._resolve_admins(session)
        logger.warning("unknown_recipient_role", role=role)
        return []

    async def _resolve_affected_user(
        self, payload: dict, session: AsyncSession
    ) -> list[Recipient]:
        user_id = payload.get("user_id")
        if not user_id:
            return []

        result = await session.execute(
            select(User).where(User.id == str(user_id), User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()

        if user is None:
            logger.warning("affected_user_not_found", user_id=user_id)
            return []

        return [Recipient(email=user.email, name=user.display_name, user_id=user.id, role=RecipientRole.AFFECTED_USER)]

    async def _resolve_team_leader(
        self, payload: dict, session: AsyncSession
    ) -> list[Recipient]:
        team_id = payload.get("team_id")

        # team_id가 payload에 없으면 affected_user의 팀에서 조회
        if not team_id:
            user_id = payload.get("user_id")
            if not user_id:
                return []
            result = await session.execute(select(User.team_id).where(User.id == str(user_id)))
            row = result.scalar_one_or_none()
            if row is None:
                return []
            team_id = row

        result = await session.execute(select(Team).where(Team.id == str(team_id)))
        team = result.scalar_one_or_none()

        if team is None:
            logger.debug("team_not_found", team_id=team_id)
            return []

        # 리더는 role 로 판정한다 — 팀에 리더가 여러 명일 수 있고(admin-ui 복수
        # 지정), Team.leader_user_id 는 "가장 최근 지정" 표시용 포인터라 일부
        # 리더만 메일을 받거나 스테일 포인터가 탈퇴자를 가리키는 함정이 있다.
        result = await session.execute(
            select(User).where(
                User.team_id == team.id,
                User.role == "TEAM_LEADER",
                User.is_active.is_(True),
            )
        )
        leaders = result.scalars().all()

        if not leaders:
            # leader 미지정 팀은 오류 아님 (BR-RCP-04)
            logger.debug("team_leader_not_set", team_id=team_id)
            return []

        return [
            Recipient(email=u.email, name=u.display_name, user_id=u.id, role=RecipientRole.TEAM_LEADER)
            for u in leaders
        ]

    async def _resolve_admins(self, session: AsyncSession) -> list[Recipient]:
        result = await session.execute(
            select(User).where(
                User.is_active.is_(True),
                User.role == "ADMIN",
            )
        )
        admins = result.scalars().all()

        if not admins:
            logger.error("no_admin_users_found")
            return []

        return [
            Recipient(email=u.email, name=u.display_name, user_id=u.id, role=RecipientRole.ADMIN)
            for u in admins
        ]
