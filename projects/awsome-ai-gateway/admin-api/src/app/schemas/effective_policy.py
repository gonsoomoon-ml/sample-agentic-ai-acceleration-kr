# Copyright 2026 © Amazon.com and Affiliates.
"""Effective Policy — 사용자에게 실제로 적용되는 정책의 합성 읽기 전용 뷰.

정책이 4개 축(user→app, user/team→model, model→app, 예산/한도)에 흩어져 있어
"이 사용자가 왜 X를 못 쓰지"를 답하려면 화면 여러 곳을 뒤져야 한다. 이 스키마는
그 합성 결과를 한 번에 내려준다.
"""
from __future__ import annotations

from pydantic import BaseModel


class EffectivePolicyCell(BaseModel):
    """client × model_alias 한 칸의 판정."""

    client: str
    model_alias: str
    allowed: bool
    # 거부된 축 목록. "user_app"(user_allowed_clients), "user_model"(user/team
    # allowed_models), "model_app"(model_aliases.allowed_clients) 중 0개 이상.
    blocked_by: list[str]


class EffectiveBudgetEntry(BaseModel):
    scope: str  # USER | TEAM
    client: str | None  # None = 총예산, 값 = 앱별 예산
    max_budget_usd: str
    used_usd: str | None  # 현재 리포팅 월 사용액 (행이 없으면 None)
    policy: str


class EffectiveRateLimitEntry(BaseModel):
    scope: str  # GLOBAL | TEAM | USER
    model_alias: str | None  # None = 전체 모델
    rpm_limit: int | None
    tpm_limit: int | None
    cpm_limit_usd: str | None
    cph_limit_usd: str | None


class EffectiveDowngradeRule(BaseModel):
    scope: str  # USER | TEAM
    threshold_pct: int
    from_model_alias: str
    to_model_alias: str


class EffectivePolicyResponse(BaseModel):
    user_id: str
    email: str | None
    team_id: str | None
    team_name: str | None

    # effective — user 행이 있으면 user, 없으면 team, 없으면 org, 셋 다 없으면 None.
    # ⚠️ 각 스코프에서 "행 0개 = 정책 없음(하위 폴백)" — 전면 거부를 표현할 수 없다.
    allowed_clients: list[str] | None
    allowed_clients_source: str  # "user" | "team" | "organization" | "none"
    # effective — user 행이 있으면 user, 없으면 team, 둘 다 없으면 None.
    allowed_models: list[str] | None
    allowed_models_source: str  # "user" | "team" | "none"

    web_search: dict[str, bool]  # client → enabled
    cells: list[EffectivePolicyCell]
    budgets: list[EffectiveBudgetEntry]
    rate_limits: list[EffectiveRateLimitEntry]
    downgrade_rules: list[EffectiveDowngradeRule]
