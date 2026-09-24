# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Regression: gateway-proxy 가 집계 기간(월/일)을 **UTC** 로 파생해, KST 로 버킷된
데이터·admin-api·자기 자신의 다른 절반과 9시간씩 어긋났던 결함.

이 자산의 집계 경계는 전부 KST 다:
  * ``daily_aggregates.date`` = ``DATE(requested_at AT TIME ZONE 'Asia/Seoul')``
    (cost-recorder-worker/src/worker/daily_aggregator.py:43), cron 도
    ``AsyncIOScheduler(timezone="Asia/Seoul")`` KST 00:10 (worker/main.py:93)
  * admin-api 는 조회·집계·ROI 쓰기 전부 KST (admin-api/src/app/core/usage_filters.py,
    scheduler/main.py:33 — DEVLOG §59)

gateway-proxy 만 ``datetime.now(tz=timezone.utc).strftime("%Y-%m")`` 였다(8곳).
pod TZ 가 UTC 라 결과적으로 UTC 월/일이 된다. 세 가지가 깨졌다:

  1. **예산이 새 달에 리셋되지 않는다.** ``budget:*:{period}`` 를 쓰는 쪽
     (services/cost_recorder.py)과 읽는 쪽(middleware/budget.py)이 둘 다 UTC 여서
     내부적으로는 일관됐지만, 경계가 KST 가 아니므로 매월 1일 KST 00:00~09:00 동안
     **지난달 카운터를 계속 쓴다** — 지난달 예산을 소진한 팀이 새 달 첫 9시간 동안
     차단된 채로 남는다(월말 마지막 9시간엔 반대로 조기 리셋).
  2. **서비스 간 불일치.** ``budget.budget_usages.period`` 행을 gateway 는 UTC 월로
     쓰고(cost_stream → batch_flusher) admin-api 는 KST 월로 읽는다
     (admin-api/src/app/routers/budgets.py:194) → 그 9시간 동안 팀 예산 화면이
     조회할 행 자체가 없어 $0 으로 보인다.
  3. **매일 9시간이 사라진다.** ``/v1/usage/me`` 는 ``date < 오늘`` DB 집계(KST 버킷) +
     오늘분 Redis 카운터로 합산한다. 오늘을 UTC 로 잡으면 KST 09:00 이전에 "오늘"이
     KST 어제가 되어, DB 는 KST 어제를 잘라내고 Redis 는 UTC 어제 키
     (=KST 어제 09:00~) 만 읽으므로 **KST 어제 00:00~09:00 이 양쪽 어디에도 없다.**

Fix: ``app/periods.py`` 하나로 모아 8곳(읽기·쓰기 모두)을 KST 로 통일.
⚠️ 읽는 쪽만 고치면 안 된다 — admin-api scheduler 주석이 같은 교훈을 남겼다
   (읽기만 KST 로 바꾸면 쓰는 쪽의 UTC 행을 못 찾아 9시간 "지표 0" 창이 생긴다).
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.periods import KST, current_kst_date, current_kst_period

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # regression -> tests -> gateway-proxy -> root
GATEWAY_SRC = PROJECT_ROOT / "gateway-proxy" / "src" / "app"
ADMIN_USAGE_FILTERS = (
    PROJECT_ROOT / "admin-api" / "src" / "app" / "core" / "usage_filters.py"
)


