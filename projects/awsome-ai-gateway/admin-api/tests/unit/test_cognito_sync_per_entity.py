# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Per-entity Cognito sync (sync_user / sync_group) — 260626_comm_customer 항목1-a.

핵심 검증:
- DB 에 없는 사용자도 신규 생성 (고객 명시 케이스)
- 기존(sso_subject hit) → update / 재생성(email reconcile) → 새 sub 갱신
- ★ 전역 reconciliation(deactivate-missing / stale-team)을 절대 수행하지 않음
  (sync_all 과 달리 repo.list_users / list_all_teams 를 호출하면 안 됨)
- Cognito 에 없는 사용자 → 에러 대신 soft-delete(is_active=False), user_id 반환
- 그룹 sync: 팀 확보 + 멤버 upsert
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.auth import UserRole
from app.services.cognito_sync_service import CognitoSyncService


def _session():
    """AsyncMock 세션 — begin_nested 만 실제 async CM(MagicMock)으로 둔다.
    그냥 AsyncMock 이면 begin_nested() 가 코루틴을 반환해 async with 에서
    TypeError + never-awaited 경고가 난다."""
    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=MagicMock())
    return session


def _settings(monkeypatch):
    s = MagicMock()
    s.COGNITO_USER_POOL_ID = "pool-1"
    s.DEFAULT_TEAM_ID = str(uuid.uuid4())
    s.OIDC_PROVIDER_NAME = "cognito"
    s.OIDC_GROUP_PREFIX = "Claude_"
    s.ADMIN_EMAILS = []
    s.ADMIN_GROUPS = []
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "get_settings", lambda: s)
    return s


def _wire_team_cache(repo):
    """`_build_team_cache` 가 요구하는 경량 로더를 repo mock 에 달아준다.

    ⚠️ per-entity 경로(sync_user/sync_group)가 이제 전체 동기화와 **같은 팀 캐시**를
    쓴다. 예전엔 그룹 하나당 `_ensure_team` 으로 팀·부서를 DB 에서 다시 찾아
    라운드트립이 그룹 수만큼 쌓였다. 캐시 인프라는 이미 있었고 이 호출부만
    연결이 빠져 있었다.

    이 로더들이 없으면 `_build_team_cache` 가 MagicMock 을 await 하려다
    `TypeError: object MagicMock can't be used in 'await' expression` 로 터진다.
    """
    repo.list_departments_lite = AsyncMock(return_value=[])
    repo.list_teams_lite = AsyncMock(return_value=[])
    repo.get_first_org_id = AsyncMock(return_value=uuid.uuid4())
    repo.create_team = AsyncMock()
    repo.create_department = AsyncMock()
    return repo


def _admin_get_user(sub, email, name="N", enabled=True):
    return {
        "Username": email,
        "Enabled": enabled,
        "UserAttributes": [
            {"Name": "sub", "Value": sub},
            {"Name": "email", "Value": email},
            {"Name": "name", "Value": name},
        ],
    }


@pytest.mark.asyncio
async def test_sync_user_creates_when_absent(monkeypatch):
    """DB 에 없는 사용자 → 신규 생성 (고객 핵심 요구)."""
    s = _settings(monkeypatch)
    session = _session()
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=None)
    repo.get_by_email = AsyncMock(return_value=None)  # DB 에 전혀 없음
    repo.create_user = AsyncMock()
    # 전역 sweep 함수 — 호출되면 테스트 실패하도록 감지
    repo.list_users = AsyncMock(return_value=[])
    repo.list_all_teams = AsyncMock(return_value=[])

    _wire_team_cache(repo)

    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=_admin_get_user("NEW-SUB", "new@x.com"))
    svc._list_groups_for_user = MagicMock(return_value=[])  # 그룹 없음 → DEFAULT_TEAM

    result = await svc.sync_user(session, "new@x.com")

    repo.create_user.assert_awaited_once()
    assert result.users_created == 1
    # ★ 핵심 안전장치: 전역 deactivate-missing sweep(repo.list_users 로 전체 유저
    #   조회 후 비활성화) 절대 미수행. (_ensure_team 은 list_all_teams 로 팀을 '조회'
    #   할 수 있으므로 — 그건 무해한 read — list_all_teams 미호출은 단언하지 않는다.
    #   위험한 불변식은 'deactivation sweep 없음'이다.)
    repo.list_users.assert_not_called()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_sync_user_updates_existing(monkeypatch):
    """sso_subject hit → 기존 row update, create 안 함."""
    s = _settings(monkeypatch)
    session = _session()
    existing = MagicMock()
    existing.id = uuid.uuid4(); existing.sso_subject = "SUB-1"
    existing.email = "old@x.com"; existing.display_name = "Old"
    existing.role = UserRole.DEVELOPER; existing.team_id = uuid.UUID(s.DEFAULT_TEAM_ID)
    existing.is_active = True
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=existing)
    repo.create_user = AsyncMock()
    repo.list_users = AsyncMock(return_value=[])
    # update path flushes pending mutations before counting (cognito_sync_service.py:327)
    repo.flush = AsyncMock()
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=_admin_get_user("SUB-1", "new@x.com", "New"))
    svc._list_groups_for_user = MagicMock(return_value=[])

    result = await svc.sync_user(session, "new@x.com")

    repo.create_user.assert_not_called()
    assert existing.email == "new@x.com"  # updated
    assert result.users_updated == 1
    repo.list_users.assert_not_called()  # 전역 sweep 없음


