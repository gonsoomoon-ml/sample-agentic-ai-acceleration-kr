# Copyright 2026 © Amazon.com and Affiliates
"""REPORTING_TIMEZONE 이 월 경계 계산에 실제로 반영되는지.

기본값(Asia/Seoul)은 종전 KST 동작 그대로여야 하고, 한국 밖 배포(US·India)는
env 하나로 경계가 따라와야 한다. DST 가 있는 리전(America/Los_Angeles)은 3월·11월에
UTC 오프셋이 바뀌므로(PST -8 / PDT -7) 고정 오프셋 구현이면 경계가 1시간 틀어진다 —
기대값은 ZoneInfo 로 계산하지 않고 독립적으로 적어 둔다(동어반복 방지).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import usage_filters as uf
from app.core.config import get_settings


@pytest.fixture
def reporting_tz(monkeypatch):
    """REPORTING_TIMEZONE 을 바꾸고 lru_cache 된 Settings 를 비운다. None = 기본값."""

    def _set(tz: str | None) -> None:
        if tz is None:
            monkeypatch.delenv("REPORTING_TIMEZONE", raising=False)
        else:
            monkeypatch.setenv("REPORTING_TIMEZONE", tz)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tz", "period", "start", "end"),
    [
        # 기본값 = Asia/Seoul: 종전 KST 동작 그대로 (8/1 00:00 KST = 7/31 15:00Z)
        (None, "2026-08", "2026-07-31T15:00Z", "2026-08-31T15:00Z"),
        ("Asia/Seoul", "2026-08", "2026-07-31T15:00Z", "2026-08-31T15:00Z"),
        # LA 3월: 월초는 PST(-8), 월말 경계는 DST 시작(3/8) 이후라 PDT(-7)
        ("America/Los_Angeles", "2026-03", "2026-03-01T08:00Z", "2026-04-01T07:00Z"),
        # LA 11월: 11/1 00:00 은 아직 PDT(전환은 02:00), 12/1 은 PST
        ("America/Los_Angeles", "2026-11", "2026-11-01T07:00Z", "2026-12-01T08:00Z"),
        # India: 반시간 오프셋(+5:30)
        ("Asia/Kolkata", "2026-08", "2026-07-31T18:30Z", "2026-08-31T18:30Z"),
        # 연 경계(12월 → 다음 해 1월)
        ("UTC", "2026-12", "2026-12-01T00:00Z", "2027-01-01T00:00Z"),
    ],
)
def test_period_to_utc_range_follows_reporting_timezone(reporting_tz, tz, period, start, end):
    reporting_tz(tz)
    got_start, got_end = uf.period_to_utc_range(period)
    assert (got_start, got_end) == (_utc(start), _utc(end))
    # 반환값은 항상 UTC aware — DB 파라미터로 그대로 바인딩되는 전제
    assert got_start.utcoffset() == timedelta(0) and got_end.utcoffset() == timedelta(0)


@pytest.mark.unit
def test_month_expr_and_current_period_follow_reporting_timezone(reporting_tz):
    reporting_tz("America/Los_Angeles")
    sql = str(uf.kst_month_expr().compile(compile_kwargs={"literal_binds": True}))
    assert "America/Los_Angeles" in sql and "Asia/Seoul" not in sql
    assert uf.current_kst_period() == datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m")


@pytest.mark.unit
def test_default_is_seoul_when_env_absent(reporting_tz):
    reporting_tz(None)
    assert uf.reporting_timezone() == "Asia/Seoul"
    assert "Asia/Seoul" in str(uf.kst_month_expr().compile(compile_kwargs={"literal_binds": True}))
