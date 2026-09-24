# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""``/admin/analytics/usage/by-user`` 가 마이그레이션 주입분(seed)을 반영하는지.

배경 — ``POST /admin/budgets/seed-spent`` 는 이관 이전 사용액을 ``budget.budget_usages``
에 **절대값**으로 넣는다(예산 소진율 연속성). 그런데 분석 경로는 ``usage.usage_logs``
만 읽었다. 그래서 같은 사용자·같은 기간에 대해 예산 화면은 $963 을, 분석 화면은 $13 을
보여줬다 — 운영자가 어느 쪽을 믿어야 할지 알 수 없는 상태다.

⚠️ 이 파일이 실 PostgreSQL 을 요구하는 이유: 검증 대상 SQL 이 CTE + ``GREATEST`` +
   ``LEFT JOIN`` 이라 **mock 이나 SQLite 로는 한 줄도 실행되지 않는다.** 목 기반
   단위 테스트는 이 로직에 대해 항상 통과한다(실제로 스위트 504개가 이 변경 전후로
   전부 그린이었다) — 그게 이 파일이 필요한 이유다.

두 가지 오답을 명시적으로 배제한다. 각각 "고쳐 놓고도 맞는 것처럼 보이는" 형태다:

  1. ``max(일자범위 실사용, budget_usages 월 누적)``
     ``budget_usages`` 에는 일자 축이 없다. 그래서 date 를 월 중간으로 주면 월 총액이
     거의 항상 이겨, 바깥 ``cost_usd`` 는 date 를 무시한 월 총액이 되고 ``calls`` 는
     date 범위가 된다 — 한 응답 안에서 두 필드가 서로 다른 기간을 말한다.

  2. 잔차를 SUCCESS 실사용만으로 빼기
     ``budget_usages`` 는 **전 status** 를 누적한다. SUCCESS 만 빼면 실패 요청의 비용이
     잔차에 남아 seed 를 과대계상한다.

실행::

    docker run -d --name pg-proof -p 55432:5432 -e POSTGRES_PASSWORD=proof \\
        -e POSTGRES_DB=gwproof pgvector/pgvector:pg16
    PROOF_DSN=postgresql+asyncpg://postgres:proof@127.0.0.1:55432/gwproof \\
        pytest tests/regression/test_high_analytics_seed_reconciliation.py

⚠️ 이 픽스처는 ``PROOF_DSN`` 의 데이터베이스를 **건드리지 않는다.** 자기 소유
   ``<dbname>_seedrec`` 를 만들고 쓰고 지운다. 이유는
   ``test_high_duplicate_active_config.py`` 의 모듈 docstring 에 실측으로 기록돼 있다 —
   같은 DB 를 공유하며 스키마를 DROP 하는 테스트는 자기는 늘 통과하고 **다음에 도는
   테스트를** 깨뜨린다(그래서 원리적으로 자기 진단이 불가능하다).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

PROOF_DSN = os.environ.get("PROOF_DSN")
_OWNED_SUFFIX = "_seedrec"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INIT_DIR = _REPO_ROOT / "db" / "init"

pytestmark = pytest.mark.skipif(
    not PROOF_DSN, reason="PROOF_DSN 미설정 — 실 PostgreSQL 필요(mock/SQLite 로는 검증 불가)"
)

KST = timezone(timedelta(hours=9))


# ─────────────────────────────────────────────────────────────────────────────
# 자체 소유 DB 부트스트랩
# ─────────────────────────────────────────────────────────────────────────────


def _split_dsn(dsn: str) -> tuple[str, str]:
    prefix, _, dbname = dsn.rpartition("/")
    assert prefix and dbname, f"DSN 형식이 이상하다: {dsn}"
    return prefix, dbname