@pytest.mark.asyncio
async def test_sync_user_reconciles_recreated_sub(monkeypatch):
    """재생성(새 sub, 같은 email): create 안 하고 기존 row 의 sso_subject 갱신."""
    s = _settings(monkeypatch)
    session = _session()
    existing = MagicMock()
    existing.id = uuid.uuid4(); existing.sso_subject = "OLD-SUB"
    existing.email = "a@x.com"; existing.display_name = "A"
    existing.role = UserRole.DEVELOPER; existing.team_id = uuid.UUID(s.DEFAULT_TEAM_ID)
    existing.is_active = True
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=None)  # 새 sub miss
    repo.get_by_email = AsyncMock(return_value=existing)     # email hit
    repo.create_user = AsyncMock()
    repo.list_users = AsyncMock(return_value=[])
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=_admin_get_user("NEW-SUB", "a@x.com"))
    svc._list_groups_for_user = MagicMock(return_value=[])

    await svc.sync_user(session, "a@x.com")

    repo.create_user.assert_not_called()
    assert existing.sso_subject == "NEW-SUB"  # reconciled


@pytest.mark.asyncio
async def test_sync_user_deactivates_oidc_user_when_missing_in_cognito(monkeypatch):
    """Cognito 에 없는 username(=삭제됨) + DB 에 활성 OIDC 유저 → is_active False,
    user_id 반환, users_deactivated=1, 에러 아님 (고객 항목1 핵심)."""
    s = _settings(monkeypatch)
    session = _session()
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.provider = s.OIDC_PROVIDER_NAME
    existing.is_active = True
    repo = MagicMock()
    repo.get_by_email = AsyncMock(return_value=existing)
    repo.create_user = AsyncMock()
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=None)  # Cognito 삭제됨

    result = await svc.sync_user(session, "gone@x.com")

    assert existing.is_active is False
    assert result.users_deactivated == 1
    assert result.user_id == str(existing.id)
    assert result.errors == []
    repo.create_user.assert_not_called()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_sync_user_missing_in_cognito_and_db_is_noop(monkeypatch):
    """Cognito 에도 DB 에도 없음 → no-op. user_id=None, deactivated=0, 에러 아님."""
    _settings(monkeypatch)
    session = _session()
    repo = MagicMock()
    repo.get_by_email = AsyncMock(return_value=None)
    repo.create_user = AsyncMock()
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=None)

    result = await svc.sync_user(session, "ghost@x.com")

    assert result.user_id is None
    assert result.users_deactivated == 0
    assert result.errors == []
    repo.create_user.assert_not_called()


@pytest.mark.asyncio
async def test_sync_user_missing_in_cognito_skips_non_oidc(monkeypatch):
    """Cognito 없음 + DB 유저가 비-OIDC(수동/서비스 계정) → 비활성화 안 함.
    단 찾았으므로 user_id 는 반환(email 충돌 사고 방지 안전장치)."""
    _settings(monkeypatch)
    session = _session()
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.provider = "manual"  # 비-OIDC
    existing.is_active = True
    repo = MagicMock()
    repo.get_by_email = AsyncMock(return_value=existing)
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=None)

    result = await svc.sync_user(session, "admin@x.com")

    assert existing.is_active is True  # 건드리지 않음
    assert result.users_deactivated == 0
    assert result.user_id == str(existing.id)