def _at(instant: datetime):
    """``app.periods`` 안의 ``datetime.now(tz)`` 만 고정 시각으로 바꾼다.

    실제 ``datetime.now(KST)`` 는 "그 순간의 절대시각을 KST 로 표현한 것" 이므로
    ``instant.astimezone(tz)`` 가 충실한 대역이다.
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102 - datetime.now 대역
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    return patch("app.periods.datetime", _FrozenDatetime)


# ──────────────────────────────────────────────────────────────────────────────
# 1) 경계표 — UTC 였다면 틀렸을 순간들
# ──────────────────────────────────────────────────────────────────────────────

# (UTC 절대시각, 기대 KST 월, 기대 KST 일자)
BOUNDARY_CASES = [
    # 9월이 KST 로 시작하는 순간. UTC 로는 아직 8/31 → 예전 구현은 "2026-08".
    (datetime(2026, 8, 31, 15, 0, 0, tzinfo=UTC), "2026-09", "2026-09-01"),
    # 그 1초 전 — 아직 KST 8월.
    (datetime(2026, 8, 31, 14, 59, 59, tzinfo=UTC), "2026-08", "2026-08-31"),
    # 9시간 창의 끝(KST 09:00) — 여기서부터는 UTC 도 같은 답을 낸다.
    (datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC), "2026-09", "2026-09-01"),
    # 창 한가운데(KST 04:30).
    (datetime(2026, 8, 31, 19, 30, 0, tzinfo=UTC), "2026-09", "2026-09-01"),
    # 연 경계 — 월만 넘기고 연을 안 넘기는 구현을 잡는다.
    (datetime(2026, 12, 31, 15, 0, 0, tzinfo=UTC), "2027-01", "2027-01-01"),
    # 한 자리 월의 zero-padding ('2026-1' 이 되면 키가 갈린다).
    (datetime(2025, 12, 31, 15, 0, 0, tzinfo=UTC), "2026-01", "2026-01-01"),
    # 월 중 — UTC 와 KST 가 일치하는 평시(전환이 무영향인 구간).
    (datetime(2026, 9, 15, 3, 0, 0, tzinfo=UTC), "2026-09", "2026-09-15"),
]


@pytest.mark.parametrize(("instant", "expected_period", "expected_date"), BOUNDARY_CASES)
def test_period_and_date_follow_kst_boundaries(
    instant: datetime, expected_period: str, expected_date: str
):
    with _at(instant):
        assert current_kst_period() == expected_period
        assert current_kst_date() == expected_date


def test_the_frozen_clock_helper_actually_works():
    """대조군 — 시계를 못 고정하면 위 경계표 전체가 '지금'을 보고 있을 뿐이다."""
    far = datetime(2031, 3, 4, 15, 0, 0, tzinfo=UTC)
    with _at(far):
        assert current_kst_period() == "2031-03"
    # 패치를 벗으면 실제 현재로 돌아와야 한다(패치가 전역에 새지 않는지).
    assert current_kst_period() != "2031-03"


def test_the_boundary_table_contains_cases_that_utc_gets_wrong():
    """대조군 — 예전 구현(UTC)과 답이 갈리는 순간이 표에 실제로 들어있어야 한다.

    이게 없으면 경계표가 UTC/KST 가 우연히 일치하는 순간들로만 채워져 있어도
    통과한다 — 즉 결함을 재현하지 못하는 표가 된다.
    """
    diverging = [
        c for c in BOUNDARY_CASES if c[0].strftime("%Y-%m") != c[1]
    ]
    assert len(diverging) >= 3, (
        f"UTC 와 답이 갈리는 케이스가 {len(diverging)}개뿐이다 — 이 표는 결함을 "
        f"재현하지 못한다"
    )
    # 일자 쪽도 마찬가지.
    diverging_dates = [
        c for c in BOUNDARY_CASES if c[0].strftime("%Y-%m-%d") != c[2]
    ]
    assert len(diverging_dates) >= 3, f"일자 경계 케이스 부족: {len(diverging_dates)}"


@pytest.mark.parametrize("instant", [c[0] for c in BOUNDARY_CASES])
def test_month_and_day_never_disagree(instant: datetime):
    """``/v1/usage/me`` 는 period(월 하한)와 today(일 상한)를 같이 쓴다.

    둘이 다른 타임존에서 오면 월 첫날에 하한 > 상한 이 되어 DB 구간이 비어버린다.
    """
    with _at(instant):
        assert current_kst_date().startswith(current_kst_period() + "-")


# ──────────────────────────────────────────────────────────────────────────────
# 2) 서비스 간 계약 — admin-api 와 **같은 값**이어야 한다
# ──────────────────────────────────────────────────────────────────────────────


def _load_admin_kst_period():
    """admin-api 의 current_kst_period 를 AST 로 떼어내 실행 가능한 함수로.

    admin-api 는 gateway-proxy 의 sys.path 에 없고(별도 venv/패키지 루트) 모듈째
    import 하면 sqlalchemy 모델까지 끌고 온다. 함수 하나만 컴파일해 실행한다.
    (test_high_redis_url_credential_leak.py 와 같은 idiom.)

    admin 쪽 구현은 ``_reporting_tz()`` (REPORTING_TIMEZONE 설정형)을 거치므로,
    그 의존을 네임스페이스에 주입한다 — 대조 목적은 "같은 리포팅 타임존 아래 같은
    값" 이라 Seoul 로 고정한다.
    """
    source = ADMIN_USAGE_FILTERS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "current_kst_period":
            module = ast.Module(body=[node], type_ignores=[])
            ns: dict = {
                "datetime": datetime,
                "KST": timezone(timedelta(hours=9)),
                "_reporting_tz": lambda: ZoneInfo("Asia/Seoul"),
            }
            exec(compile(module, str(ADMIN_USAGE_FILTERS), "exec"), ns)  # noqa: S102
            return ns["current_kst_period"], ns
    pytest.fail(
        f"{ADMIN_USAGE_FILTERS} 에서 current_kst_period 를 찾지 못했다 — "
        f"admin-api 쪽 규칙이 바뀌었다면 gateway 도 같이 봐야 한다"
    )


def test_admin_api_source_is_present():
    """대조군 — 상대 경로가 틀리면 아래 대조가 공허해진다."""
    assert ADMIN_USAGE_FILTERS.exists(), f"admin-api 소스가 없다: {ADMIN_USAGE_FILTERS}"


def test_gateway_derives_periods_from_the_configured_reporting_timezone():
    """계약의 본체 — gateway 도 REPORTING_TIMEZONE 을 읽어야 한다.

    admin-api·cost-recorder-worker·notification-worker 는 전부 설정형으로 이관됐는데
    gateway 만 KST 하드코딩으로 남으면, 리포팅 타임존을 Seoul 이 아닌 값으로 둔
    배포(예: dev=Asia/Kolkata)에서 월 경계 몇 시간 동안 서로 다른 period 행/키를
    읽고 쓴다 — 이 파일이 고정한 결함 #2 의 재발이다. 값 대조가 아니라 **파생
    경로**를 못박는다.
    """
    src = (GATEWAY_SRC / "periods.py").read_text(encoding="utf-8")
    assert "reporting_timezone" in src, (
        "periods.py 가 reporting_timezone 설정을 읽지 않는다 — KST 하드코딩으로 "
        "되돌아갔다"
    )
    assert "ZoneInfo(" in src, (
        "고정 오프셋으로 되돌리면 DST 있는 리포팅 타임존에서 경계가 틀어진다"
    )


def test_gateway_and_admin_api_agree_on_the_kst_month():
    """같은 ``budget_usages`` 행과 ``budget:*`` 키를 두 서비스가 읽고 쓴다.

    한쪽만 경계가 바뀌면 그 순간부터 조회 대상 행이 사라진다(결함 #2).
    """
    admin_fn, admin_ns = _load_admin_kst_period()

    # admin 쪽 KST 상수가 정말 UTC+9 인지 — 우리가 넣어준 값이 아니라 소스에서 확인.
    admin_src = ADMIN_USAGE_FILTERS.read_text(encoding="utf-8")
    assert "timedelta(hours=9)" in admin_src, "admin-api 의 KST 오프셋 정의를 못 찾았다"
    assert KST.utcoffset(None) == timedelta(hours=9)

    checked = 0
    for instant, expected_period, _ in BOUNDARY_CASES:
        class _Frozen(datetime):
            _now = instant

            @classmethod
            def now(cls, tz=None):
                return cls._now.astimezone(tz) if tz else cls._now.replace(tzinfo=None)

        admin_ns["datetime"] = _Frozen
        with _at(instant):
            gateway_value = current_kst_period()
        admin_value = admin_fn()
        assert gateway_value == admin_value == expected_period, (
            f"{instant.isoformat()} 에서 gateway={gateway_value!r} "
            f"admin-api={admin_value!r} — 같은 budget_usages 행을 못 찾는다"
        )
        checked += 1

    assert checked == len(BOUNDARY_CASES), "대조한 케이스가 없다"


# ──────────────────────────────────────────────────────────────────────────────
# 3) 재발 방지 — UTC 로 월/일을 파생하는 코드가 다시 들어오지 못하게
# ──────────────────────────────────────────────────────────────────────────────

# 이 파일들이 period/date 를 파생한다. 새 파생 지점이 생기면 여기에 추가할 것 —
# 목록에 없으면 아래 가드가 그 파일을 보지 않는다.
PERIOD_DERIVING_FILES = [
    GATEWAY_SRC / "routers" / "usage.py",
    GATEWAY_SRC / "middleware" / "budget.py",
    GATEWAY_SRC / "schemas" / "cost_stream.py",
    GATEWAY_SRC / "services" / "cost_recorder.py",
]


def test_period_deriving_files_all_exist():
    """대조군 — 경로가 바뀌면 아래 가드가 아무것도 검사하지 않는다."""
    missing = [str(p) for p in PERIOD_DERIVING_FILES if not p.exists()]
    assert not missing, f"경로가 바뀌었다: {missing}"


def test_no_source_file_derives_a_period_from_utc():
    """``strftime("%Y-%m…")`` 를 UTC ``now()`` 에 붙이는 코드가 없어야 한다.

    AST 로 ``.strftime(...)`` 호출을 찾아 **수신자 표현식**을 본다 — 주석·docstring 에
    남아 있는 예전 코드 인용(app/periods.py 의 설명 등)에는 속지 않는다.
    """
    offenders: list[str] = []
    strftime_calls = 0

    for path in PERIOD_DERIVING_FILES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "strftime":
                continue
            fmt = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
            if not str(fmt).startswith("%Y-%m"):
                continue
            strftime_calls += 1
            receiver = ast.get_source_segment(source, node.func.value) or ""
            # UTC 명시 또는 인자 없는 now()(= 프로세스 로컬 TZ, pod 에서는 UTC).
            is_utc = re.search(r"\b(utc|UTC)\b", receiver) is not None
            is_naive_now = re.fullmatch(r"\s*datetime\.now\(\s*\)\s*", receiver) is not None
            if is_utc or is_naive_now:
                offenders.append(f"{path.name}: {receiver}.strftime({fmt!r})")

    assert not offenders, (
        f"UTC(또는 프로세스 로컬 TZ)로 period/date 를 파생하는 코드가 있다 — "
        f"KST 로 버킷된 데이터와 9시간 어긋난다. app.periods 를 쓸 것: {offenders}"
    )


def test_every_period_site_uses_the_shared_helper():
    """4개 파일 전부가 ``app.periods`` 를 import 해야 한다.

    위 테스트는 '나쁜 코드가 없다' 만 본다 — 파생 코드를 통째로 지워도 통과한다.
    쓰는 쪽/읽는 쪽이 **같은** 헬퍼를 쓰는 게 이 수정의 본체이므로 따로 못박는다.
    """
    for path in PERIOD_DERIVING_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.periods"
            for alias in node.names
        }
        assert imported, (
            f"{path.name} 이 app.periods 를 import 하지 않는다 — 자체 파생으로 "
            f"되돌아갔을 수 있다"
        )
        assert imported <= {"current_kst_period", "current_kst_date", "KST"}, (
            f"{path.name} 이 app.periods 에서 예상 외 이름을 가져온다: {imported}"
        )


def test_usage_me_uses_one_clock_for_the_redis_key_and_the_db_bound():
    """결함 #3 의 구조적 재발 방지.

    ``today`` 하나에서 Redis 키와 DB 상한(``today_date``)이 **둘 다** 나와야 한다.
    각자 따로 ``now()`` 를 부르면 다시 갈라질 수 있다.
    """
    source = (GATEWAY_SRC / "routers" / "usage.py").read_text(encoding="utf-8")
    assert "today = current_kst_date()" in source, "today 파생이 바뀌었다"
    assert "date_cls.fromisoformat(today)" in source, (
        "DB 상한이 today 에서 파생되지 않는다 — Redis 키와 갈라질 수 있다"
    )