def _asyncpg_dsn(sa_dsn: str) -> str:
    """SQLAlchemy DSN → 순수 asyncpg DSN."""
    return sa_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _create_owned_db() -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    prefix, dbname = _split_dsn(PROOF_DSN)
    owned = f"{dbname}{_OWNED_SUFFIX}"
    admin = create_async_engine(f"{prefix}/postgres", isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            # FORCE: 죽은 런이 남긴 커넥션이 drop 을 막지 못하게(PostgreSQL 13+).
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{owned}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{owned}"'))
    finally:
        await admin.dispose()
    return f"{prefix}/{owned}"


async def _drop_owned_db() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    prefix, dbname = _split_dsn(PROOF_DSN)
    admin = create_async_engine(f"{prefix}/postgres", isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(f'DROP DATABASE IF EXISTS "{dbname}{_OWNED_SUFFIX}" WITH (FORCE)')
            )
    finally:
        await admin.dispose()


async def _apply_init_sql(dsn: str) -> None:
    """실 ``db/init/*.sql`` 을 그대로 적용한다.

    ⚠️ 손으로 쓴 DDL 을 쓰지 않는 이유: 이 테스트가 검증하는 것은 ``usage_logs`` 와
       ``budget_usages`` 의 **실제 컬럼/타입/enum** 위에서 SQL 이 도는지다. DDL 을
       테스트에 복제하면 프로덕션 스키마가 바뀌어도 테스트는 옛 스키마로 계속 통과한다.

    ⚠️ asyncpg 를 직접 쓴다. SQLAlchemy 의 asyncpg 드라이버는 문장을 prepared statement
       로 감싸서 **멀티 스테이트먼트를 거부**하고, init SQL 은 ``DO $$ ... $$`` 블록과
       여러 문장을 담고 있다. asyncpg 의 ``execute`` 는 인자가 없으면 simple query
       프로토콜을 써서 그대로 통과한다.
    """
    asyncpg = pytest.importorskip("asyncpg")

    files = sorted(_INIT_DIR.glob("*.sql"))
    assert len(files) >= 4, f"init SQL 을 {len(files)}개만 찾았다 — 경로 확인: {_INIT_DIR}"

    conn = await asyncpg.connect(_asyncpg_dsn(dsn))
    try:
        for f in files:
            sql = f.read_text(encoding="utf-8")
            try:
                await conn.execute(sql)
            except Exception as e:  # noqa: BLE001 - 어떤 파일이 왜 실패했는지 보여야 한다
                # pgvector 확장이 없는 이미지에서는 일부 파일이 실패할 수 있다. 이 테스트가
                # 필요한 테이블이 결국 생겼는지는 아래에서 단정하므로, 여기서는 기록만 한다.
                print(f"  [init] {f.name} 실패(계속): {type(e).__name__}: {e}")

        # 대조군 — 이 테스트가 요구하는 테이블이 실제로 생겼는가. 없으면 skip 이 아니라
        # 실패다(스키마가 없는 채로 "통과" 하면 아무것도 증명하지 못한다).
        for schema, table in [
            ("auth", "users"),
            ("auth", "teams"),
            ("usage", "usage_logs"),
            ("budget", "budget_usages"),
        ]:
            n = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = $1 AND table_name = $2",
                schema,
                table,
            )
            assert n == 1, f"{schema}.{table} 이 만들어지지 않았다 — init SQL 적용 실패"
    finally:
        await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 시드 데이터
# ─────────────────────────────────────────────────────────────────────────────

PERIOD = "2026-05"

# 사용자 3명이 각기 다른 형태를 대표한다.
U_PLAIN = uuid.UUID("aaaa0000-0000-4000-a000-000000000001")  # 실사용만
U_SEEDED = uuid.UUID("aaaa0000-0000-4000-a000-000000000002")  # 실사용 + seed
U_SEED_ONLY = uuid.UUID("aaaa0000-0000-4000-a000-000000000003")  # seed 만(usage_logs 0건)


def _kst(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 5, day, hour, tzinfo=KST)