@pytest.mark.asyncio
async def test_sync_user_missing_in_cognito_already_inactive(monkeypatch):
    """Cognito 없음 + 이미 비활성 OIDC 유저 → deactivated 카운트 안 올림, user_id 반환."""
    s = _settings(monkeypatch)
    session = _session()
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.provider = s.OIDC_PROVIDER_NAME
    existing.is_active = False  # 이미 비활성
    repo = MagicMock()
    repo.get_by_email = AsyncMock(return_value=existing)
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=None)

    result = await svc.sync_user(session, "gone@x.com")

    assert result.users_deactivated == 0
    assert result.user_id == str(existing.id)


@pytest.mark.asyncio
async def test_sync_user_returns_user_id_on_create(monkeypatch):
    """정상 경로(생성) → 생성된 유저의 user_id 를 응답에 담는다(후속 처리용)."""
    s = _settings(monkeypatch)
    session = _session()
    created = {}

    async def _capture_create(user):
        created["user"] = user
        return user

    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=None)
    repo.get_by_email = AsyncMock(return_value=None)
    repo.create_user = AsyncMock(side_effect=_capture_create)
    repo.list_users = AsyncMock(return_value=[])
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=_admin_get_user("NEW-SUB", "new@x.com"))
    svc._list_groups_for_user = MagicMock(return_value=[])

    result = await svc.sync_user(session, "new@x.com")

    assert result.users_created == 1
    assert result.user_id == str(created["user"].id)


@pytest.mark.asyncio
async def test_sync_user_returns_user_id_on_update(monkeypatch):
    """정상 경로(갱신) → 기존 유저의 user_id 를 응답에 담는다."""
    s = _settings(monkeypatch)
    session = _session()
    existing = MagicMock()
    existing.id = uuid.uuid4(); existing.sso_subject = "SUB-1"
    existing.email = "old@x.com"; existing.display_name = "Old"
    existing.role = UserRole.DEVELOPER; existing.team_id = uuid.UUID(s.DEFAULT_TEAM_ID)
    existing.is_active = True
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=existing)
    repo.create_user = AsyncMock()
    # update path flushes pending mutations before counting (cognito_sync_service.py:327)
    repo.flush = AsyncMock()
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=_admin_get_user("SUB-1", "new@x.com", "New"))
    svc._list_groups_for_user = MagicMock(return_value=[])

    result = await svc.sync_user(session, "new@x.com")

    assert result.user_id == str(existing.id)


@pytest.mark.asyncio
async def test_sync_group_upserts_members_no_global_cleanup(monkeypatch):
    """그룹 sync: 팀 확보 + 멤버 upsert. 전역 정리(list_all_teams sweep) 미수행."""
    s = _settings(monkeypatch)
    session = _session()
    team = MagicMock(); team.id = uuid.uuid4()
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=None)
    repo.get_by_email = AsyncMock(return_value=None)
    repo.create_user = AsyncMock()
    repo.list_users = AsyncMock(return_value=[])
    repo.list_all_teams = AsyncMock(return_value=[])
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    # ⚠️ 이제 uuid 를 돌려주는 `_ensure_team_id` 를 탄다 — ORM 객체를 들고 있으면
    #    멤버 upsert 루프의 flush 이후 DetachedInstanceError 가 날 수 있어서 바뀌었다.
    svc._ensure_team_id = AsyncMock(return_value=team.id)
    svc._list_users_in_group = MagicMock(return_value=[
        {"Username": "m1@x.com", "Enabled": True, "Attributes": [
            {"Name": "sub", "Value": "S1"}, {"Name": "email", "Value": "m1@x.com"}]},
        {"Username": "m2@x.com", "Enabled": True, "Attributes": [
            {"Name": "sub", "Value": "S2"}, {"Name": "email", "Value": "m2@x.com"}]},
    ])

    result = await svc.sync_group(session, "Claude_dept_team")

    assert repo.create_user.await_count == 2
    assert result.users_created == 2
    assert result.groups_synced == 1
    repo.list_users.assert_not_called()  # ★ deactivate-missing sweep 없음 (위험 불변식)


# ──────────────────────────────────────────────────────────────────────────────
# per-entity 경로도 팀 캐시를 쓴다 (전체 동기화와 같은 경로)
# ──────────────────────────────────────────────────────────────────────────────
#
# `sync_all` 은 :98 에서 `_build_team_cache` 를 만들어 쓰는데 per-entity 경로만
# 캐시 없이 `_ensure_team` 을 탔다. `_ensure_team` 은 그룹 하나당 팀·부서를 DB 에서
# 다시 찾으므로, 그룹이 많은 사용자에서 라운드트립이 그룹 수만큼 쌓인다.
# 캐시 인프라(`_TeamCache`/`_build_team_cache`/`_ensure_team_id`)는 이미 있었고
# 이 호출부만 연결이 빠져 있었다.


