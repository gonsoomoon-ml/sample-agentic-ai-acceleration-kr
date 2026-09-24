# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Regression: `GET /admin/dashboard/kpi` — 계약과 예산 산식.

## 왜 이 엔드포인트가 생겼나

대시보드 상단 카드를 그리려고 프론트가 4개 API 를 동시에 불렀고, 그중
`GET /admin/budgets/summary` 가 병목이었다. 그 핸들러는 활성 예산 config 를 훑으며
**config 하나당 Redis GET + usage_logs SUM 을 순차로** 수행한다
(`budget_service.get_budget_summary` → `_resolve_used`). 사용자가 많아지면 라운드트립이
그만큼 쌓인다. 카드에 필요한 건 합계 몇 개뿐이라 SQL 집계 한 번으로 대체했다.

## 이 파일이 못 박는 것

1. **예산 한도(분모)는 TEAM + 팀 없는 USER 만 더한다.** 팀에 속한 USER 예산을 함께 더하면
   같은 한도를 팀 축과 개인 축에서 이중계상한다. 참조 구현은 활성 config 전체를 naive
   SUM 해서 그 함정에 빠졌다 — 실측으로 1500 이어야 하는 값이 2100 이 됐다.
2. **사용액(분자)은 usage_logs 의 SUCCESS 합계다.** `budget.budget_usages` 는
   cost-recorder-worker 가 status 구분 없이 누적하는 monotonic accumulator 라
   ERROR/TIMEOUT 비용이 섞이고 사후 정정도 없다. 같은 화면의 다른 카드가 전부 §59
   기준(SUCCESS only)이므로 여기서만 다른 소스를 쓰면 화면 안에서 숫자가 안 맞는다.
3. **한도 0 이면 사용률은 `null`** — 0% 가 아니다. 0% 는 "예산을 안 썼다" 는 사실 진술이다.
4. **참조 구현이 밟은 두 지뢰를 다시 밟지 않는다**: 존재하지 않는 `app_group` 컬럼 참조,
   존재하지 않는 `app.models.key` 모듈 임포트.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


from app.routers import dashboard as dash

SOURCE = Path(dash.__file__)


# ──────────────────────────────────────────────────────────────────────────────
# 0) 공허성 대조군 — 엔드포인트가 실재하는가
# ──────────────────────────────────────────────────────────────────────────────


def test_the_kpi_route_exists_and_scopes_the_cache_key():
    paths = {r.path for r in dash.router.routes}
    assert "/admin/dashboard/kpi" in paths, f"등록된 경로: {sorted(paths)}"
    src = inspect.getsource(dash.dashboard_kpi)
    assert "require_admin_or_team_leader" in src, "관리자/팀 리더 전용이 아니다"
    # TEAM_LEADER 에 연 엔드포인트는 캐시 키에 유효 scope 가 없으면 ADMIN 이 채운
    # 전사 응답이 리더에게 새어 나간다 — scope= 를 키에 넣는 게 유출 방지 조건이다.
    assert "scope=" in src, "캐시 키에 유효 scope 가 없다 — TEAM_LEADER 유출 경로"


def test_kpi_returns_every_card_field():
    """프론트 카드 8개가 요구하는 키 집합 — 하나라도 빠지면 그 카드가 '—' 로 굳는다."""
    src = inspect.getsource(dash.dashboard_kpi)
    for key in (
        "total_requests", "total_tokens", "total_cost_usd", "active_users",
        "cost_per_user_usd", "budget_used_usd", "budget_limit_usd",
        "budget_utilization_pct", "active_keys", "active_models",
    ):
        assert f'"{key}"' in src, f"응답에 {key} 가 없다"


# ──────────────────────────────────────────────────────────────────────────────
# 1) 예산 산식 — 이중계상 회피
# ──────────────────────────────────────────────────────────────────────────────


