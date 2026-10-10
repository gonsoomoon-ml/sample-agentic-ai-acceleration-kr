# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""RecipientResolver 단위 테스트 — DB를 AsyncMock으로 대체."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from worker.models.auth import Team, User
from worker.schemas.recipients import RecipientRole
from worker.services.recipient_resolver import RecipientResolver


# spec_set (not spec) is deliberate: it makes an assignment to an attribute that does not
# exist on the real model raise AttributeError. These fixtures previously set `name` and
# `roles`, neither of which exists on auth.users (the columns are `display_name` and `role`),
# and plain `spec` allows that — so the resolver received an auto-created child MagicMock,
# Recipient() rejected it, RecipientResolver swallowed the exception, and the tests asserted
# on an empty list. spec_set turns that silent drift into an immediate failure.
def _make_user(
    user_id: str = "u1",
    email: str = "user@example.com",
    display_name: str = "Alice",
    team_id: str = "t1",
    role: str = "DEVELOPER",
    is_active: bool = True,
) -> User:
    u = MagicMock(spec_set=User)
    u.id = user_id
    u.email = email
    u.display_name = display_name
    u.team_id = team_id
    u.role = role
    u.is_active = is_active
    return u


def _make_team(team_id: str = "t1", leader_user_id: str | None = "u2") -> Team:
    t = MagicMock(spec_set=Team)
    t.id = team_id
    t.leader_user_id = leader_user_id
    return t


def _make_factory(session: AsyncMock) -> MagicMock:
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


async def test_resolve_affected_user() -> None:
    user = _make_user("u1", "alice@example.com", "Alice")
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=result)

    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.AFFECTED_USER],
        payload={"user_id": "u1"},
    )

    assert len(recipients) == 1
    assert recipients[0].email == "alice@example.com"
    assert recipients[0].role == RecipientRole.AFFECTED_USER


async def test_resolve_affected_user_not_found_returns_empty() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.AFFECTED_USER],
        payload={"user_id": "nonexistent"},
    )
    assert recipients == []


async def test_resolve_affected_user_no_user_id_returns_empty() -> None:
    session = AsyncMock()
    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.AFFECTED_USER],
        payload={},  # user_id 없음
    )
    assert recipients == []


async def test_resolve_admin_returns_all_admins() -> None:
    admin1 = _make_user("u1", "admin1@example.com", "Admin1", role="ADMIN")
    admin2 = _make_user("u2", "admin2@example.com", "Admin2", role="ADMIN")

    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [admin1, admin2]
    session.execute = AsyncMock(return_value=result)

    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.ADMIN],
        payload={},
    )

    assert len(recipients) == 2
    emails = {r.email for r in recipients}
    assert "admin1@example.com" in emails
    assert "admin2@example.com" in emails


async def test_resolve_deduplicates_emails() -> None:
    """동일 이메일이 여러 역할로 해소되면 중복 제거."""
    user = _make_user("u1", "alice@example.com", "Alice")

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    result.scalars.return_value.all.return_value = [user]
    session.execute = AsyncMock(return_value=result)

    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.AFFECTED_USER, RecipientRole.ADMIN],
        payload={"user_id": "u1"},
    )

    emails = [r.email for r in recipients]
    assert emails.count("alice@example.com") == 1


async def test_resolve_unknown_role_returns_empty() -> None:
    session = AsyncMock()
    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=["unknown_role"],
        payload={},
    )
    assert recipients == []


async def test_resolve_team_leader_returns_all_leaders() -> None:
    """리더가 여러 명이면 전원에게 간다 — Team.leader_user_id 는 가장 최근 지정을
    가리키는 표시용 포인터라 그걸 따라가면 나머지 리더는 메일을 못 받는다."""
    session = AsyncMock()
    team = _make_team("t1")
    leader1 = _make_user("u2", "leader1@example.com", role="TEAM_LEADER")
    leader2 = _make_user("u3", "leader2@example.com", role="TEAM_LEADER")

    team_res = MagicMock()
    team_res.scalar_one_or_none.return_value = team
    leaders_res = MagicMock()
    leaders_res.scalars.return_value.all.return_value = [leader1, leader2]
    session.execute = AsyncMock(side_effect=[team_res, leaders_res])

    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.TEAM_LEADER], payload={"team_id": "t1"}
    )

    assert sorted(r.email for r in recipients) == [
        "leader1@example.com",
        "leader2@example.com",
    ]
    assert all(r.role == RecipientRole.TEAM_LEADER for r in recipients)


async def test_resolve_team_leader_no_leaders_returns_empty() -> None:
    """리더 미지정 팀은 오류가 아니라 빈 목록 (BR-RCP-04)."""
    session = AsyncMock()
    team_res = MagicMock()
    team_res.scalar_one_or_none.return_value = _make_team("t1", leader_user_id=None)
    leaders_res = MagicMock()
    leaders_res.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(side_effect=[team_res, leaders_res])

    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.TEAM_LEADER], payload={"team_id": "t1"}
    )
    assert recipients == []


async def test_resolve_admin_skipped_on_bulk_events() -> None:
    """bulk 표시 이벤트(force_reauth 등)는 admin 역할을 생략한다 — 멤버 N명
    이벤트가 관리자에게 N 통 fan-out 되는 걸 막는다."""
    session = AsyncMock()
    resolver = RecipientResolver(_make_factory(session))
    recipients = await resolver.resolve(
        roles=[RecipientRole.ADMIN], payload={"bulk": True}
    )
    assert recipients == []
    session.execute.assert_not_called()
