# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""OIDCService._parse_group — Cognito 그룹명 파싱 결정론 테스트.

규칙 (underscore 개수 기반):
  - "Claude_<team>"          → (None, "team")      Default Department 팀
  - "Claude_<dept>_<team>"   → ("dept", "team")
  - 그 외                    → None (reject)
"""
from __future__ import annotations

import pytest

from app.services.oidc_service import OIDCService


@pytest.mark.parametrize(
    "group_name,expected",
    [
        # 정상 케이스
        ("Claude_backend", (None, "backend")),
        ("Claude_ML", (None, "ML")),
        ("Claude_ai_platform", ("ai", "platform")),
        ("Claude_test-department_aws-test", ("test-department", "aws-test")),
        ("Claude_AI-Center_S/W-Culture-Office", ("AI-Center", "S/W-Culture-Office")),

        # prefix 불일치
        ("Engineers", None),
        ("", None),
        ("claude_backend", None),  # case-sensitive
        ("ClaudeAdmin", None),     # prefix 없음 (admin 부트스트랩은 별도 로직)

        # underscore 0개 — prefix 만 있고 tail 없음
        ("Claude_", None),

        # underscore 3개+ — 모호
        ("Claude_a_b_c", None),
        ("Claude_a_b_c_d", None),

        # 빈 세그먼트
        ("Claude__team", None),       # 부서 빈 문자열
        ("Claude_dept_", None),       # 팀 빈 문자열
        ("Claude__", None),
    ],
)
def test_parse_group(group_name: str, expected: tuple[str | None, str] | None) -> None:
    assert OIDCService._parse_group(group_name) == expected


# ── _upsert_user: 로그인 시 수동 지정 TEAM_LEADER 보존 ────────────────────────
# TEAM_LEADER 는 Cognito 그룹이 아니라 admin-ui("팀 리더 지정")에서만 부여된다 —
# derive_role 은 ADMIN/DEVELOPER 만 반환한다. 재로그인이 그 값을 DEVELOPER 로
# 되돌리지 않도록 보존하되, ADMIN 승격은 그대로 반영한다.

def _session():
    """AsyncMock 세션 — begin_nested 만 실제 async CM(MagicMock)으로 둔다."""
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=MagicMock())
    return session


def _session_without_leader_pointer():
    """``release_stale_leader_pointer`` 의 Team 조회가 '포인터 없음'을 보는 세션."""
    from unittest.mock import AsyncMock, MagicMock

    session = _session()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_upsert_user_preserves_manual_team_leader():
    """TEAM_LEADER 사용자가 재로그인(DEVELOPER 파생)해도 role 이 보존된다."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.auth import UserRole

    team_id = uuid.uuid4()
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.email = "dev@test.com"
    existing.display_name = "Dev"
    existing.role = UserRole.TEAM_LEADER
    existing.team_id = team_id

    with patch("app.services.oidc_service.UserRepository") as MockRepo, \
         patch("app.services.oidc_service.get_settings") as MockSettings:
        MockRepo.return_value.get_by_sso_subject = AsyncMock(return_value=existing)
        MockRepo.return_value.get_by_email = AsyncMock(return_value=None)
        MockSettings.return_value.OIDC_PROVIDER_NAME = "cognito"

        user, created, team_changed = await OIDCService._upsert_user(
            MagicMock(),
            session=_session_without_leader_pointer(),
            sso_subject="sub-1",
            email="dev@test.com",
            display_name="Dev",
            team_id=team_id,
            role=UserRole.DEVELOPER,  # Cognito 그룹에서 파생 — 리더 그룹은 없다
        )

    assert user.role == UserRole.TEAM_LEADER
    assert created is False


@pytest.mark.asyncio
async def test_upsert_user_admin_promotion_beats_team_leader():
    """ADMIN_GROUPS 매칭으로 파생된 ADMIN 은 TEAM_LEADER 보존보다 우선한다."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.auth import UserRole

    team_id = uuid.uuid4()
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.email = "dev@test.com"
    existing.display_name = "Dev"
    existing.role = UserRole.TEAM_LEADER
    existing.team_id = team_id

    with patch("app.services.oidc_service.UserRepository") as MockRepo, \
         patch("app.services.oidc_service.get_settings") as MockSettings:
        MockRepo.return_value.get_by_sso_subject = AsyncMock(return_value=existing)
        MockRepo.return_value.get_by_email = AsyncMock(return_value=None)
        MockSettings.return_value.OIDC_PROVIDER_NAME = "cognito"

        user, _, _ = await OIDCService._upsert_user(
            MagicMock(),
            session=_session_without_leader_pointer(),
            sso_subject="sub-1",
            email="dev@test.com",
            display_name="Dev",
            team_id=team_id,
            role=UserRole.ADMIN,
        )

    assert user.role == UserRole.ADMIN
