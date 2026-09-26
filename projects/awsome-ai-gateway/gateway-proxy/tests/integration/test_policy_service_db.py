"""load_models_inputs against real Postgres — the SQL half of the policy fragment.

Each test runs inside a transaction that is rolled back, and uses its own
`ph-test-*` model aliases so seed data does not leak into the assertions.
Prerequisite: `docker compose up -d postgres` (init SQL creates the schema).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.policy_service import load_models_inputs
from tests.integration.conftest import live_db_gate

DB_URL = os.environ.get(
    "TEST_DB_URL",
    "postgresql+asyncpg://gateway:gateway_dev_password@localhost:5432/gateway",
)
pytestmark = [*live_db_gate(DB_URL), pytest.mark.integration, pytest.mark.asyncio]

ADMIN = "00000000-0000-4000-a000-000000000010"   # seeded admin (created_by FKs)
DEPT = "00000000-0000-4000-a000-000000000002"    # seeded department


@pytest.fixture
async def db():
    engine = create_async_engine(DB_URL)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


async def add_team(db) -> str:
    team_id = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO auth.teams (id, dept_id, name) VALUES (:id, :dept, :name)"),
        {"id": team_id, "dept": DEPT, "name": f"ph-team-{team_id[:8]}"},
    )
    return team_id


async def add_user(db, team_id: str, active: bool = True) -> str:
    user_id = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO auth.users (id, email, display_name, sso_subject, team_id, is_active) "
            "VALUES (:id, :email, 'ph', :sub, :team, :active)"
        ),
        {"id": user_id, "email": f"{user_id}@example.com", "sub": user_id, "team": team_id,
         "active": active},
    )
    return user_id


async def add_model(db, alias: str, *, status: str = "ACTIVE", clients: list[str] | None = None):
    await db.execute(
        text(
            "INSERT INTO model.model_aliases "
            "(alias, provider, provider_model_id, api_format, status, allowed_clients, created_by) "
            "VALUES (:alias, 'BEDROCK', :pid, 'BEDROCK_NATIVE', :status, :clients, :admin)"
        ),
        {"alias": alias, "pid": f"us.anthropic.{alias}", "status": status, "clients": clients,
         "admin": ADMIN},
    )


async def test_unknown_user_has_no_inputs(db):
    assert await load_models_inputs(db, str(uuid.uuid4()), "claude-code") is None


async def test_inactive_user_has_no_inputs(db):
    user_id = await add_user(db, await add_team(db), active=False)

    assert await load_models_inputs(db, user_id, "claude-code") is None


async def test_inputs_reflect_user_team_app_and_default(db):
    team_id = await add_team(db)
    user_id = await add_user(db, team_id)
    await add_model(db, "ph-test-a")
    await add_model(db, "ph-test-b")
    await add_model(db, "ph-test-codex-only", clients=["codex"])
    await add_model(db, "ph-test-inactive", status="INACTIVE")
    await db.execute(
        text("INSERT INTO model.team_allowed_models (team_id, model_alias, created_by) "
             "VALUES (:t, 'ph-test-a', :admin), (:t, 'ph-test-b', :admin)"),
        {"t": team_id, "admin": ADMIN},
    )
    await db.execute(
        text("INSERT INTO model.user_allowed_models (user_id, model_alias, created_by) "
             "VALUES (:u, 'ph-test-b', :admin)"),
        {"u": user_id, "admin": ADMIN},
    )
    await db.execute(
        text("INSERT INTO model.routing_profiles (client, backend, region, default_model) "
             "VALUES ('claude-code', 'invoke', 'us-west-2', 'ph-test-b') "
             "ON CONFLICT (client) DO UPDATE SET default_model = EXCLUDED.default_model"),
    )

    inputs = await load_models_inputs(db, user_id, "claude-code")

    assert inputs["user_aliases"] == ["ph-test-b"]
    assert sorted(inputs["team_aliases"]) == ["ph-test-a", "ph-test-b"]
    assert "ph-test-codex-only" in inputs["active_models"]
    assert "ph-test-codex-only" not in inputs["app_models"]
    assert "ph-test-inactive" not in inputs["active_models"]
    assert {"ph-test-a", "ph-test-b"} <= set(inputs["app_models"])
    assert inputs["app_models"] == sorted(inputs["app_models"])
    assert inputs["default_model"] == "ph-test-b"
