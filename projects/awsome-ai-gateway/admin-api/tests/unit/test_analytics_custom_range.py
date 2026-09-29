# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
"""Analytics '직접 입력(custom)' 날짜 구간 지원.

배경 — 예전엔 UI 가 start/end 를 받아도 백엔드는 period(YYYY-MM)만 봤다.
resolveMonth 가 start 의 월로 환산해 보내서, 종료일은 무시되고 시작일이 다른 달이면
"전부 0" 이 조용히 나왔다. 이제 start_date+end_date 가 둘 다 오면 백엔드는
cost_date_range_filter(일자 구간, SUCCESS)로 집계한다.

못박는 것:
  1. 구간 필터가 sargable(컬럼에 함수를 씌우지 않는다 — 인덱스를 탄다)이고 SUCCESS 전용.
  2. end_day 는 포함 — UTC 경계는 (end_day+1) 00:00 KST 다.
  3. start/end 한쪽만 오면 400 — 조용한 월 fallback 은 무증상 오답의 재탕이다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.core import usage_filters as uf
from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.models.usage import UsageLog
from app.services.analytics_service import AnalyticsService


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    get_settings.cache_clear()


def _utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


@pytest.mark.unit
def test_cost_date_range_filter_is_sargable_and_success_only():
    stmt = (
        select(func.count())
        .select_from(UsageLog)
        .where(uf.cost_date_range_filter("2026-09-01", "2026-09-22"))
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    # 컬럼에 함수를 씌운 non-sargable 형태는 금지 — cost_period_filter 와 같은 이유.
    assert "to_char" not in sql.lower()
    assert "timezone(" not in sql.lower()
    assert "requested_at >=" in sql and "requested_at <" in sql
    assert "status" in sql


@pytest.mark.unit
def test_cost_date_range_filter_end_day_is_inclusive():
    """[start, end] 의 end 는 그 날 하루 전체 — UTC 경계는 end+1일 00:00 KST 다.

    KST 9/1 00:00 = UTC 8/31 15:00, KST 9/23 00:00 = UTC 9/22 15:00.
    """
    # 필터 내부 경계 계산을 직접 검증(컴파일된 바인드 값이 아니라 의미를 본다).
    start_utc, end_utc = uf.day_range_to_utc("2026-09-01", "2026-09-22")
    assert (start_utc, end_utc) == (_utc("2026-08-31T15:00Z"), _utc("2026-09-22T15:00Z"))


@pytest.mark.unit
async def test_get_analytics_rejects_partial_custom_range(mock_session, admin_user):
    """start/end 한쪽만 오면 400 — 조용히 월로 fallback 하면 '날짜가 안 먹는' 버그의 재탕."""
    svc = AnalyticsService()
    with pytest.raises(ValidationError):
        await svc.get_analytics(
            mock_session, period="2026-09", actor=admin_user, start_date="2026-09-01"
        )
    with pytest.raises(ValidationError):
        await svc.get_analytics(
            mock_session, period="2026-09", actor=admin_user, end_date="2026-09-30"
        )
    mock_session.execute.assert_not_called()


@pytest.mark.unit
async def test_get_analytics_rejects_invalid_custom_range(mock_session, admin_user):
    svc = AnalyticsService()
    # end < start
    with pytest.raises(ValidationError):
        await svc.get_analytics(
            mock_session, period="2026-09", actor=admin_user,
            start_date="2026-09-30", end_date="2026-09-01",
        )
    # 달력에 없는 날짜
    with pytest.raises(ValidationError):
        await svc.get_analytics(
            mock_session, period="2026-09", actor=admin_user,
            start_date="2026-06-31", end_date="2026-09-01",
        )
    mock_session.execute.assert_not_called()
