# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
"""Regression: 팀 없는 TEAM_LEADER 가 전사 분석·CSV export 를 받아 가던 권한 누출.

결함:
  AnalyticsService.get_analytics 는 scope="all" 일 때 TEAM_LEADER 를 자기 팀으로
  좁혔다 — `roi_scope = TEAM; scope_id = actor.team_id`. 그런데 actor.team_id 는
  None 일 수 있고(auth.users.team_id 는 nullable, db/init/02_create_tables.sql:50;
  JWT 에 team_id 클레임이 없으면 CurrentUser.team_id 는 None, core/auth.py:157),
  격리를 거는 세 자리가 모두 scope_id 의 참/거짓에 걸려 있었다:

    * AnalyticsRepository._apply_scope_filter: `if scope == TEAM and scope_id:`
      → scope_id 가 None 이면 세 분기가 다 거짓 → **WHERE 없이 통과** = 전사 집계
    * get_analytics 의 by_user: `if scope_id is not None:`
    * get_analytics 의 trends:  `if scope_id is not None:`

  결과: 비용/요청/토큰/활성사용자 KPI, 사용자별 상위 50명(display_name + email),
  일별 추이가 전부 전사 데이터로 나갔다. /admin/analytics/export 는 같은 함수를
  거치므로 그 전사 데이터가 CSV/JSON 파일로 그대로 내려갔다.

  도달 경로가 가설이 아니다: dev 토큰은 role 을 본문에서 읽고 team_id 를 항상
  None 으로 만들기 때문에(core/auth.py:124-130) `dev.{"role":"TEAM_LEADER"}`
  하나로 재현되고, 운영에서는 팀 미배정 상태의 TEAM_LEADER 행이면 그대로 성립한다.

수정 방향은 "좁힐 수 없으면 넓은 결과를 주지 않는다"(fail closed):
  repo 는 비-GLOBAL scope 에 scope_id 가 없으면 ValueError 로 터지고, 서비스는 그
  전에 403 ForbiddenError 로 사람이 읽을 수 있는 이유를 준다.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select

from app.core.auth import CurrentUser
from app.core.exceptions import ForbiddenError
from app.models.auth import UserRole
from app.models.usage import ROIScope, UsageLog
from app.repositories.analytics_repository import AnalyticsRepository
from app.services.analytics_service import AnalyticsService

TEAM_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
TEAM_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


def _base_stmt():
    return select(func.count(UsageLog.id))


def _sql(stmt) -> str:
    return str(stmt)


# ──────────────────────────────────────────────────────────────────────────────
# 1) repo 계층 — 좁힐 수 없으면 터진다
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scope", [ROIScope.TEAM, ROIScope.USER, ROIScope.DEPT])
def test_non_global_scope_without_an_id_refuses_instead_of_widening(scope: ROIScope):
    """scope_id 없는 비-GLOBAL scope 는 거부한다 — 예전엔 필터 없이 통과했다."""
    with pytest.raises(ValueError) as exc:
        AnalyticsRepository._apply_scope_filter(_base_stmt(), scope, None)
    # 왜 터졌는지 로그에서 바로 읽히게.
    assert scope.value in str(exc.value)


@pytest.mark.parametrize(
    ("scope", "column"),
    [
        (ROIScope.TEAM, "team_id"),
        (ROIScope.USER, "user_id"),
        (ROIScope.DEPT, "dept_id"),
    ],
)
def test_scope_with_an_id_actually_narrows(scope: ROIScope, column: str):
    """대조군 — 격리가 정말 WHERE 로 붙는지. 이게 없으면 위 단정이 공허해진다.

    "터진다" 만 확인하면, 모든 분기가 무조건 터지도록 망가진 구현도 통과한다.
    """
    stmt = AnalyticsRepository._apply_scope_filter(_base_stmt(), scope, TEAM_A)
    sql = _sql(stmt)
    assert "WHERE" in sql, f"WHERE 절이 없다 — 격리가 붙지 않았다: {sql}"
    assert f"usage_logs.{column}" in sql, f"{column} 조건이 없다: {sql}"


def test_global_scope_is_still_unfiltered():
    """GLOBAL 은 필터 없음이 **의도**다 — 여기까지 막으면 전사 대시보드/ROI 집계가 죽는다.

    scheduler/roi_aggregator.py:32 가 (GLOBAL, None) 로 호출한다.
    """
    stmt = AnalyticsRepository._apply_scope_filter(_base_stmt(), ROIScope.GLOBAL, None)
    assert _sql(stmt) == _sql(_base_stmt()), "GLOBAL 에 조건이 덧붙었다"


# ──────────────────────────────────────────────────────────────────────────────
# 2) 서비스 계층 — 403 으로 먼저 막고, DB 에 질의조차 하지 않는다
# ──────────────────────────────────────────────────────────────────────────────


def _leader(team_id: uuid.UUID | None) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        email="leader@test.com",
        role=UserRole.TEAM_LEADER,
        team_id=team_id,
    )


def _admin(team_id: uuid.UUID | None = None) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        email="admin@test.com",
        role=UserRole.ADMIN,
        team_id=team_id,
    )


def _recording_session(led: list[uuid.UUID] | None = None) -> tuple[AsyncMock, list[str]]:
    """실행된 모든 statement 의 SQL 을 모아 두는 세션. 실제 DB 는 건드리지 않는다.

    `led` = auth.teams.leader_user_id 조회가 돌려줄 팀 id 목록 — TEAM_LEADER 의
    격리 집합은 이 조회로만 만들어진다(엄격 정책: 소속은 범위에 포함하지 않는다).
    """
    led = led or []
    seen: list[str] = []

    def _empty_result():
        result = MagicMock()
        result.scalar_one.return_value = 0
        result.scalar.return_value = 0
        result.all.return_value = []
        result.first.return_value = None
        result.__iter__ = MagicMock(return_value=iter([]))
        scalars = MagicMock()
        scalars.all.return_value = []
        result.scalars.return_value = scalars
        return result

    def _led_result():
        result = _empty_result()
        scalars = MagicMock()
        scalars.all.return_value = list(led)
        result.scalars.return_value = scalars
        return result

    async def _execute(stmt, *args, **kwargs):
        # literal 바인드로 렌더한다 — IN 집합에 어떤 team_id 가 들어갔는지(TEAM_A 만인지,
        # TEAM_B 도 섞였는지)를 SQL 문자열로 검증하려면 POSTCOMPILE 플레이스홀더가 아니라
        # 실제 값이 필요하다. 렌더 실패 시엔 평문으로 둔다(검증은 호출자의 몫).
        try:
            text = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            text = str(stmt)
        seen.append(text)
        if "leader_user_id" in text:
            return _led_result()
        return _empty_result()

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    return session, seen


@pytest.mark.asyncio
async def test_team_leader_without_a_team_is_refused_not_given_everything():
    """열람 가능 팀이 하나도 없는 TEAM_LEADER 의 scope=all → 403.

    리더 팀 조회(auth.teams.leader_user_id = actor.user_id)는 허용한다 — 복수 팀
    리더 지원으로 격리 집합을 만들려면 그 인가 조회가 선행돼야 한다. 그 질의는
    본인이 리더인 팀 id 만 읽는다(사용량 데이터 아님). 금지되는 것은 usage_logs·
    budget_usages 같은 **데이터** 질의다.
    """
    session, seen = _recording_session()
    svc = AnalyticsService()

    with pytest.raises(ForbiddenError) as exc:
        await svc.get_analytics(
            session, period="2026-09", group_by="user", scope="all", actor=_leader(None)
        )

    # 운영자가 무엇을 해야 하는지 알 수 있는 메시지여야 한다(팀 배정).
    assert "team" in str(exc.value).lower()
    leaked = [s for s in seen if "usage_logs" in s or "budget_usages" in s]
    assert not leaked, f"막기 전에 데이터 질의가 나갔다 — 전사 데이터를 이미 읽었다: {leaked[:2]}"


@pytest.mark.asyncio
async def test_csv_export_is_refused_too():
    """CSV/JSON export 가 실제 유출 경로였다 — 같은 403 이어야 한다."""
    session, seen = _recording_session()
    svc = AnalyticsService()

    with pytest.raises(ForbiddenError):
        await svc.export_analytics(
            session, format="csv", period="2026-09", group_by="user", actor=_leader(None)
        )
    leaked = [s for s in seen if "usage_logs" in s or "budget_usages" in s]
    assert not leaked


@pytest.mark.asyncio
async def test_team_leader_with_a_team_is_still_scoped_to_it():
    """대조군 ① — 정상적인 TEAM_LEADER 를 막아 버리면 안 된다.

    그리고 by_user/trends 까지 **모든** 질의에 team_id 격리가 붙어 있어야 한다.
    예전엔 이 두 질의만 scope_id 가드 뒤에 있어서 따로 빠져나갔다.
    """
    session, seen = _recording_session(led=[TEAM_A])
    svc = AnalyticsService()

    res = await svc.get_analytics(
        session, period="2026-09", group_by="user", scope="all", actor=_leader(TEAM_A)
    )
    assert res.period == "2026-09"
    assert seen, "질의가 하나도 나가지 않았다 — 아래 단정이 공허하다"

    # 격리 경로는 테이블에 따라 둘이다. **어느 쪽도 없으면** 전사 데이터가 섞인다.
    #
    #   usage_logs.team_id  — usage_logs 를 직접 읽는 질의
    #   users.team_id       — budget_usages 를 읽는 질의. 이 테이블에는 team_id 컬럼이
    #                         **없어서**(scope/scope_id/period/client 뿐) auth.users 로
    #                         조인해 거르는 것이 유일한 방법이다.
    #
    # 세 번째는 인가 조회이지 데이터 질의가 아니다:
    #   teams.leader_user_id — 복수 팀 리더 지원으로 "리더가 맡은 팀 집합"을 만드는
    #                         조회. 본인이 리더인 팀 id 만 읽으므로 격리 없이 둔다.
    #
    # ⚠️ 이 목록에 새 문자열을 추가할 때는 그것이 진짜 격리인지 확인할 것. "team" 이
    #    들어간 아무 문자열이나 넣으면(예: GROUP BY teams.name) 가드가 통째로 공허해진다.
    SCOPE_PREDICATES = ("usage_logs.team_id IN", "users.team_id IN", "teams.leader_user_id")
    unscoped = [s for s in seen if not any(p in s for p in SCOPE_PREDICATES)]
    assert not unscoped, (
        f"team_id 격리가 없는 질의 {len(unscoped)}건 — 전사 데이터가 섞여 나온다. "
        f"예: {unscoped[0][:400]}"
    )

    # 대조군 — 두 경로가 **둘 다 실제로 등장**하는가. 한쪽이 사라지면 위 단정은
    # 남은 한쪽만으로 통과하고, 없어진 질의의 격리 누락을 못 잡는다.
    assert any("usage_logs.team_id IN" in s for s in seen), (
        "usage_logs 격리 질의가 하나도 없다 — 가드의 전제가 깨졌다"
    )
    assert any("budget.budget_usages" in s for s in seen), (
        "budget_usages 질의가 나가지 않았다 — seed 반영 경로가 사라졌다면 "
        "이 가드는 그 경로의 격리를 더 이상 검사하지 못한다"
    )
    for q in seen:
        if "budget.budget_usages" in q:
            assert "users.team_id IN" in q, (
                "budget_usages 질의에 users.team_id 격리가 없다 — TEAM_LEADER 가 다른 팀 "
                f"사용자의 이관 금액을 받아 간다: {q[:400]}"
            )


@pytest.mark.asyncio
async def test_admin_without_a_team_still_sees_everything():
    """대조군 ② — ADMIN 은 team_id 가 None 이어도 전사 분석을 봐야 한다.

    서비스 토큰/dev 토큰으로 만든 ADMIN 은 team_id 가 항상 None 이다
    (core/auth.py:129, :145). 여기서 막으면 대시보드 전체가 403 이 된다.
    """
    session, seen = _recording_session()
    svc = AnalyticsService()

    res = await svc.get_analytics(
        session, period="2026-09", group_by="model", scope="all", actor=_admin()
    )
    assert res.cost_summary.total_cost_usd == Decimal("0")
    assert seen
    assert all("usage_logs.team_id = " not in s for s in seen), (
        "ADMIN 인데 팀 격리가 걸렸다 — 전사 집계가 아니다"
    )


@pytest.mark.asyncio
async def test_team_leader_asking_for_another_team_is_still_forbidden():
    """대조군 ③ — 기존 team: scope 검사가 살아 있는지(수정이 이 경로를 건드리지 않았다)."""
    session, _ = _recording_session(led=[TEAM_A])
    svc = AnalyticsService()
    other = uuid.uuid4()

    with pytest.raises(ForbiddenError):
        await svc.get_analytics(
            session,
            period="2026-09",
            group_by="model",
            scope=f"team:{other}",
            actor=_leader(TEAM_A),
        )


@pytest.mark.asyncio
async def test_team_leader_can_request_own_team_scope():
    """scope=team:{본인팀} 은 허용된다 — 막히면 UI 의 팀 필터가 403 이 된다."""
    session, seen = _recording_session(led=[TEAM_A])
    svc = AnalyticsService()

    res = await svc.get_analytics(
        session, period="2026-09", group_by="model", scope=f"team:{TEAM_A}",
        actor=_leader(TEAM_A),
    )
    assert res.period == "2026-09"
    assert any("usage_logs.team_id IN" in s for s in seen), (
        "team: scope 질의에 팀 격리가 없다"
    )


@pytest.mark.asyncio
async def test_leader_scope_set_comes_from_led_teams_not_just_membership():
    """복수 팀 리더 지원 — 격리 집합은 leader_user_id 조회로 만들어져야 한다.

    소속 팀(actor.team_id) 하나만 보면, 한 사람이 여러 팀의 리더일 때 나머지
    팀 데이터가 빠진다. mock 세션은 빈 결과를 돌려주므로 응답 내용이 아니라
    조회가 실제로 나가는지만 본다.
    """
    session, seen = _recording_session(led=[TEAM_A])
    svc = AnalyticsService()

    await svc.get_analytics(
        session, period="2026-09", group_by="model", scope="all", actor=_leader(TEAM_A)
    )
    assert any("leader_user_id" in s for s in seen), (
        "리더 소유 팀 조회가 없다 — 복수 팀 리더면 나머지 팀이 격리 집합에서 빠진다"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 3) 엄격 정책 — 소속만으로는 열리지 않는다 (2026-09-18 확정)
# ──────────────────────────────────────────────────────────────────────────────
#
# 정책: TEAM_LEADER 의 격리 집합 = auth.teams.leader_user_id 가 본인인 팀들 **뿐**.
# 소속 팀(User.team_id)은 범위에 포함하지 않는다 — 소속과 리더 지정은 다른 개념:
#
#   소속 team1,           리더 team1   → team1
#   소속 team1+team2,     리더 team1+2 → team1+team2
#   소속 team1+team2,     리더 team1   → team1 만 (team2 는 소속이어도 안 보임)
#
# 예전 구현은 {리더 팀} ∪ {소속 팀} 으로 합쳐서 세 번째 케이스에서 team2 까지
# 열렸다 — 아래 테스트가 그 회귀를 못박는다.


@pytest.mark.asyncio
async def test_membership_alone_does_not_open_the_team():
    """소속 team1 이지만 **리더 지정이 없는** TEAM_LEADER → 403.

    엄격 정책의 핵심 분기다 — 소속은 리더 지정이 아니다. 예전 led∪member 구현이면
    이 사용자는 team1 데이터를 봤다(과다 열람). 403 은 "팀 배정을 요청하라" 는
    운영 신호다 — 조용히 빈 화면을 주면 리더 지정 누락을 아무도 모른다.
    """
    session, seen = _recording_session(led=[])  # 소속만 있고 리더 지정 없음
    svc = AnalyticsService()

    with pytest.raises(ForbiddenError):
        await svc.get_analytics(
            session, period="2026-09", group_by="model", scope="all", actor=_leader(TEAM_A)
        )
    leaked = [s for s in seen if "usage_logs" in s or "budget_usages" in s]
    assert not leaked, f"소속만으로 데이터 질의가 나갔다: {leaked[:2]}"


@pytest.mark.asyncio
async def test_member_of_two_teams_but_leader_of_one_sees_only_the_led_team():
    """소속 team1+team2, 리더는 team1 만 → team2 데이터는 열리지 않는다.

    사용자가 명시한 세 번째 케이스다. 격리 집합은 led 조회 결과(led=[TEAM_A])만이고,
    actor.team_id(소속)는 들어가지 않아야 한다 — TEAM_B 가 어떤 질의에도 등장하면
    소속 기반 확장이 살아난 것이다.
    """
    session, seen = _recording_session(led=[TEAM_A])
    svc = AnalyticsService()
    # 소속이 두 팀이라도 CurrentUser.team_id 는 1개라, team2 소속은 DB 쪽 얘기다.
    # 여기서 검증하는 건 "리더가 아닌 팀 id 가 격리 집합에 섞이지 않는다" 는 것.
    actor = _leader(TEAM_B)  # 소속은 B 인데 리더는 A 만 — B 데이터를 보면 안 된다

    await svc.get_analytics(
        session, period="2026-09", group_by="model", scope="all", actor=actor
    )

    data_queries = [s for s in seen if "usage_logs" in s or "budget_usages" in s]
    assert data_queries, "데이터 질의가 없다 — 아래 단정이 공허하다"
    for q in data_queries:
        assert TEAM_B.hex not in q, (
            f"리더가 아닌 팀(소속 B) id 가 질의에 들어갔다 — 소속 기반 확장 회귀: {q[:400]}"
        )


@pytest.mark.asyncio
async def test_leader_of_two_teams_sees_both():
    """한 사람이 두 팀의 리더 → 두 팀 다 격리 집합에 들어간다.

    사용자가 명시한 두 번째 케이스다. 복수 팀 리더 지원의 존재 이유다.
    """
    session, seen = _recording_session(led=[TEAM_A, TEAM_B])
    svc = AnalyticsService()

    await svc.get_analytics(
        session, period="2026-09", group_by="model", scope="all", actor=_leader(TEAM_A)
    )

    data_queries = [s for s in seen if "usage_logs.team_id" in s]
    assert data_queries, "팀 격리 질의가 없다"
    assert any(TEAM_A.hex in q and TEAM_B.hex in q for q in data_queries), (
        "복수 리더 팀이 같은 격리 집합에 들어가지 않았다 — 한 팀만 보이게 된다"
    )


@pytest.mark.asyncio
async def test_leader_of_a_cannot_request_team_b_scope():
    """리더는 A 만인데 scope=team:B 를 요청 → 403 (소속 여부와 무관)."""
    session, _ = _recording_session(led=[TEAM_A])
    svc = AnalyticsService()

    with pytest.raises(ForbiddenError):
        await svc.get_analytics(
            session, period="2026-09", group_by="model",
            scope=f"team:{TEAM_B}", actor=_leader(TEAM_B)  # 소속 B 라도 리더는 A 뿐
        )
