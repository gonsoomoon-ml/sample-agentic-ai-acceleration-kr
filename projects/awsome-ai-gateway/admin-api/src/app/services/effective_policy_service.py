# Copyright 2026 © Amazon.com and Affiliates.
"""EffectivePolicyService — 사용자에게 적용되는 모든 정책의 합성 읽기 전용 뷰.

게이트웨이가 요청 시점에 AND 로 걸어 판정하는 축들을 한 번에 계산해 내려준다:

  * ``auth.user_allowed_clients``   → user_app  축 (빈 목록 = 전체 허용, fail-open)
  * ``user/team_allowed_models``    → user_model 축 (user > team > none 우선순위)
  * ``model_aliases.allowed_clients`` → model_app 축 (NULL = 전체 허용, [] = 전면 거부)
  * ``budget.budget_configs``       → 예산 (사용량은 현재 리포팅 월)
  * ``model.rate_limit_configs``    → 적용되는 rate limit (GLOBAL→TEAM→USER)
  * ``budget.downgrade_policies``   → 예산 소진 시 모델 전환 규칙
  * ``model.routing_profiles``      → 앱별 web search 토글

판정 로직은 gateway-proxy 의 check_client_scope / check_client_model_scope 와
같은 의미를 따른다 — 여기서 새 규칙을 만들지 않고 같은 규칙을 미리 보여준다.
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients import VALID_CLIENTS
from app.core.exceptions import NotFoundError
from app.core.usage_filters import current_kst_period
from app.models.auth import (
    Department,
    OrgAllowedClient,
    Team,
    TeamAllowedClient,
    User,
    UserAllowedClient,
)
from app.models.budget import BudgetConfig, BudgetUsage, DowngradePolicy
from app.models.model import (
    ModelAlias,
    ModelStatus,
    RateLimitConfig,
    RateLimitScope,
    TeamAllowedModel,
    UserAllowedModel,
)
from app.models.routing import RoutingProfile
from app.schemas.effective_policy import (
    EffectiveBudgetEntry,
    EffectiveDowngradeRule,
    EffectivePolicyCell,
    EffectivePolicyResponse,
    EffectiveRateLimitEntry,
)


def compute_cells(
    allowed_clients: list[str] | None,
    effective_models: list[str] | None,
    models: list[tuple[str, list[str] | None]],
) -> list[EffectivePolicyCell]:
    """clients × models 매트릭스 — 순수 함수 (테스트 가능한 판정 코어).

    게이트웨이와 동일 의미:
      * user_app  : allowed_clients 가 비어있지 않은 목록이고 client 가 없으면 거부
      * model_app : model.allowed_clients 가 None 이 아니고 client 가 없으면 거부
                    ([] = 전면 거부 — fail-closed, 사용자 축과 의미가 다르다)
      * user_model: effective_models 가 목록이고 alias 가 없으면 거부
    """
    cells: list[EffectivePolicyCell] = []
    for client in sorted(VALID_CLIENTS):
        for alias, model_clients in models:
            blocked: list[str] = []
            if allowed_clients and client not in allowed_clients:
                blocked.append("user_app")
            if effective_models is not None and alias not in effective_models:
                blocked.append("user_model")
            if model_clients is not None and client not in model_clients:
                blocked.append("model_app")
            cells.append(
                EffectivePolicyCell(
                    client=client,
                    model_alias=alias,
                    allowed=not blocked,
                    blocked_by=blocked,
                )
            )
    return cells


class EffectivePolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: uuid.UUID) -> EffectivePolicyResponse:
        user = (
            await self.session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("User", str(user_id))

        team = None
        if user.team_id:
            team = (
                await self.session.execute(
                    select(Team).where(Team.id == user.team_id)
                )
            ).scalar_one_or_none()

        # ── 축 1: user→app (user > team > org > 제한없음 — 게이트웨이와 동일 순서) ──
        uac = list(
            (
                await self.session.execute(
                    select(UserAllowedClient.client).where(
                        UserAllowedClient.user_id == user.id
                    )
                )
            ).scalars()
        )
        if uac:
            allowed_clients: list[str] | None = uac
            clients_source = "user"
        elif user.team_id:
            tac = list(
                (
                    await self.session.execute(
                        select(TeamAllowedClient.client).where(
                            TeamAllowedClient.team_id == user.team_id
                        )
                    )
                ).scalars()
            )
            if tac:
                allowed_clients = tac
                clients_source = "team"
            elif team and team.dept_id:
                # team → department → organization 경로로 org 정책을 찾는다
                dept = (
                    await self.session.execute(
                        select(Department.org_id).where(Department.id == team.dept_id)
                    )
                ).scalar_one_or_none()
                oac = (
                    list(
                        (
                            await self.session.execute(
                                select(OrgAllowedClient.client).where(
                                    OrgAllowedClient.org_id == dept
                                )
                            )
                        ).scalars()
                    )
                    if dept
                    else []
                )
                allowed_clients = oac if oac else None
                clients_source = "organization" if oac else "none"
            else:
                allowed_clients = None
                clients_source = "none"
        else:
            allowed_clients = None
            clients_source = "none"

        # ── 축 2: user/team→model (user > team > none) ──
        uam = list(
            (
                await self.session.execute(
                    select(UserAllowedModel.model_alias).where(
                        UserAllowedModel.user_id == user.id
                    )
                )
            ).scalars()
        )
        if uam:
            effective_models: list[str] | None = uam
            models_source = "user"
        elif user.team_id:
            tam = list(
                (
                    await self.session.execute(
                        select(TeamAllowedModel.model_alias).where(
                            TeamAllowedModel.team_id == user.team_id
                        )
                    )
                ).scalars()
            )
            effective_models = tam if tam else None
            models_source = "team" if tam else "none"
        else:
            effective_models = None
            models_source = "none"

        # ── 축 3: model→app (ACTIVE 모델 전체) ──
        model_rows = list(
            (
                await self.session.execute(
                    select(ModelAlias.alias, ModelAlias.allowed_clients).where(
                        ModelAlias.status == ModelStatus.ACTIVE
                    )
                )
            ).all()
        )

        cells = compute_cells(allowed_clients, effective_models, model_rows)

        # ── 예산 (user + team, 현재 리포팅 월 사용량) ──
        scope_ids = [x for x in (user.id, user.team_id) if x]
        budget_rows = list(
            (
                await self.session.execute(
                    select(BudgetConfig)
                    .where(BudgetConfig.is_active.is_(True))
                    .where(BudgetConfig.scope_id.in_(scope_ids))
                )
            ).scalars()
        )
        period = current_kst_period()
        usage_rows = list(
            (
                await self.session.execute(
                    select(BudgetUsage)
                    .where(BudgetUsage.period == period)
                    .where(BudgetUsage.scope_id.in_(scope_ids))
                )
            ).scalars()
        )
        usage_map = {
            (u.scope.value if hasattr(u.scope, "value") else u.scope, u.scope_id, u.client): u.used_usd
            for u in usage_rows
        }
        budgets = []
        for b in budget_rows:
            b_scope = b.scope.value if hasattr(b.scope, "value") else str(b.scope)
            used = usage_map.get((b_scope, b.scope_id, b.client))
            budgets.append(
                EffectiveBudgetEntry(
                    scope=b_scope,
                    client=b.client,
                    max_budget_usd=str(b.max_budget_usd),
                    used_usd=str(used) if used is not None else None,
                    policy=b.policy.value if hasattr(b.policy, "value") else str(b.policy),
                )
            )

        # ── rate limits (GLOBAL + team + user) ──
        rl_rows = list(
            (
                await self.session.execute(
                    select(RateLimitConfig)
                    .where(RateLimitConfig.is_active.is_(True))
                    .where(
                        or_(
                            RateLimitConfig.scope == RateLimitScope.GLOBAL,
                            RateLimitConfig.scope_id.in_(scope_ids),
                        )
                    )
                )
            ).scalars()
        )
        rate_limits = [
            EffectiveRateLimitEntry(
                scope=r.scope.value if hasattr(r.scope, "value") else str(r.scope),
                model_alias=r.model_alias,
                rpm_limit=r.rpm_limit,
                tpm_limit=r.tpm_limit,
                cpm_limit_usd=str(r.cpm_limit_usd) if r.cpm_limit_usd is not None else None,
                cph_limit_usd=str(r.cph_limit_usd) if r.cph_limit_usd is not None else None,
            )
            for r in rl_rows
        ]

        # ── downgrade rules (user + team) ──
        dg_rows = list(
            (
                await self.session.execute(
                    select(DowngradePolicy)
                    .where(DowngradePolicy.is_active.is_(True))
                    .where(DowngradePolicy.scope_id.in_(scope_ids))
                )
            ).scalars()
        )
        downgrade_rules = [
            EffectiveDowngradeRule(
                scope=d.scope.value if hasattr(d.scope, "value") else str(d.scope),
                threshold_pct=d.threshold_pct,
                from_model_alias=d.from_model_alias,
                to_model_alias=d.to_model_alias,
            )
            for d in dg_rows
        ]

        # ── web search per app ──
        rp_rows = list(
            (await self.session.execute(select(RoutingProfile))).scalars()
        )
        web_search = {r.client: bool(r.web_search_enabled) for r in rp_rows}

        return EffectivePolicyResponse(
            user_id=str(user.id),
            email=user.email,
            team_id=str(user.team_id) if user.team_id else None,
            team_name=team.name if team else None,
            allowed_clients=allowed_clients,
            allowed_clients_source=clients_source,
            allowed_models=effective_models,
            allowed_models_source=models_source,
            web_search=web_search,
            cells=cells,
            budgets=budgets,
            rate_limits=rate_limits,
            downgrade_rules=downgrade_rules,
        )
