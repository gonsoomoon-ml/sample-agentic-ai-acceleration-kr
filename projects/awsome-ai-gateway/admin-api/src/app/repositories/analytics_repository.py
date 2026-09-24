# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ColumnElement, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage_filters import client_filter, cost_period_filter, kst_month_expr
from app.models.usage import ROIAggregation, ROIScope, UsageLog


class AnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── ROIAggregation (pre-aggregated read) ──

    async def get_aggregations(
        self,
        period: str,
        scope: ROIScope | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> list[ROIAggregation]:
        stmt = select(ROIAggregation).where(ROIAggregation.period == period)
        if scope:
            stmt = stmt.where(ROIAggregation.scope == scope)
        if scope_id:
            stmt = stmt.where(ROIAggregation.scope_id == scope_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_aggregation(self, agg: ROIAggregation) -> ROIAggregation:
        """UPSERT by period + scope + scope_id."""
        existing_stmt = select(ROIAggregation).where(
            ROIAggregation.period == agg.period,
            ROIAggregation.scope == agg.scope,
            ROIAggregation.scope_id == agg.scope_id,
        )
        result = await self._session.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.total_cost_usd = agg.total_cost_usd
            existing.cost_per_user_usd = agg.cost_per_user_usd
            existing.budget_utilization_pct = agg.budget_utilization_pct
            existing.cost_by_model = agg.cost_by_model
            existing.active_users = agg.active_users
            existing.active_user_rate_pct = agg.active_user_rate_pct
            existing.requests_per_user_per_day = agg.requests_per_user_per_day
            existing.activation_gap_pct = agg.activation_gap_pct
            existing.aggregated_at = agg.aggregated_at
            existing.aggregated_by = agg.aggregated_by
            return existing
        else:
            self._session.add(agg)
            await self._session.flush()
            return agg

    # ── UsageLog queries (for scheduler aggregation) ──

    async def sum_usage_by_model(
        self, period: str, scope: ROIScope, scope_id: uuid.UUID | None, client: str | None = None,
        scope_ids: list[uuid.UUID] | None = None,
        cost_where: ColumnElement | None = None,
    ) -> dict[str, Decimal]:
        """Returns {model_alias: total_cost_usd} for the given period/scope.

        `cost_where` 를 넘기면 period 대신 그 WHERE 를 쓴다 — Analytics 의
        custom 날짜 구간(cost_date_range_filter)이 이 경로를 탄다.
        """
        stmt = select(
            UsageLog.model_alias,
            func.sum(UsageLog.cost_usd).label("total_cost"),
        ).where(
            cost_where if cost_where is not None else cost_period_filter(period),  # §59 SUCCESS + KST
        )
        stmt = self._apply_scope_filter(stmt, scope, scope_id, scope_ids)
        stmt = self._apply_client_filter(stmt, client)
        stmt = stmt.group_by(UsageLog.model_alias)
        result = await self._session.execute(stmt)
        return {row.model_alias: row.total_cost or Decimal("0") for row in result}

    async def count_requests_by_model(
        self, period: str, scope: ROIScope, scope_id: uuid.UUID | None, client: str | None = None,
        scope_ids: list[uuid.UUID] | None = None,
        cost_where: ColumnElement | None = None,
    ) -> dict[str, int]:
        """Returns {model_alias: request_count} for the given period/scope.

        sum_usage_by_model 과 **같은** WHERE(_apply_scope_filter + _apply_client_filter)를
        쓴다 — 비용과 요청수가 다른 모집단에서 나오면 Analytics export 의 두 열이 서로
        안 맞는다. (sum_usage_by_model 시그니처는 건드리지 않는다 —
         scheduler/roi_aggregator.py:32,82 가 의존.)
        """
        stmt = select(
            UsageLog.model_alias,
            func.count().label("requests"),
        ).where(
            cost_where if cost_where is not None else cost_period_filter(period),  # §59 SUCCESS + KST
        )
        stmt = self._apply_scope_filter(stmt, scope, scope_id, scope_ids)
        stmt = self._apply_client_filter(stmt, client)
        stmt = stmt.group_by(UsageLog.model_alias)
        result = await self._session.execute(stmt)
        return {row.model_alias: int(row.requests or 0) for row in result}

    async def count_active_users(
        self, period: str, scope: ROIScope, scope_id: uuid.UUID | None, client: str | None = None,
        scope_ids: list[uuid.UUID] | None = None,
        cost_where: ColumnElement | None = None,
    ) -> int:
        stmt = select(func.count(distinct(UsageLog.user_id))).where(
            cost_where if cost_where is not None else cost_period_filter(period),  # §59 SUCCESS + KST
        )
        stmt = self._apply_scope_filter(stmt, scope, scope_id, scope_ids)
        stmt = self._apply_client_filter(stmt, client)
        result = await self._session.execute(stmt)
        return result.scalar_one() or 0

    async def total_requests(
        self, period: str, scope: ROIScope, scope_id: uuid.UUID | None, client: str | None = None,
        scope_ids: list[uuid.UUID] | None = None,
        cost_where: ColumnElement | None = None,
    ) -> int:
        stmt = select(func.count(UsageLog.id)).where(
            cost_where if cost_where is not None else cost_period_filter(period),  # §59 SUCCESS + KST
        )
        stmt = self._apply_scope_filter(stmt, scope, scope_id, scope_ids)
        stmt = self._apply_client_filter(stmt, client)
        result = await self._session.execute(stmt)
        return result.scalar_one() or 0

    async def total_tokens(
        self, period: str, scope: ROIScope, scope_id: uuid.UUID | None, client: str | None = None,
        scope_ids: list[uuid.UUID] | None = None,
        cost_where: ColumnElement | None = None,
    ) -> int:
        """모든 과금 버킷의 합. 캐시(생성/읽기)를 빼면 총 토큰이 과소보고된다
        (dev 실측 -29.2%). 이 값은 대시보드 KPI 와 BI 챗 어시스턴트가 같이 읽는다.
        reasoning_tokens 는 output_tokens 에 이미 포함(models/usage.py:61)이라 제외."""
        stmt = select(
            func.coalesce(func.sum(UsageLog.input_tokens), 0)
            + func.coalesce(func.sum(UsageLog.output_tokens), 0)
            + func.coalesce(func.sum(UsageLog.cache_creation_tokens), 0)
            + func.coalesce(func.sum(UsageLog.cache_read_tokens), 0)
        ).where(
            cost_where if cost_where is not None else cost_period_filter(period),  # §59 SUCCESS + KST
        )
        stmt = self._apply_scope_filter(stmt, scope, scope_id, scope_ids)
        stmt = self._apply_client_filter(stmt, client)
        result = await self._session.execute(stmt)
        return result.scalar_one() or 0

    async def token_bucket_totals(
        self, period: str, scope: ROIScope, scope_id: uuid.UUID | None, client: str | None = None,
        scope_ids: list[uuid.UUID] | None = None,
        cost_where: ColumnElement | None = None,
    ) -> dict[str, int]:
        """4개 과금 버킷 각각의 합 — Analytics 토큰 분석 패널용.

        total_tokens 와 **같은** WHERE/스코프/클라이언트 필터를 쓴다 — 패널의
        합이 KPI 카드의 총 토큰과 어긋나면 같은 화면에서 두 숫자가 싸운다.
        """
        stmt = select(
            func.coalesce(func.sum(UsageLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(UsageLog.cache_creation_tokens), 0).label("cache_write_tokens"),
            func.coalesce(func.sum(UsageLog.cache_read_tokens), 0).label("cache_read_tokens"),
        ).where(
            cost_where if cost_where is not None else cost_period_filter(period),  # §59 SUCCESS + KST
        )
        stmt = self._apply_scope_filter(stmt, scope, scope_id, scope_ids)
        stmt = self._apply_client_filter(stmt, client)
        row = (await self._session.execute(stmt)).one()
        return {
            "input_tokens": int(row.input_tokens or 0),
            "output_tokens": int(row.output_tokens or 0),
            "cache_write_tokens": int(row.cache_write_tokens or 0),
            "cache_read_tokens": int(row.cache_read_tokens or 0),
        }

    @staticmethod
    def _apply_client_filter(stmt, client: str | None):
        if (cf := client_filter(client)) is not None:
            stmt = stmt.where(cf)
        return stmt

    @staticmethod
    def _apply_scope_filter(
        stmt,
        scope: ROIScope,
        scope_id: uuid.UUID | None,
        scope_ids: list[uuid.UUID] | None = None,
    ):
        """scope 에 맞는 WHERE 를 덧붙인다. GLOBAL 만 필터 없음이다.

        ⚠️ 예전엔 각 분기가 `scope == ROIScope.TEAM and scope_id` 형태였다. scope_id 가
           None 이면 세 분기 모두 거짓이 되어 **아무 필터도 붙지 않은 채** 그대로 통과했다
           — 즉 "이 팀만" 이라고 요청한 질의가 조용히 전사 집계로 승격된다. 실제 도달
           경로가 있다: auth.users.team_id 는 nullable 이고(db/init/02_create_tables.sql:50)
           JWT 의 team_id 클레임이 없으면 CurrentUser.team_id 가 None 이 되므로
           (core/auth.py:157), 팀 없는 TEAM_LEADER 가 전사 분석/CSV export 를 받아 갔다.

           그래서 좁히지 못하는 상황에서는 넓은 결과를 주지 않고 **터진다**. 호출자는
           GLOBAL 을 원하면 GLOBAL 을 명시해야 한다.

           TEAM 은 scope_ids(복수 팀 집합)도 받는다 — auth.teams.leader_user_id 가
           여러 팀에서 같은 사용자를 가리킬 수 있어(한 사람이 복수 팀 리더)
           TEAM_LEADER 의 열람 범위는 단일 team_id 가 아니라 집합이다.
        """
        if scope == ROIScope.GLOBAL:
            return stmt  # 전사 집계 — 필터 없음이 의도된 유일한 경우

        if scope == ROIScope.TEAM:
            ids = scope_ids if scope_ids is not None else (
                [scope_id] if scope_id is not None else []
            )
            if not ids:
                raise ValueError(
                    f"{scope.value} scope 에 scope_id 가 없다 — 필터를 생략하면 전사 데이터가 "
                    f"나가므로 거부한다. 전사 집계가 목적이면 ROIScope.GLOBAL 을 넘길 것."
                )
            return stmt.where(UsageLog.team_id.in_(ids))

        if scope_id is None:
            raise ValueError(
                f"{scope.value} scope 에 scope_id 가 없다 — 필터를 생략하면 전사 데이터가 "
                f"나가므로 거부한다. 전사 집계가 목적이면 ROIScope.GLOBAL 을 넘길 것."
            )

        if scope == ROIScope.USER:
            return stmt.where(UsageLog.user_id == scope_id)
        if scope == ROIScope.DEPT:
            return stmt.where(UsageLog.dept_id == scope_id)

        # 새 ROIScope 가 추가됐는데 여기 분기를 안 넣은 경우 — 조용히 전사로 넓히지 않는다.
        raise ValueError(f"지원하지 않는 ROIScope: {scope!r}")