@pytest.mark.asyncio
async def test_sync_user_resolves_teams_through_the_cache(monkeypatch):
    """sync_user 가 캐시를 만들어 그룹 해석에 넘기는지 — 그룹당 재조회가 아니어야 한다."""
    _settings(monkeypatch)
    session = _session()
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=None)
    repo.get_by_email = AsyncMock(return_value=None)
    repo.create_user = AsyncMock()
    _wire_team_cache(repo)
    import app.services.cognito_sync_service as mod
    monkeypatch.setattr(mod, "UserRepository", lambda sess: repo)

    svc = CognitoSyncService(MagicMock())
    svc._admin_get_user = MagicMock(return_value=_admin_get_user("S9", "u9@x.com"))
    # 그룹 3개 — 캐시가 없으면 팀 조회가 3번 나간다.
    svc._list_groups_for_user = MagicMock(
        return_value=["Claude_d1_t1", "Claude_d1_t2", "Claude_d2_t3"]
    )
    heavy = AsyncMock()
    svc._ensure_team = heavy  # 캐시를 쓰면 이 경로는 호출되지 않는다

    await svc.sync_user(session, "u9@x.com")

    assert repo.list_teams_lite.await_count == 1, (
        f"팀 캐시를 1회만 만들어야 한다 (실제 {repo.list_teams_lite.await_count}회)"
    )
    assert heavy.await_count == 0, (
        "캐시가 있는데도 무거운 _ensure_team 을 탔다 — 그룹당 DB 재조회가 일어난다"
    )


@pytest.mark.asyncio
async def test_sync_group_uses_the_uuid_returning_helper(monkeypatch):
    """sync_group 은 ORM 객체가 아니라 uuid 를 받아야 한다.

    멤버 upsert 루프가 중간에 flush/commit 을 하므로, ORM 객체를 들고 있으면
    expunge 이후 속성 접근이 DetachedInstanceError 로 터질 수 있다.
    """
    import inspect

    src = inspect.getsource(CognitoSyncService.sync_group)
    assert "_ensure_team_id(" in src, "uuid 반환 헬퍼를 쓰지 않는다"
    assert "team.id" not in src, (
        "ORM 객체의 속성을 참조한다 — flush 이후 DetachedInstanceError 위험"
    )
    assert "_build_team_cache(" in src, "팀 캐시를 만들지 않는다"


def test_the_no_cache_fallback_is_preserved():
    """`cache=None` 이면 예전 경로를 그대로 쓴다 — 기존 호출부 호환.

    `_ensure_team` 을 삭제하면 캐시를 넘기지 않는 호출부가 조용히 깨진다.
    """
    import inspect

    assert hasattr(CognitoSyncService, "_ensure_team"), (
        "_ensure_team 이 사라졌다 — cache 없이 호출하는 경로가 깨진다"
    )
    sig = inspect.signature(CognitoSyncService._resolve_team_id_from_groups)
    assert "cache" in sig.parameters, "cache 파라미터가 없다"
    assert sig.parameters["cache"].default is None, (
        "cache 가 기본값 None 이 아니다 — 기존 호출부가 깨진다"
    )


# ── _effective_role: admin-ui 수동 지정 TEAM_LEADER 보존 ──────────────────────
# TEAM_LEADER 는 Cognito 그룹이 아니라 admin-ui 에서만 부여된다(_derive_role 은
# ADMIN/DEVELOPER 만 반환). sync/재로그인이 그 값을 DEVELOPER 로 덮어쓰지 않도록
# 보존하되, ADMIN 승격/강등과 팀 이관 강등은 그대로 반영한다.

def test_effective_role_preserves_team_leader_on_sync():
    from app.services.cognito_sync_service import _effective_role

    team_id = uuid.uuid4()
    # 같은 팀에서 재동기화 — derived DEVELOPER 가 수동 지정 TEAM_LEADER 를 지우지 않는다
    assert _effective_role(
        UserRole.TEAM_LEADER, team_id, UserRole.DEVELOPER, team_id
    ) == UserRole.TEAM_LEADER


def test_effective_role_demotes_team_leader_on_team_change():
    """팀 리더십은 팀별 속성 — Cognito 그룹 재배정으로 팀이 바뀌면 강등."""
    from app.services.cognito_sync_service import _effective_role

    assert _effective_role(
        UserRole.TEAM_LEADER, uuid.uuid4(), UserRole.DEVELOPER, uuid.uuid4()
    ) == UserRole.DEVELOPER