async def _seed(dsn: str) -> None:
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(_asyncpg_dsn(dsn))
    try:
        team_id = await conn.fetchval("SELECT id FROM auth.teams LIMIT 1")
        assert team_id is not None, "시드 팀이 없다 — init SQL 의 03_seed_data 확인"

        for uid, email in [
            (U_PLAIN, "plain@example.com"),
            (U_SEEDED, "seeded@example.com"),
            (U_SEED_ONLY, "seedonly@example.com"),
        ]:
            await conn.execute(
                "INSERT INTO auth.users (id, team_id, email, display_name, role, sso_subject) "
                "VALUES ($1, $2, $3, $4, 'DEVELOPER', $5) ON CONFLICT DO NOTHING",
                uid,
                team_id,
                email,
                email.split("@")[0],
                f"sub-{uid}",
            )

        dept_id = await conn.fetchval(
            "SELECT dept_id FROM auth.teams WHERE id = $1", team_id
        )
        assert dept_id is not None, "시드 팀에 dept_id 가 없다"

        row = await conn.fetchrow(
            "SELECT alias, provider FROM model.model_aliases LIMIT 1"
        )
        assert row, "시드 모델 alias 가 없다"
        alias, provider = row["alias"], row["provider"]

        async def log(user, day, cost, status="SUCCESS", tokens=(10, 20, 5, 7)):
            # ⚠️ request_id 는 NOT NULL 이다. 실 init SQL 을 쓴 덕에 이 제약이 여기서
            #    드러났다 — DDL 을 테스트에 복제했다면 컬럼 하나가 조용히 빠진 채로
            #    "실 스키마에서 검증했다" 고 믿었을 것이다.
            await conn.execute(
                "INSERT INTO usage.usage_logs "
                "(id, request_id, user_id, team_id, dept_id, model_alias, provider, status, "
                " requested_at, completed_at, cost_usd, input_tokens, output_tokens, "
                " cache_read_tokens, cache_creation_tokens, latency_ms) "
                "VALUES (gen_random_uuid(), gen_random_uuid()::text, $1, $2, $3, $4, "
                # ⚠️ usage_logs.provider 는 enum 이 아니라 varchar(32) 다(model_aliases 쪽만
                #    model.provider enum). 캐스트를 붙이면 UndefinedObjectError 가 난다.
                "        $5, $6::usage.usage_status, $7, $7, $8, $9, $10, $11, $12, 100)",
                user, team_id, dept_id, alias, str(provider), status, _kst(day),
                Decimal(str(cost)), *tokens,
            )

        # U_PLAIN: 5/03 $1, 5/20 $2  (SUCCESS)
        await log(U_PLAIN, 3, "1.00")
        await log(U_PLAIN, 20, "2.00")

        # U_SEEDED: 5/03 $3 SUCCESS, 5/20 $4 SUCCESS, 5/05 $9 FAILED
        #   → 월 SUCCESS 합 $7, 월 전-status 합 $16
        await log(U_SEEDED, 3, "3.00")
        await log(U_SEEDED, 20, "4.00")
        await log(U_SEEDED, 5, "9.00", status="ERROR")

        # budget_usages: U_SEEDED 는 월 누적 $116 로 기록됐다(전-status $16 + 이관 $100)
        for uid, used in [(U_SEEDED, "116.00"), (U_SEED_ONLY, "50.00")]:
            await conn.execute(
                "INSERT INTO budget.budget_usages "
                "(id, scope, scope_id, period, client, used_usd, limit_usd, last_updated) "
                "VALUES (gen_random_uuid(), 'USER'::budget.budget_scope, $1, $2, NULL, $3, "
                "        1000, now())",
                uid, PERIOD, Decimal(used),
            )
    finally:
        await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 픽스처
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
async def owned_dsn():
    dsn = await _create_owned_db()
    await _apply_init_sql(dsn)
    await _seed(dsn)
    yield dsn
    await _drop_owned_db()


@pytest.fixture
async def session(owned_dsn):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(owned_dsn)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _by_user(session, *, date: str):
    from app.services.analytics_service import AnalyticsService

    resp = await AnalyticsService().get_usage_by_user(session, period=PERIOD, date=date)
    return {it.user_id: it for it in resp.items}


# ─────────────────────────────────────────────────────────────────────────────
# 1. 기본 — seed 없는 사용자는 움직이지 않는다
# ─────────────────────────────────────────────────────────────────────────────


async def test_user_without_seed_is_unchanged(session):
    """가장 중요한 회귀 방어 — 대부분의 사용자는 seed 가 없다."""
    items = await _by_user(session, date="2026-05-31")
    it = items[str(U_PLAIN)]
    assert it.cost_usd == Decimal("3.00"), "실사용만인 사용자의 금액이 움직였다"
    assert it.seeded_usd == Decimal("0")
    assert it.calls == 2


async def test_date_param_scopes_the_plain_user(session):
    items = await _by_user(session, date="2026-05-10")
    it = items[str(U_PLAIN)]
    assert it.cost_usd == Decimal("1.00"), "5/20 건이 5/10 까지의 합에 섞였다"
    assert it.calls == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. seed 반영 — 금액 보존 + date 준수
# ─────────────────────────────────────────────────────────────────────────────