def test_budget_denominator_excludes_team_member_user_configs():
    """⚠️ 핵심 — 한도 합계가 TEAM + 팀 없는 USER 로 제한돼야 한다.

    AST 로 WHERE 조건을 확인한다. 문자열 grep 이면 주석에 적힌 설명만 보고 통과할 수 있다.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "dashboard_kpi"
    )
    body = ast.unparse(fn)
    # 활성 config 만
    assert "BudgetConfig.is_active" in body, "비활성 config 를 걸러내지 않는다"
    # TEAM 이거나 (USER 이면서 team_id IS NULL)
    assert "BudgetScope.TEAM" in body, "TEAM scope 조건이 없다"
    assert "BudgetScope.USER" in body, "USER scope 조건이 없다"
    assert "User.team_id.is_(None)" in body, (
        "팀 없는 USER 조건이 없다 — 팀 멤버 개인예산까지 더해 이중계상된다"
    )
    assert "or_(" in body, "TEAM 또는 팀없는USER 의 OR 결합이 없다"


def test_budget_numerator_does_not_read_budget_usages():
    """분자는 usage_logs SUCCESS 합계여야 한다 — budget_usages 는 status 무관 누적이다."""
    body = inspect.getsource(dash.dashboard_kpi)
    assert "BudgetUsage" not in body, (
        "budget_usages 에서 사용액을 읽는다 — ERROR/TIMEOUT 비용이 섞여 §59(SUCCESS only) "
        "기준인 같은 화면의 다른 카드와 숫자가 어긋난다"
    )
    assert "cost_period_filter" in body, (
        "cost_period_filter(SUCCESS + KST 월)를 쓰지 않는다 — 집계 기준이 갈린다"
    )


def test_zero_limit_yields_null_utilization_not_zero_percent():
    """한도 0 → null. 0% 는 '예산을 안 썼다' 는 거짓 사실이다."""
    body = inspect.getsource(dash.dashboard_kpi)
    assert "if total_limit > 0 else None" in body, (
        "한도 0 에서 사용률을 0 으로 접고 있다"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2) 참조 구현의 지뢰 2종
# ──────────────────────────────────────────────────────────────────────────────


def test_no_reference_to_a_nonexistent_app_group_column():
    """참조 구현 HEAD 의 SQL 은 `bc.app_group` 을 참조해 이 스키마에서 즉시 UndefinedColumn 이다."""
    src = SOURCE.read_text(encoding="utf-8")
    assert "app_group" not in src, (
        "app_group 컬럼을 참조한다 — 이 스키마에 없는 컬럼이라 /kpi 가 매 요청 500 이 된다"
    )


def test_key_and_model_symbols_come_from_the_modules_that_exist():
    """참조 구현은 `app.models.key` 를 임포트했고 그 모듈은 여기 없다(auth.py 에 있다)."""
    src = SOURCE.read_text(encoding="utf-8")
    assert "from app.models.key import" not in src, "존재하지 않는 모듈을 임포트한다"
    # 실제로 해석되는지 — 임포트 실패는 라우터 전체를 죽인다
    from app.models.auth import KeyStatus, VirtualKey  # noqa: F401
    from app.models.budget import BudgetConfig, BudgetScope  # noqa: F401
    from app.models.model import ModelAlias, ModelStatus  # noqa: F401


def test_period_default_goes_through_the_shared_kst_helper():
    """6개 핸들러가 한 기본 period 를 공유하고, 그 구현은 단일 진실원을 거쳐야 한다."""
    body = inspect.getsource(dash.dashboard_kpi)
    assert "_default_period()" in body, "공유 기본 period 헬퍼를 쓰지 않는다"
    src = SOURCE.read_text(encoding="utf-8")
    # _default_period 는 모듈 레벨 공유 헬퍼인데, 그 구현이 usage_filters 의
    # current_kst_period(내부는 reporting_timezone)를 거치지 않고 따로 파생하면
    # 나머지 라우터와 월 정의가 갈라진다.
    assert "current_kst_period" in src, (
        "_default_period 가 단일 진실원(current_kst_period)을 거치지 않는다"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 3) 캐시 — 다른 대시보드 핸들러와 같은 규약
# ──────────────────────────────────────────────────────────────────────────────


def test_kpi_uses_the_shared_cache_helpers_with_all_params():
    body = inspect.getsource(dash.dashboard_kpi)
    assert "_cache_key(" in body and "_cache_get(" in body and "_cache_set(" in body, (
        "캐시를 쓰지 않는다 — 이 엔드포인트가 병목 해소의 핵심이다"
    )
    assert "period=period" in body and "client=client" in body, (
        "응답을 바꾸는 파라미터가 캐시 키에 다 들어가지 않았다"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 4) 예산 요약이 사용자를 잘라먹지 않는지
# ──────────────────────────────────────────────────────────────────────────────
#
# `/kpi` 가 대시보드 카드를 SQL 집계로 대체했지만, `/admin/budgets/summary` 자체는
# 예산 관리 화면(admin-ui/src/app/budgets/page.tsx)과 TopSpend 보강에서 계속 쓰인다.
# 그 핸들러가 `list_users(limit=500)` 이었다 — created_at desc 로 정렬한 뒤 앞에서
# 자르므로 **가입이 오래된 사용자의 예산 행이 조용히 빠진다**(오류 없이 틀린 사용률).


def test_budget_summary_does_not_truncate_the_user_list():
    """⚠️ limit 이 붙은 사용자 조회로 예산 요약을 만들면 안 된다."""
    import inspect

    from app.services.budget_service import BudgetService

    src = inspect.getsource(BudgetService.get_budget_summary)
    assert "iter_all_users()" in src, (
        "예산 요약이 전수 사용자 조회를 쓰지 않는다 — limit 로 자르면 그 뒤 사용자의 "
        "예산 행이 사라진 채 사용률이 계산된다"
    )
    assert "list_users(" not in src, (
        f"limit 이 붙는 list_users 를 아직 쓴다:\n{src[:300]}"
    )


def test_cursor_pagination_is_not_used_as_the_workaround():
    """커서 페이징으로 우회하는 것도 금지 — 정렬 키와 커서 키가 다르다.

    `list_users` 는 `order_by(created_at desc)` 인데 커서 조건이 `User.id < cursor` 다.
    id 순서와 created_at 순서는 무관하므로 그 조합은 행을 건너뛰거나 같은 페이지를
    반복한다. 이 테스트는 그 함정으로 '고쳤다' 고 착각하는 것을 막는다.
    """
    import inspect

    from app.repositories.user_repository import UserRepository
    from app.services.budget_service import BudgetService

    # 전제 확인 — 정렬 키와 커서 키가 실제로 다른가(다르면 커서 사용 금지가 정당하다)
    lu = inspect.getsource(UserRepository.list_users)
    assert "order_by(User.created_at.desc())" in lu, "정렬 키가 바뀌었다 — 이 가드 재검토"
    assert "User.id < cursor" in lu, "커서 키가 바뀌었다 — 이 가드 재검토"

    src = inspect.getsource(BudgetService.get_budget_summary)
    assert "cursor=" not in src, (
        "예산 요약이 커서 페이징을 쓴다 — created_at 정렬에 id 커서를 섞으면 행이 누락된다"
    )


def test_iter_all_users_has_no_limit_clause():
    """전수 조회가 실제로 limit 없이 나가는지 — 이름만 바뀐 게 아니어야 한다."""
    import inspect

    from app.repositories.user_repository import UserRepository

    src = inspect.getsource(UserRepository.iter_all_users)
    assert ".limit(" not in src, f"limit 이 남아 있다:\n{src}"
    assert "order_by(User.created_at.desc())" in src, "정렬이 없다 — 결과 순서가 불안정하다"