def test_effective_role_admin_promotion_still_applies():
    """ADMIN_GROUPS 매칭 승격은 TEAM_LEADER 보존보다 우선한다."""
    from app.services.cognito_sync_service import _effective_role

    team_id = uuid.uuid4()
    assert _effective_role(
        UserRole.TEAM_LEADER, team_id, UserRole.ADMIN, team_id
    ) == UserRole.ADMIN


def test_effective_role_admin_demotion_still_applies():
    """ADMIN_GROUPS 에서 빠진 ADMIN 은 그대로 강등된다."""
    from app.services.cognito_sync_service import _effective_role

    team_id = uuid.uuid4()
    assert _effective_role(
        UserRole.ADMIN, team_id, UserRole.DEVELOPER, team_id
    ) == UserRole.DEVELOPER


def test_needs_update_does_not_flag_preserved_team_leader():
    """TEAM_LEADER 보존자를 매번 "role 다름"으로 오판해 불필요 upsert 를 타지 않도록
    _needs_update 도 _effective_role 기준으로 비교한다."""
    from app.services.cognito_sync_service import _needs_update

    team_id = uuid.uuid4()
    snap = {
        "email": "dev@test.com",
        "display_name": "Dev",
        "team_id": team_id,
        "role": UserRole.TEAM_LEADER,
        "is_active": True,
    }
    # Cognito 파생값은 DEVELOPER 지만 effective role 은 TEAM_LEADER — 변경 없음
    assert not _needs_update(
        snap, email="dev@test.com", name="Dev",
        team_id=team_id, role=UserRole.DEVELOPER, enabled=True,
    )


@pytest.mark.asyncio
async def test_upsert_one_user_releases_stale_pointer_with_session(monkeypatch):
    """회귀: _upsert_one_user 가 session 을 받아 release_stale_leader_pointer 를
    실제 호출한다. session 파라미터가 빠지면 NameError 가 except 에 삼켜져
    정리 경로가 dead code 가 되고 sync 마다 warning 만 쌓인다."""
    _settings(monkeypatch)
    session = MagicMock()
    old_team, new_team = uuid.uuid4(), uuid.uuid4()
    existing = MagicMock()
    existing.id = uuid.uuid4(); existing.sso_subject = "SUB-1"
    existing.email = "a@x.com"; existing.display_name = "A"
    existing.role = UserRole.TEAM_LEADER; existing.team_id = old_team
    existing.is_active = True
    repo = MagicMock()
    repo.get_by_sso_subject = AsyncMock(return_value=existing)
    repo.get_by_email = AsyncMock(return_value=None)
    repo.flush = AsyncMock()

    import app.services.cognito_sync_service as mod
    release = AsyncMock(return_value=1)
    monkeypatch.setattr(mod, "release_stale_leader_pointer", release)

    svc = CognitoSyncService(MagicMock())
    result = mod.SyncResult()
    await svc._upsert_one_user(
        repo, session, sub="SUB-1", email="a@x.com", name="A",
        enabled=True, team_id=new_team, role=UserRole.DEVELOPER, result=result,
    )

    release.assert_awaited_once()
    args, kwargs = release.await_args
    assert args[0] is session
    assert kwargs["user_id"] == existing.id
    assert kwargs["team_id"] == new_team  # 옛 팀이 아니라 확정된 새 상태
    assert result.users_updated == 1


@pytest.mark.asyncio
async def test_deactivate_missing_user_clears_leader_pointer(monkeypatch):
    """비활성화된 사용자를 가리키는 leader_user_id 도 함께 정리한다."""
    s = _settings(monkeypatch)
    session = _session()
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.email = "gone@x.com"
    existing.role = UserRole.TEAM_LEADER
    existing.team_id = uuid.uuid4()
    existing.is_active = True
    existing.provider = s.OIDC_PROVIDER_NAME
    repo = MagicMock()
    repo.get_by_email = AsyncMock(return_value=existing)

    import app.services.cognito_sync_service as mod
    release = AsyncMock(return_value=1)
    monkeypatch.setattr(mod, "release_stale_leader_pointer", release)

    svc = CognitoSyncService(MagicMock())
    result = mod.SyncResult()
    await svc._deactivate_missing_user(repo, session, "gone@x.com", result)

    assert existing.is_active is False
    release.assert_awaited_once()
    _, kwargs = release.await_args
    assert kwargs["is_active"] is False
    assert result.users_deactivated == 1
