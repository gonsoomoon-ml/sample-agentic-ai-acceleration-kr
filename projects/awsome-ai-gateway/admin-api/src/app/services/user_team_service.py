# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_logger
from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import Department, Team, User, UserRole
from app.models.budget import BudgetScope
from app.models.model import RateLimitScope
from app.repositories.budget_repository import BudgetRepository
from app.repositories.model_repository import RateLimitConfigRepository
from app.repositories.user_repository import UserRepository
from app.schemas.users import DepartmentResponse, OrgNodeMeta, OrgTreeNode, TeamListItem, TeamResponse, UserResponse
from app.services.key_service import KeyService

logger = structlog.get_logger()


class UserTeamService:
    def __init__(self, cache_mgr: CacheInvalidationManager, key_service: KeyService) -> None:
        self._cache_mgr = cache_mgr
        self._key_service = key_service

    async def create_department(
        self,
        session: AsyncSession,
        *,
        name: str,
        org_id: uuid.UUID | None = None,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> DepartmentResponse:
        repo = UserRepository(session)

        # Default to first org if not specified
        if org_id is None:
            org = await repo.get_default_org()
            if org is None:
                raise NotFoundError("Organization", "default")
            org_id = org.id

        dept = Department(
            id=uuid.uuid4(),
            org_id=org_id,
            name=name,
        )
        await repo.create_department(dept)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="CREATE_DEPARTMENT",
            resource_type="Department",
            resource_id=str(dept.id),
            changes={"after": {"name": name}},
            ip_address=ip_address,
            request_id=request_id,
        )

        return DepartmentResponse(
            id=str(dept.id),
            name=dept.name,
            org_id=str(dept.org_id),
            created_at=dept.created_at,
        )

    async def create_team(
        self,
        session: AsyncSession,
        *,
        name: str,
        department_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> TeamResponse:
        repo = UserRepository(session)
        dept = await repo.get_department(department_id)
        if dept is None:
            raise NotFoundError("Department", str(department_id))

        team = Team(
            id=uuid.uuid4(),
            dept_id=department_id,
            name=name,
        )
        await repo.create_team(team)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="CREATE_TEAM",
            resource_type="Team",
            resource_id=str(team.id),
            changes={"after": {"name": name, "department_id": str(department_id)}},
            ip_address=ip_address,
            request_id=request_id,
        )

        return TeamResponse(
            id=str(team.id),
            name=team.name,
            department_id=str(team.dept_id),
            leader_user_id=str(team.leader_user_id) if team.leader_user_id else None,
            created_at=team.created_at,
        )

    async def set_team_leader(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> TeamResponse:
        """팀원 한 명을 팀 리더로 지정한다.

        팀 하나에 여러 명이 리더일 수 있다 — 이미 리더인 다른 사람을 내리지 않는다
        (role=TEAM_LEADER 는 팀당 배타적 단일값이 아니라 팀원 각자의 속성).
        ``Team.leader_user_id`` 는 "가장 최근에 지정된 리더"를 가리키는 표시용
        포인터일 뿐, 실제 권한(``require_team_leader_of`` 등)은 각 사용자의
        ``role``+``team_id`` 로 판정되므로 이 값과 무관하게 정상 동작한다.
        """
        repo = UserRepository(session)

        team = await repo.get_team(team_id)
        if team is None:
            raise NotFoundError("Team", str(team_id))

        target_user = await repo.get_user(user_id)
        if target_user is None:
            raise NotFoundError("User", str(user_id))
        if target_user.team_id != team_id:
            raise ValidationError(
                f"User {user_id} does not belong to team {team_id} — transfer them first."
            )

        await repo.update_user_role(user_id, UserRole.TEAM_LEADER)
        team.leader_user_id = user_id

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="SET_TEAM_LEADER",
            resource_type="Team",
            resource_id=str(team_id),
            changes={"after": {"leader_user_id": str(user_id)}},
            ip_address=ip_address,
            request_id=request_id,
        )

        return TeamResponse(
            id=str(team.id),
            name=team.name,
            department_id=str(team.dept_id),
            leader_user_id=str(team.leader_user_id) if team.leader_user_id else None,
            created_at=team.created_at,
        )

    async def unset_team_leader(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> TeamResponse:
        """팀 리더 지정 해제(한 명) — 그 사람의 role 만 DEVELOPER 로 되돌린다.

        팀에 리더가 여러 명일 수 있으므로 어느 사용자를 해제할지 반드시 지정해야
        한다. ``Team.leader_user_id`` (표시용 포인터)가 이 사람을 가리키고 있었다면
        남은 리더 중 한 명으로 옮기고, 아무도 없으면 비운다.

        ADMIN 은 영향받지 않는다(_derive_role 이 ADMIN_GROUPS 로 별도 판정) — 여기서
        DEVELOPER 로 되돌려도 그 사람이 ClaudeAdmin 이면 다음 Cognito 로그인 때 다시
        ADMIN 으로 복원된다.
        """
        repo = UserRepository(session)

        team = await repo.get_team(team_id)
        if team is None:
            raise NotFoundError("Team", str(team_id))

        target_user = await repo.get_user(user_id)
        if target_user is None or target_user.team_id != team_id:
            raise NotFoundError("Team leader", str(user_id))
        if target_user.role != UserRole.TEAM_LEADER:
            raise ValidationError(f"User {user_id} is not currently a team leader of this team.")

        await repo.update_user_role(user_id, UserRole.DEVELOPER)

        if team.leader_user_id == user_id:
            remaining_leader = next(
                (m for m in team.members if m.id != user_id and m.role == UserRole.TEAM_LEADER),
                None,
            )
            team.leader_user_id = remaining_leader.id if remaining_leader else None

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="UNSET_TEAM_LEADER",
            resource_type="Team",
            resource_id=str(team_id),
            changes={
                "before": {"leader_user_id": str(user_id)},
                "after": {"leader_user_id": str(team.leader_user_id) if team.leader_user_id else None},
            },
            ip_address=ip_address,
            request_id=request_id,
        )

        return TeamResponse(
            id=str(team.id),
            name=team.name,
            department_id=str(team.dept_id),
            leader_user_id=str(team.leader_user_id) if team.leader_user_id else None,
            created_at=team.created_at,
        )

    async def transfer_user(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        new_team_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> UserResponse:
        repo = UserRepository(session)

        user = await repo.get_user(user_id)
        if user is None:
            raise NotFoundError("User", str(user_id))
        old_team_id = user.team_id

        # 팀 리더십은 팀별 속성이라 이관되지 않는다 — 그대로 두면 옛 팀의 leader_user_id
        # 가 이제 그 팀 소속도 아닌 사람을 계속 가리키고(스테일 포인터), 동시에
        # require_team_leader_of(role+team_id 로만 판정)가 새 팀에서 이 사람을
        # 명시적 지정 없이 리더로 인가해버리는 의도치 않은 권한 상승이 발생한다.
        # (authenticate_for_admin_ui 가 Cognito 그룹 변경 시 이 메서드를 자동 호출하므로,
        # 이 버그는 관리자 조작 없이도 Cognito 그룹 재배정만으로 트리거될 수 있었다.)
        # 같은 팀으로의 no-op 호출(new_team_id == old_team_id)까지 강등시키지 않도록
        # 실제로 팀이 바뀌는 경우에만 적용한다.
        if user.role == UserRole.TEAM_LEADER and new_team_id != old_team_id:
            await repo.update_user_role(user_id, UserRole.DEVELOPER)
            if old_team_id is not None:
                old_team = await repo.get_team(old_team_id)
                if old_team is not None and old_team.leader_user_id == user_id:
                    remaining_leader = next(
                        (
                            m
                            for m in old_team.members
                            if m.id != user_id and m.role == UserRole.TEAM_LEADER
                        ),
                        None,
                    )
                    old_team.leader_user_id = remaining_leader.id if remaining_leader else None

        # BR-BUD-04: Deactivate existing user budget configs
        budget_repo = BudgetRepository(session)
        await budget_repo.deactivate_configs(BudgetScope.USER, user_id)

        # BR-RL: Deactivate existing USER scope rate-limit configs
        rl_repo = RateLimitConfigRepository(session)
        await rl_repo.deactivate_configs(RateLimitScope.USER, user_id)

        # Capture VK hashes before team update (still valid pre-transfer)
        vk_hashes: list[str] = await self._key_service.list_active_vk_hashes_for_user(session, user_id)

        # Transfer team
        user = await repo.update_user_team(user_id, new_team_id)

        # Cache invalidation: user context + budget config cache + all VK caches
        # RL config 캐시는 gateway-proxy 가 다른 namespace (rl:config:USER:<uid>:<model>) 로 관리.
        # 현재 admin-api 에서 직접 invalidate 하는 표준 패턴이 없어 5분 TTL 자연 만료에 의존.
        # 별도 후속 작업으로 wildcard invalidate 또는 통합 namespace 정리 필요.
        cache_keys: list[str] = [
            f"user_context:{user_id}",
            f"budget:config:user:{{{user_id}}}",
            *[f"key:cache:vk:{h}" for h in vk_hashes],
        ]
        await self._cache_mgr.invalidate(cache_keys, session=session)

        # Reverse-index swap: move VK hashes from old team set to new team set
        if old_team_id is not None and vk_hashes:
            try:
                await self._cache_mgr.swap_reverse_index_membership(
                    old_key=f"team:vk_hashes:{old_team_id}",
                    new_key=f"team:vk_hashes:{new_team_id}",
                    members=vk_hashes,
                    session=session,
                )
            except Exception:
                logger.exception(
                    "transfer_user.reverse_index_swap_failed",
                    user_id=str(user_id),
                    old_team_id=str(old_team_id),
                    new_team_id=str(new_team_id),
                )

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="TRANSFER_USER",
            resource_type="User",
            resource_id=str(user_id),
            changes={
                "before": {"team_id": str(old_team_id) if old_team_id else None},
                "after": {"team_id": str(new_team_id)},
            },
            ip_address=ip_address,
            request_id=request_id,
        )

        return self._to_user_response(user)

    async def list_users(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        is_active: bool | None = None,
        email: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[UserResponse], bool]:
        repo = UserRepository(session)
        cursor_uuid = uuid.UUID(cursor) if cursor else None
        users = await repo.list_users(
            team_id=team_id,
            department_id=department_id,
            is_active=is_active,
            email=email,
            cursor=cursor_uuid,
            limit=limit + 1,
        )
        has_more = len(users) > limit
        if has_more:
            users = users[:limit]
        return [self._to_user_response(u) for u in users], has_more

    async def list_teams(self, session: AsyncSession) -> list[TeamListItem]:
        repo = UserRepository(session)
        teams = await repo.list_all_teams()
        return [
            TeamListItem(
                id=str(t.id),
                name=t.name,
                department_id=str(t.dept_id),
                department_name=t.department.name if t.department else None,
                leader_user_id=str(t.leader_user_id) if t.leader_user_id else None,
                member_count=len([m for m in t.members if m.is_active]),
            )
            for t in teams
        ]

    async def get_org_tree(self, session: AsyncSession) -> OrgTreeNode | None:
        repo = UserRepository(session)
        orgs = await repo.list_all_orgs()
        if not orgs:
            return None
        org = orgs[0]

        dept_nodes: list[OrgTreeNode] = []
        for dept in org.departments:
            team_nodes: list[OrgTreeNode] = []
            for team in dept.teams:
                leader = next(
                    (m for m in team.members if team.leader_user_id and m.id == team.leader_user_id),
                    None,
                )
                active_members = [m for m in team.members if m.is_active]
                if not active_members:
                    continue
                member_nodes: list[OrgTreeNode] = []
                for member in active_members:
                    member_nodes.append(
                        OrgTreeNode(
                            id=str(member.id),
                            name=member.display_name,
                            type="USER",
                            children=[],
                            meta=OrgNodeMeta(
                                member_count=None,
                                leader_name=None,
                                email=member.email,
                                role=member.role.value,
                                team_name=team.name,
                            ),
                        )
                    )
                team_nodes.append(
                    OrgTreeNode(
                        id=str(team.id),
                        name=team.name,
                        type="TEAM",
                        children=member_nodes,
                        meta=OrgNodeMeta(
                            member_count=len(active_members),
                            leader_name=leader.display_name if leader else None,
                            leader_user_id=str(team.leader_user_id) if team.leader_user_id else None,
                            email=leader.email if leader else None,
                            role=None,
                            team_name=None,
                        ),
                    )
                )
            if not team_nodes:
                continue
            dept_nodes.append(
                OrgTreeNode(
                    id=str(dept.id),
                    name=dept.name,
                    type="DEPARTMENT",
                    children=team_nodes,
                    meta=OrgNodeMeta(
                        member_count=sum(len(n.children) for n in team_nodes),
                        leader_name=None,
                        email=None,
                        role=None,
                        team_name=None,
                    ),
                )
            )

        return OrgTreeNode(
            id=str(org.id),
            name=org.name,
            type="ORGANIZATION",
            children=dept_nodes,
            meta=OrgNodeMeta(
                member_count=sum(len(t.members) for d in org.departments for t in d.teams),
                leader_name=None,
                email=None,
                role=None,
                team_name=None,
            ),
        )

    @staticmethod
    def _to_user_response(user: User) -> UserResponse:
        return UserResponse(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            team_id=str(user.team_id) if user.team_id else None,
            team_name=user.team.name if user.team else None,
            is_active=user.is_active,
            created_at=user.created_at,
        )