async def test_seed_appears_and_is_exposed_separately(session):
    """월 전체: 실사용 SUCCESS $7 + seed $100 = $107.

    seed 는 `116 − 16`(전-status 월 실사용)으로 역산된다. SUCCESS 만으로 뺐다면
    `116 − 7 = 109` 가 되어 실패 요청의 $9 만큼 과대계상된다.
    """
    items = await _by_user(session, date="2026-05-31")
    it = items[str(U_SEEDED)]
    assert it.seeded_usd == Decimal("100.00"), (
        f"seed 역산이 틀렸다: {it.seeded_usd} (전-status 로 빼야 100)"
    )
    assert it.cost_usd == Decimal("107.00")


async def test_cost_usd_honours_date_even_with_a_seed(session):
    """여기가 `max(actual, month_total)` 형태가 틀리는 지점이다.

    5/10 까지면 실사용 SUCCESS 는 $3 뿐이다. seed 는 월 상수이므로 그대로 더해져
    $103 이 되어야 한다. `max` 형태였다면 월 총액 $116 이 이겨서, calls=1(일자 범위)
    인데 cost_usd 는 월 전체를 말하는 응답이 된다.
    """
    items = await _by_user(session, date="2026-05-10")
    it = items[str(U_SEEDED)]
    assert it.calls == 1, "일자 범위가 안 걸렸다 — 이 테스트의 전제가 깨졌다"
    assert it.cost_usd == Decimal("103.00"), (
        f"date 가 무시됐다: {it.cost_usd} (월 총액 116 이 이겼다면 max 형태다)"
    )
    assert it.seeded_usd == Decimal("100.00")


async def test_model_breakdown_is_not_inflated_by_the_seed(session):
    """seed 에는 모델 granularity 가 없다 — 모델 표에 넣으면 데이터를 조작하는 것이다."""
    from app.services.analytics_service import AnalyticsService

    resp = await AnalyticsService().get_usage_by_user_model(
        session, period=PERIOD, date="2026-05-31"
    )
    mine = [it for it in resp.items if it.user_id == str(U_SEEDED)]
    assert mine, "by-user-model 에 해당 사용자가 없다"
    assert sum(it.cost_usd for it in mine) == Decimal("7.00"), (
        "모델 단위 합이 실사용($7)을 넘었다 — seed 가 새어 들어갔다"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. usage_logs 가 0건인데 seed 만 있는 사용자
# ─────────────────────────────────────────────────────────────────────────────


async def test_seed_only_user_is_not_dropped(session):
    """이관 직후 아직 게이트웨이를 쓰지 않은 사람.

    목록의 기준 테이블이 usage_logs 라 이 사람은 통째로 빠진다 — 예산은 소진됐는데
    분석 화면에는 존재하지 않는, 설명 불가능한 상태다.
    """
    items = await _by_user(session, date="2026-05-31")
    assert str(U_SEED_ONLY) in items, "seed 만 있는 사용자가 목록에서 사라졌다"
    it = items[str(U_SEED_ONLY)]
    assert it.cost_usd == Decimal("50.00")
    assert it.seeded_usd == Decimal("50.00")
    assert it.calls == 0
    assert it.user_email == "seedonly@example.com", "보강 경로가 메타데이터를 못 채웠다"


async def test_items_are_sorted_by_total_cost_descending(session):
    """seed 를 더한 뒤 재정렬되는가 — SQL 의 ORDER BY 는 실사용 기준이다.

    실사용만 보면 U_SEEDED($7) > U_PLAIN($3) > U_SEED_ONLY($0) 지만, 총액으로는
    U_SEEDED($107) > U_SEED_ONLY($50) > U_PLAIN($3) 이다.
    """
    from app.services.analytics_service import AnalyticsService

    resp = await AnalyticsService().get_usage_by_user(
        session, period=PERIOD, date="2026-05-31"
    )
    ours = [it for it in resp.items if it.user_id in {str(U_PLAIN), str(U_SEEDED), str(U_SEED_ONLY)}]
    assert [it.user_id for it in ours] == [str(U_SEEDED), str(U_SEED_ONLY), str(U_PLAIN)], (
        "seed 반영 후 재정렬되지 않았다"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. 확장 필드
# ─────────────────────────────────────────────────────────────────────────────


async def test_email_and_cache_tokens_are_exposed(session):
    items = await _by_user(session, date="2026-05-31")
    it = items[str(U_PLAIN)]
    assert it.user_email == "plain@example.com"
    # 로그 2건 × (in 10, out 20, cache_read 5, cache_write 7)
    assert (it.input_tokens, it.output_tokens) == (20, 40)
    assert (it.cache_read_tokens, it.cache_write_tokens) == (10, 14)


async def test_by_user_model_exposes_email(session):
    from app.services.analytics_service import AnalyticsService

    resp = await AnalyticsService().get_usage_by_user_model(
        session, period=PERIOD, date="2026-05-31"
    )
    mine = [it for it in resp.items if it.user_id == str(U_PLAIN)]
    assert mine and all(it.user_email == "plain@example.com" for it in mine)


# ─────────────────────────────────────────────────────────────────────────────
# 5. 음성 대조군 — seed 가 없으면 아무 일도 일어나지 않는다
# ─────────────────────────────────────────────────────────────────────────────


async def test_no_phantom_seed_when_budget_usages_matches_actual(session):
    """``budget_usages`` 가 실사용과 같으면 잔차 0 — 없는 seed 를 만들지 않는다.

    ``GREATEST(..., 0)`` 이 없으면 분석(SUCCESS)이 예산(전-status)보다 클 때 음수
    잔차가 나와 금액을 **깎는다**.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(_asyncpg_dsn(str(session.bind.url).replace("***", "proof")))
    try:
        uid = uuid.UUID("aaaa0000-0000-4000-a000-000000000009")
        team_id = await conn.fetchval("SELECT id FROM auth.teams LIMIT 1")
        await conn.execute(
            "INSERT INTO auth.users (id, team_id, email, display_name, role, sso_subject) "
            "VALUES ($1,$2,'exact@example.com','exact','DEVELOPER',$3) ON CONFLICT DO NOTHING",
            uid, team_id, f"sub-{uid}",
        )
        dept_id = await conn.fetchval(
            "SELECT dept_id FROM auth.teams WHERE id = $1", team_id
        )
        row = await conn.fetchrow("SELECT alias, provider FROM model.model_aliases LIMIT 1")
        await conn.execute(
            "INSERT INTO usage.usage_logs (id,request_id,user_id,team_id,dept_id,model_alias,"
            "provider,status,requested_at,completed_at,cost_usd,input_tokens,output_tokens,"
            "cache_read_tokens,cache_creation_tokens,latency_ms) VALUES (gen_random_uuid(),"
            "gen_random_uuid()::text,$1,$2,$3,$4,$5,'SUCCESS'::usage.usage_status,$6,$6,"
            "5.00,1,1,0,0,10)",
            uid, team_id, dept_id, row["alias"], str(row["provider"]), _kst(7),
        )
        # 예산 기록이 실사용과 정확히 같다 → 잔차 0
        await conn.execute(
            "INSERT INTO budget.budget_usages (id,scope,scope_id,period,client,used_usd,"
            "limit_usd,last_updated) VALUES (gen_random_uuid(),'USER'::budget.budget_scope,"
            "$1,$2,NULL,5.00,1000,now())",
            uid, PERIOD,
        )
    finally:
        await conn.close()

    items = await _by_user(session, date="2026-05-31")
    it = items[str(uid)]
    assert it.seeded_usd == Decimal("0"), f"없는 seed 가 생겼다: {it.seeded_usd}"
    assert it.cost_usd == Decimal("5.00")


# ─────────────────────────────────────────────────────────────────────────────
# 6. `/admin/analytics` 의 by_user 차트 — 사용자가 실제로 보는 화면
# ─────────────────────────────────────────────────────────────────────────────


def _admin_actor():
    from app.core.auth import CurrentUser
    from app.models.auth import UserRole

    return CurrentUser(
        user_id=uuid.uuid4(), email="admin@example.com", role=UserRole.ADMIN, team_id=None
    )


def _leader_actor(team_id, user_id=None):
    from app.core.auth import CurrentUser
    from app.models.auth import UserRole

    return CurrentUser(
        user_id=user_id or uuid.uuid4(), email="lead@example.com",
        role=UserRole.TEAM_LEADER, team_id=team_id,
    )


async def _analytics_by_user(session, actor):
    from app.services.analytics_service import AnalyticsService

    resp = await AnalyticsService().get_analytics(
        session, period=PERIOD, group_by="user", scope="all", actor=actor
    )
    return {b.email: b for b in resp.by_user}


async def test_analytics_chart_includes_the_seed(session):
    """예산 페이지가 96% 소진을 말하는데 차트에서는 싸 보이는 상태를 만들지 않는다."""
    by_email = await _analytics_by_user(session, _admin_actor())
    assert "seeded@example.com" in by_email, "차트에 해당 사용자가 없다"
    # 월 SUCCESS $7 + seed $100
    assert by_email["seeded@example.com"].cost_usd == Decimal("107.00")


async def test_analytics_chart_keeps_plain_users_untouched(session):
    by_email = await _analytics_by_user(session, _admin_actor())
    assert by_email["plain@example.com"].cost_usd == Decimal("3.00")


async def test_analytics_chart_shows_seed_only_users(session):
    by_email = await _analytics_by_user(session, _admin_actor())
    assert "seedonly@example.com" in by_email, "usage_logs 0건인 이관 사용자가 차트에서 빠졌다"
    assert by_email["seedonly@example.com"].cost_usd == Decimal("50.00")
    assert by_email["seedonly@example.com"].requests == 0


async def test_analytics_chart_ranks_by_total_including_seed(session):
    """상위 50명 축이 총액 기준이어야 한다 — 실사용 기준이면 seed 큰 사용자가 밀린다."""
    from app.services.analytics_service import AnalyticsService

    resp = await AnalyticsService().get_analytics(
        session, period=PERIOD, group_by="user", scope="all", actor=_admin_actor()
    )
    costs = [b.cost_usd for b in resp.by_user]
    assert costs == sorted(costs, reverse=True), "총액 내림차순이 아니다"
    ours = [b.email for b in resp.by_user if b.email and b.email.endswith("@example.com")]
    assert ours.index("seeded@example.com") < ours.index("plain@example.com")
    assert ours.index("seedonly@example.com") < ours.index("plain@example.com")


async def test_team_leader_does_not_see_other_teams_seed(session):
    """권한 누출 방어 — budget_usages 에는 team_id 가 없어 users 조인으로 걸러야 한다.

    TEAM_LEADER 의 범위는 ``auth.teams.leader_user_id`` 로 지정된 팀이다(엄격 정책 —
    소속 팀은 범위가 아니다). 시드 사용자 전원이 같은 팀이므로, **다른** 팀의 리더에게는
    아무것도 보이지 않아야 한다.
    """
    from sqlalchemy import text

    leader_id = uuid.uuid4()
    other_team = uuid.uuid4()  # 실제 팀 행 — 리더 지정이 있어야 스코프가 열린다
    dept_id = await session.scalar(text("SELECT dept_id FROM auth.teams LIMIT 1"))
    await session.execute(
        text(
            "INSERT INTO auth.users (id, team_id, email, display_name, role, sso_subject)"
            " VALUES (:id, NULL, :email, 'other-lead', 'TEAM_LEADER', :sub)"
        ),
        {
            "id": leader_id,
            "email": f"otherlead-{leader_id}@example.com",
            "sub": f"sub-{leader_id}",
        },
    )
    await session.execute(
        text(
            "INSERT INTO auth.teams (id, dept_id, name, leader_user_id)"
            " VALUES (:id, :dept, 'other-team', :leader)"
        ),
        {"id": other_team, "dept": dept_id, "leader": leader_id},
    )

    by_email = await _analytics_by_user(
        session, _leader_actor(other_team, user_id=leader_id)
    )
    leaked = [e for e in by_email if e and e.endswith("@example.com")]
    assert not leaked, f"다른 팀 리더에게 사용자가 새어 나갔다: {leaked}"


async def test_team_leader_without_led_teams_is_denied(session):
    """리더로 지정된 팀이 하나도 없는 TEAM_LEADER 는 빈 결과가 아니라 403 이다.

    회귀 방어 — 예전엔 스코프 id 가 None 이 되며 WHERE 절이 통째로 사라져 전사
    분석이 그대로 나갔다(analytics_service 의 ⚠️ 주석 참조).
    """
    from app.core.exceptions import ForbiddenError
    from app.services.analytics_service import AnalyticsService

    with pytest.raises(ForbiddenError):
        await AnalyticsService().get_analytics(
            session, period=PERIOD, group_by="user", scope="all",
            actor=_leader_actor(uuid.uuid4()),  # led 팀 0 개
        )
