# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.encryption import AESEncryptionService
from app.core.exceptions import NotFoundError
from app.models.auth import KeyStatus, User, VirtualKey
from app.services.key_service import KeyService, VK_PREFIX


@pytest.fixture
def key_service(encryption: AESEncryptionService, cache_mgr: CacheInvalidationManager) -> KeyService:
    return KeyService(encryption=encryption, cache_mgr=cache_mgr)


def _stub_user(user_id: uuid.UUID, team_id: uuid.UUID | None = None) -> MagicMock:
    u = MagicMock(spec=User)
    u.id = user_id
    u.team_id = team_id
    # issue_key snapshots user.sso_subject into the JSON AuthContext cache payload
    # (key_service.py:133/145). A bare MagicMock attr is not JSON-serializable, so
    # pin it to the real-world value (None for non-SSO users).
    u.sso_subject = None
    return u


class TestIssueKey:
    @pytest.fixture(autouse=True)
    def _no_user_level_acl(self):
        """issue_key 가 조회하는 **사용자 단위** ACL repo 두 개를 '엔트리 없음' 으로 고정.

        이 클래스의 관심사는 VK 발급·TTL·팀 스냅샷이다. 두 repo 를 패치하지 않으면
        mock_session(AsyncMock) 위에서 진짜 repo 가 돌아 MagicMock 이 돌아오고,
        json.dumps 가 터져 스냅샷 자체가 건너뛰어진다 — 아래 캐시 관련 단정들이
        StopIteration 으로 죽거나(운이 좋은 경우) 조용히 무의미해진다.

        ⚠️ 여기서 [] 로 고정했으므로 **이 파일은 user > team 우선순위를 검증하지 않는다.**
           그 계약과 allowed_clients 스냅샷은
           tests/regression/test_critical_vk_authcontext_acl_snapshot.py 가 못 박는다.
        """
        with patch("app.services.key_service.UserAllowedModelRepository") as MockUam, \
             patch("app.services.key_service.UserAllowedClientRepository") as MockUac:
            MockUam.return_value.list_by_user = AsyncMock(return_value=[])
            MockUac.return_value.list_by_user = AsyncMock(return_value=[])
            yield

    def _mock_repo(self, MockRepo, *, expire_count=0):
        repo = MockRepo.return_value
        # issue_key now expires existing keys and inserts the new one atomically in a
        # single CTE: `expired_count, _ = await repo.expire_and_create(user_id, vk)`.
        # Mock the real method with its real return shape: (expired_count, new_id).
        # issue_key sets vk.issued_at itself, so the old _populate_dates hook is obsolete.
        repo.expire_and_create = AsyncMock(return_value=(expire_count, uuid.uuid4()))
        # issue_key dedup path awaits repo.list_active_for_user before generating a new key.
        repo.list_active_for_user = AsyncMock(return_value=[])
        return repo

    async def test_issue_key_generates_vk_prefix(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        user_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            self._mock_repo(MockRepo)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                result = await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user)

        assert result.virtual_key.startswith(VK_PREFIX)
        assert len(result.virtual_key) == 67  # vk- + 64 hex chars
        assert result.key_prefix == result.virtual_key[:11]
        assert result.status == KeyStatus.ACTIVE

    async def test_issue_key_expires_existing_active_keys(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        user_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            repo = self._mock_repo(MockRepo, expire_count=2)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user)

        # expire + insert are now one atomic CTE: expire_and_create(user_id, vk)
        repo.expire_and_create.assert_called_once()
        assert repo.expire_and_create.call_args[0][0] == user_id

    async def test_issue_key_defaults_to_24h_without_expires_at(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        user_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            self._mock_repo(MockRepo)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                result = await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user)

        expected_min = datetime.now(timezone.utc) + timedelta(hours=23)
        expected_max = datetime.now(timezone.utc) + timedelta(hours=25)
        assert expected_min < result.expires_at < expected_max

    async def test_issue_key_encrypts_and_caches(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        user_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            self._mock_repo(MockRepo)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                result = await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user)

        # key:vk:{hash} is stored with setex(ttl, user_id) — FR-2.2 TTL enforcement
        vk_setex_calls = [c for c in mock_redis.setex.call_args_list if c[0][0].startswith("key:vk:")]
        assert len(vk_setex_calls) == 1
        assert vk_setex_calls[0][0][1] > 0  # TTL must be positive

        # Encryption roundtrip: the VK stored in the model is encrypted.
        # expire_and_create(user_id, vk) — the VirtualKey is the 2nd positional arg.
        created_vk = MockRepo.return_value.expire_and_create.call_args[0][1]
        decrypted = key_service._encryption.decrypt(created_vk.key_value_encrypted)
        assert decrypted == result.virtual_key

    async def test_issue_key_redis_ttl_matches_vk_expiry(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        """FR-2.2: key:vk:{hash} TTL must match the VK lifetime."""
        user_id = uuid.uuid4()
        expiry = datetime.now(timezone.utc) + timedelta(hours=8)

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            self._mock_repo(MockRepo)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user, expires_at=expiry)

        vk_setex_calls = [c for c in mock_redis.setex.call_args_list if c[0][0].startswith("key:vk:")]
        ttl = vk_setex_calls[0][0][1]
        assert 28795 < ttl <= 28800  # ~8 hours, ±5s tolerance

        # AuthContext cache TTL is capped at min(300, ttl)
        ctx_setex_calls = [c for c in mock_redis.setex.call_args_list if c[0][0].startswith("key:cache:vk:")]
        ctx_ttl = ctx_setex_calls[0][0][1]
        assert ctx_ttl == 300  # 8h > 300s so cap applies

    async def test_issue_key_auth_cache_ttl_capped_when_vk_near_expiry(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        """When VK lifetime < 300s, AuthContext cache TTL must not exceed remaining lifetime."""
        user_id = uuid.uuid4()
        expiry = datetime.now(timezone.utc) + timedelta(seconds=60)

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            self._mock_repo(MockRepo)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user, expires_at=expiry)

        ctx_setex_calls = [c for c in mock_redis.setex.call_args_list if c[0][0].startswith("key:cache:vk:")]
        ctx_ttl = ctx_setex_calls[0][0][1]
        assert ctx_ttl <= 60  # must not exceed the 60s remaining lifetime

    async def test_issue_key_sso_session_expires_at_caps_expiry(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """FR-2.2: sso_session_expires_at shorter than rotation policy caps VK expiry."""
        user_id = uuid.uuid4()
        sso_expiry = datetime.now(timezone.utc) + timedelta(hours=8)

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            self._mock_repo(MockRepo)  # policy=None → default 90 days
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                result = await key_service.issue_key(
                    mock_session,
                    user_id=user_id,
                    actor=admin_user,
                    sso_session_expires_at=sso_expiry,
                )

        # 90-day policy > 8h SSO session, so VK expiry must equal SSO expiry
        assert abs((result.expires_at - sso_expiry).total_seconds()) < 2

    async def test_issue_key_sso_expiry_ignored_when_longer_than_default(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """sso_session_expires_at longer than the default 24h TTL leaves expiry unchanged.

        issue_key caps VK expiry to the SSO session only when the SSO session is
        *shorter* than the computed expiry (key_service.py:73). A 30-day SSO session
        is longer than the 24h default, so the default expiry must survive untouched.
        """
        user_id = uuid.uuid4()
        sso_expiry = datetime.now(timezone.utc) + timedelta(days=30)

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            self._mock_repo(MockRepo)  # no expires_at → default 24h
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
            MockTam.return_value.list_by_team = AsyncMock(return_value=[])

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                result = await key_service.issue_key(
                    mock_session,
                    user_id=user_id,
                    actor=admin_user,
                    sso_session_expires_at=sso_expiry,
                )

        expected_min = datetime.now(timezone.utc) + timedelta(hours=23)
        expected_max = datetime.now(timezone.utc) + timedelta(hours=25)
        assert expected_min < result.expires_at < expected_max


    async def test_issue_key_snapshots_team_allowed_models_into_cache(
        self,
        key_service: KeyService,
        mock_session: AsyncMock,
        admin_user: CurrentUser,
        mock_redis: AsyncMock,
    ):
        """FR-2.6: VK 발급 시 user의 team_allowed_models를 AuthContext 캐시에 주입."""
        import json

        user_id = uuid.uuid4()
        team_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository") as MockTam:
            repo = MockRepo.return_value
            repo.expire_and_create = AsyncMock(return_value=(0, uuid.uuid4()))
            repo.list_active_for_user = AsyncMock(return_value=[])
            MockUserRepo.return_value.get_user = AsyncMock(
                return_value=_stub_user(user_id, team_id)
            )
            MockTam.return_value.list_by_team = AsyncMock(
                return_value=["claude-haiku", "claude-sonnet"]
            )

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user)

        # AuthContext 캐시에 allowed_models 스냅샷 포함
        setex_calls = mock_redis.setex.call_args_list
        auth_cache_call = next(c for c in setex_calls if c[0][0].startswith("key:cache:vk:"))
        payload = json.loads(auth_cache_call[0][2])
        assert payload["allowed_models"] == ["claude-haiku", "claude-sonnet"]
        assert payload["team_id"] == str(team_id)

        # Reverse index에 VK hash 추가 (팀 범위 invalidate용)
        mock_redis.sadd.assert_called_once()
        assert mock_redis.sadd.call_args[0][0] == f"team:vk_hashes:{team_id}"

    async def test_issue_key_no_team_means_allowed_models_none(
        self,
        key_service: KeyService,
        mock_session: AsyncMock,
        admin_user: CurrentUser,
        mock_redis: AsyncMock,
    ):
        """team_id 없는 user → allowed_models=None (전체 허용)."""
        import json

        user_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.TeamAllowedModelRepository"):
            repo = MockRepo.return_value
            repo.expire_and_create = AsyncMock(return_value=(0, uuid.uuid4()))
            repo.list_active_for_user = AsyncMock(return_value=[])
            MockUserRepo.return_value.get_user = AsyncMock(
                return_value=_stub_user(user_id, team_id=None)
            )

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                await key_service.issue_key(mock_session, user_id=user_id, actor=admin_user)

        setex_calls = mock_redis.setex.call_args_list
        auth_cache_call = next(c for c in setex_calls if c[0][0].startswith("key:cache:vk:"))
        payload = json.loads(auth_cache_call[0][2])
        assert payload["allowed_models"] is None
        assert payload["team_id"] == ""
        mock_redis.sadd.assert_not_called()


class TestRevokeKey:
    async def test_revoke_key_not_found_raises(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        key_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_by_id = AsyncMock(return_value=None)
            repo.revoke = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await key_service.revoke_key(mock_session, key_id=key_id, actor=admin_user)

            # ACTIVE 체크는 행 잠금(FOR UPDATE)으로 읽어야 동시 폐기의 이중 발행을 막는다
            repo.get_by_id.assert_called_once_with(key_id, for_update=True)

    async def test_revoke_key_invalidates_cache(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        key_id = uuid.uuid4()
        raw_key = "vk-" + "a" * 64
        encrypted = key_service._encryption.encrypt(raw_key)

        vk = MagicMock(spec=VirtualKey)
        vk.key_value_encrypted = encrypted
        vk.user_id = uuid.uuid4()
        vk.status = KeyStatus.ACTIVE

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo:
            repo = MockRepo.return_value
            repo.get_by_id = AsyncMock(return_value=vk)
            repo.revoke = AsyncMock(return_value=vk)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(vk.user_id))

            with patch("app.services.key_service.audit_logger") as mock_audit:
                mock_audit.log = AsyncMock()
                await key_service.revoke_key(mock_session, key_id=key_id, actor=admin_user)

        # Redis DEL was called for two keys
        assert mock_redis.delete.call_count >= 2


class TestListActiveVkHashesForUser:
    async def test_list_active_vk_hashes_for_user_returns_hashes(
        self, key_service: KeyService, mock_session: AsyncMock
    ):
        user_id = uuid.uuid4()
        vk1 = MagicMock(id=uuid.uuid4(), key_value_encrypted=b"enc-1")
        vk2 = MagicMock(id=uuid.uuid4(), key_value_encrypted=b"enc-2")

        with patch("app.services.key_service.KeyRepository") as KRepo:
            KRepo.return_value.list_active_for_user = AsyncMock(return_value=[vk1, vk2])
            key_service._encryption.decrypt = MagicMock(side_effect=lambda b: f"raw-{b.decode()}")

            hashes = await key_service.list_active_vk_hashes_for_user(mock_session, user_id)

        expected = [
            hashlib.sha256("raw-enc-1".encode()).hexdigest(),
            hashlib.sha256("raw-enc-2".encode()).hexdigest(),
        ]
        assert hashes == expected

    async def test_list_active_vk_hashes_for_user_empty(
        self, key_service: KeyService, mock_session: AsyncMock
    ):
        user_id = uuid.uuid4()
        with patch("app.services.key_service.KeyRepository") as KRepo:
            KRepo.return_value.list_active_for_user = AsyncMock(return_value=[])
            hashes = await key_service.list_active_vk_hashes_for_user(mock_session, user_id)
        assert hashes == []

    async def test_list_active_vk_hashes_for_user_skips_decrypt_failures(
        self, key_service: KeyService, mock_session: AsyncMock
    ):
        user_id = uuid.uuid4()
        vk1 = MagicMock(id=uuid.uuid4(), key_value_encrypted=b"good")
        vk2 = MagicMock(id=uuid.uuid4(), key_value_encrypted=b"bad")

        with patch("app.services.key_service.KeyRepository") as KRepo:
            KRepo.return_value.list_active_for_user = AsyncMock(return_value=[vk1, vk2])

            def _decrypt(b: bytes) -> str:
                if b == b"bad":
                    raise ValueError("decrypt error")
                return "raw-good"
            key_service._encryption.decrypt = MagicMock(side_effect=_decrypt)

            hashes = await key_service.list_active_vk_hashes_for_user(mock_session, user_id)

        assert hashes == [hashlib.sha256("raw-good".encode()).hexdigest()]

    @pytest.mark.asyncio
    async def test_list_active_vk_hashes_excludes_expired_keys(
        self, key_service: KeyService, mock_session: AsyncMock
    ):
        """EXPIRED keys (status=EXPIRED, revoked_at=NULL) must not be returned.

        The status=ACTIVE filter is enforced at the repository layer.
        This test verifies that list_active_vk_hashes_for_user passes through
        only what the repo returns — i.e. the repo is the single gating point
        for the status filter, and callers receive only hashes for ACTIVE keys.
        """
        user_id = uuid.uuid4()
        active_vk = MagicMock(id=uuid.uuid4(), key_value_encrypted=b"active")

        with patch("app.services.key_service.KeyRepository") as KRepo:
            # Repo returns only the active VK — expired ones are filtered at DB level.
            KRepo.return_value.list_active_for_user = AsyncMock(return_value=[active_vk])
            key_service._encryption.decrypt = MagicMock(return_value="raw-active")
            hashes = await key_service.list_active_vk_hashes_for_user(mock_session, user_id)

        assert hashes == [hashlib.sha256("raw-active".encode()).hexdigest()]


class TestForceReauthTeam:
    """PR 4-A: 팀 멤버 전원 ACTIVE VK 일괄 revoke.

    `revoke_key` 루프를 검증. revoke_key 자체는 다른 테스트에서 이미 커버되므로
    여기서는 (1) repo 에 status=ACTIVE 필터 전달, (2) 각 VK 에 대해 revoke_key 호출,
    (3) revoked_count 반환, (4) audit log 한 건만 집중.
    """

    @pytest.mark.asyncio
    async def test_force_reauth_team_revokes_all_active_keys(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()
        vk1 = MagicMock(id=uuid.uuid4())
        vk2 = MagicMock(id=uuid.uuid4())
        vk3 = MagicMock(id=uuid.uuid4())

        with patch("app.services.key_service.KeyRepository") as KRepo:
            KRepo.return_value.list_keys = AsyncMock(return_value=[vk1, vk2, vk3])
            with patch.object(key_service, "revoke_key", new=AsyncMock()) as mock_revoke:
                with patch("app.services.key_service.audit_logger.log", new=AsyncMock()):
                    count = await key_service.force_reauth_team(
                        mock_session, team_id=team_id, actor=admin_user
                    )

        assert count == 3
        # list_keys 는 team_id + status=ACTIVE 로 호출됐어야 함
        call_kwargs = KRepo.return_value.list_keys.call_args.kwargs
        assert call_kwargs["team_id"] == team_id
        assert call_kwargs["status"] == KeyStatus.ACTIVE
        # revoke_key 가 각 VK 에 대해 한 번씩 호출됨
        assert mock_revoke.call_count == 3
        revoked_key_ids = {c.kwargs["key_id"] for c in mock_revoke.call_args_list}
        assert revoked_key_ids == {vk1.id, vk2.id, vk3.id}

    @pytest.mark.asyncio
    async def test_force_reauth_team_returns_zero_when_no_active_keys(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        team_id = uuid.uuid4()

        with patch("app.services.key_service.KeyRepository") as KRepo:
            KRepo.return_value.list_keys = AsyncMock(return_value=[])
            with patch.object(key_service, "revoke_key", new=AsyncMock()) as mock_revoke:
                with patch("app.services.key_service.audit_logger.log", new=AsyncMock()):
                    count = await key_service.force_reauth_team(
                        mock_session, team_id=team_id, actor=admin_user
                    )

        assert count == 0
        mock_revoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_reauth_team_continues_on_single_revoke_failure(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """한 건 revoke 실패해도 나머지 처리 계속, count 는 성공한 것만."""
        team_id = uuid.uuid4()
        vk_ok1 = MagicMock(id=uuid.uuid4())
        vk_fail = MagicMock(id=uuid.uuid4())
        vk_ok2 = MagicMock(id=uuid.uuid4())

        async def revoke_side_effect(*args, **kwargs):
            if kwargs["key_id"] == vk_fail.id:
                raise RuntimeError("redis down")

        with patch("app.services.key_service.KeyRepository") as KRepo:
            KRepo.return_value.list_keys = AsyncMock(return_value=[vk_ok1, vk_fail, vk_ok2])
            with patch.object(key_service, "revoke_key", new=AsyncMock(side_effect=revoke_side_effect)) as mock_revoke:
                with patch("app.services.key_service.audit_logger.log", new=AsyncMock()):
                    count = await key_service.force_reauth_team(
                        mock_session, team_id=team_id, actor=admin_user
                    )

        assert count == 2  # 성공한 2건만
        assert mock_revoke.call_count == 3  # 3건 모두 시도함


class TestIssueKeyDedup:
    """issue_key dedup — VK_DEDUP_SECONDS(5초) 안의 연속 발급은 기존 ACTIVE 키 재반환.

    단, 기존 키가 **이번 요청의 유효 만료** (요청 expires_at, SSO 상한 적용 후) 를
    넘지 않을 때만 재사용한다. 그렇지 않으면 세션/TTL 정책보다 오래 사는 키가
    재사용되어 세션 종료 뒤에도 살아남는다 — 24h STS 키가 1h Cowork 세션에
    재사용된 사고가 이 조건이 거꾸로였을 때 발생했다.
    """

    def _existing_vk(
        self,
        key_service: KeyService,
        *,
        age_s: float = 2.0,
        ttl_h: float = 1.0,
    ) -> tuple[MagicMock, str]:
        now = datetime.now(timezone.utc)
        raw_key = "vk-" + "b" * 64
        vk = MagicMock(spec=VirtualKey)
        vk.id = uuid.uuid4()
        vk.key_value_encrypted = key_service._encryption.encrypt(raw_key)
        vk.key_prefix = raw_key[:11]
        vk.user_id = uuid.uuid4()
        vk.status = KeyStatus.ACTIVE
        vk.issued_at = now - timedelta(seconds=age_s)
        vk.expires_at = now + timedelta(hours=ttl_h)
        return vk, raw_key

    async def test_dedup_returns_recent_key_within_effective_expiry(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """5초 내 연속 발급 + 기존 키가 유효 만료 안 → 새로 만들지 않고 재반환."""
        user_id = uuid.uuid4()
        existing, raw_key = self._existing_vk(key_service, age_s=2, ttl_h=1)

        with patch("app.services.key_service.KeyRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.list_active_for_user = AsyncMock(return_value=[existing])
            repo.expire_and_create = AsyncMock()

            result = await key_service.issue_key(
                mock_session, user_id=user_id, actor=admin_user
            )

        repo.expire_and_create.assert_not_called()
        assert result.virtual_key == raw_key
        assert result.key_id == str(existing.id)
        assert result.expires_at == existing.expires_at

    async def _issue_and_get_repo(
        self, key_service, mock_session, admin_user, user_id, existing, **issue_kwargs
    ):
        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.UserAllowedModelRepository") as MockUam, \
             patch("app.services.key_service.UserAllowedClientRepository") as MockUac, \
             patch("app.services.key_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.list_active_for_user = AsyncMock(return_value=[existing])
            repo.expire_and_create = AsyncMock(return_value=(0, uuid.uuid4()))
            MockUserRepo.return_value.get_user = AsyncMock(
                return_value=_stub_user(user_id, team_id=None)
            )
            MockUam.return_value.list_by_user = AsyncMock(return_value=[])
            MockUac.return_value.list_by_user = AsyncMock(return_value=[])
            mock_audit.log = AsyncMock()

            result = await key_service.issue_key(
                mock_session, user_id=user_id, actor=admin_user, **issue_kwargs
            )
        return repo, result

    async def test_dedup_does_not_reuse_key_outliving_sso_session(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser,
        mock_redis: AsyncMock
    ):
        """거꾸로 된 조건의 회귀: 24h STS 키(2초 전 발급)가 있는데 1h SSO 세션으로
        다시 요청하면 재사용하면 안 된다 — 세션 종료 뒤 23시간을 더 사니까."""
        user_id = uuid.uuid4()
        existing, _ = self._existing_vk(key_service, age_s=2, ttl_h=24)
        sso = datetime.now(timezone.utc) + timedelta(hours=1)

        repo, result = await self._issue_and_get_repo(
            key_service, mock_session, admin_user, user_id, existing,
            sso_session_expires_at=sso,
        )

        repo.expire_and_create.assert_called_once()
        # 새 키는 SSO 세션 상한으로 제한됐어야 한다
        assert result.expires_at <= sso
        assert result.key_id != str(existing.id)

    async def test_dedup_does_not_reuse_key_outliving_requested_ttl(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser,
        mock_redis: AsyncMock
    ):
        """호출자가 짧은 expires_at 을 명시해도 같은 규칙 — 그보다 오래 사는
        기존 키는 재사용하지 않는다(dedup 창 안에서 expires_at 무시 금지)."""
        user_id = uuid.uuid4()
        existing, _ = self._existing_vk(key_service, age_s=2, ttl_h=24)
        short_ttl = datetime.now(timezone.utc) + timedelta(hours=1)

        repo, result = await self._issue_and_get_repo(
            key_service, mock_session, admin_user, user_id, existing,
            expires_at=short_ttl,
        )

        repo.expire_and_create.assert_called_once()
        assert result.expires_at <= short_ttl

    async def test_dedup_ignores_keys_older_than_window(
        self, key_service: KeyService, mock_session: AsyncMock, admin_user: CurrentUser,
        mock_redis: AsyncMock
    ):
        """dedup 창(5초)보다 오래된 키는 만료가 짧아도 재사용하지 않는다."""
        user_id = uuid.uuid4()
        existing, _ = self._existing_vk(key_service, age_s=60, ttl_h=1)

        repo, _ = await self._issue_and_get_repo(
            key_service, mock_session, admin_user, user_id, existing,
        )

        repo.expire_and_create.assert_called_once()


class TestPendingRedisPublish:
    """key_revoked 이벤트는 큐→drain 분리 — commit 성공 직후에만 Redis 로 나간다.

    트랜잭션 안에서 바로 publish 하면 롤백 시 "취소된 폐기"의 알림이 나간다.
    이 클래스는 (1) revoke_key 가 publish 를 부르지 않고 큐에만 쌓고,
    (2) drain 이 큐를 비우며 publish 한다, (3) bulk 표시가 실린다,
    (4) force_reauth 의 실패분 이벤트는 큐에서 걷어낸다 를 못 박는다.
    """

    def _active_vk(self, key_service: KeyService) -> MagicMock:
        raw_key = "vk-" + "b" * 64
        vk = MagicMock(spec=VirtualKey)
        vk.id = uuid.uuid4()
        vk.key_prefix = "vk-bbbb"
        vk.key_value_encrypted = key_service._encryption.encrypt(raw_key)
        vk.user_id = uuid.uuid4()
        vk.status = KeyStatus.ACTIVE
        return vk

    async def test_revoke_queues_event_and_drain_publishes_after_commit(
        self, key_service: KeyService, mock_session: AsyncMock,
        admin_user: CurrentUser, mock_redis: AsyncMock,
    ):
        from app.core.db import PENDING_REDIS_KEY, drain_pending_redis

        vk = self._active_vk(key_service)
        mock_session.get = AsyncMock(return_value=vk)

        with patch("app.services.key_service.KeyRepository") as MockRepo, \
             patch("app.services.key_service.UserRepository") as MockUserRepo, \
             patch("app.services.key_service.audit_logger") as mock_audit:
            MockRepo.return_value.get_by_id = AsyncMock(return_value=vk)
            MockRepo.return_value.revoke = AsyncMock(return_value=vk)
            MockUserRepo.return_value.get_user = AsyncMock(return_value=_stub_user(vk.user_id))
            mock_audit.log = AsyncMock()
            await key_service.revoke_key(mock_session, key_id=vk.id, actor=admin_user)

        # 커밋 전에는 아무것도 발행되지 않는다 — 큐에만 있다
        mock_redis.publish.assert_not_called()
        pending = mock_session.info[PENDING_REDIS_KEY]
        assert len(pending) == 1
        channel, payload_json = pending[0]
        event = json.loads(payload_json)
        assert channel == "notifications:key"
        assert event["type"] == "key_revoked"
        assert event["payload"]["bulk"] is False

        # drain(= 커밋 성공 직후)에서만 publish — 큐는 비워진다
        await drain_pending_redis(mock_session, redis=mock_redis)
        mock_redis.publish.assert_called_once_with(channel, payload_json)
        assert mock_session.info.get(PENDING_REDIS_KEY, []) == []

        # 두 번째 drain 은 no-op — 이벤트 이중 발행 없음
        await drain_pending_redis(mock_session, redis=mock_redis)
        mock_redis.publish.assert_called_once()

    async def test_force_reauth_marks_bulk_and_pops_failed_key_event(
        self, key_service: KeyService, mock_session: AsyncMock,
        admin_user: CurrentUser, mock_redis: AsyncMock,
    ):
        """force_reauth 는 bulk=true 를 싣고, SAVEPOINT 롤백된 키의 이벤트는
        큐에서 걷어낸다 — 폐기되지 않은 키의 알림이 나가는 걸 막는다."""
        from app.core.db import PENDING_REDIS_KEY

        team_id = uuid.uuid4()
        vk_ok = self._active_vk(key_service)
        vk_fail = self._active_vk(key_service)
        # 실패 키는 발행까지만 간 뒤 죽는다 — SAVEPOINT 롤백과 같은 상황 재현
        failing_id = vk_fail.id

        async def fake_revoke(session, *, key_id, actor, ip_address="", request_id="", bulk=False):
            vk = vk_fail if key_id == failing_id else vk_ok
            await key_service._publish_key_revoked(session, vk, actor, bulk=bulk)
            if key_id == failing_id:
                raise RuntimeError("audit insert failed mid-revoke")

        with patch("app.services.key_service.KeyRepository") as KRepo, \
             patch.object(key_service, "revoke_key", new=AsyncMock(side_effect=fake_revoke)), \
             patch("app.services.key_service.audit_logger.log", new=AsyncMock()):
            KRepo.return_value.list_keys = AsyncMock(return_value=[vk_ok, vk_fail])
            count = await key_service.force_reauth_team(
                mock_session, team_id=team_id, actor=admin_user
            )

        assert count == 1
        pending = mock_session.info[PENDING_REDIS_KEY]
        assert len(pending) == 1  # 실패분 이벤트는 걷어내져 성공한 키 것만 남는다
        channel, payload_json = pending[0]
        event = json.loads(payload_json)
        assert event["payload"]["bulk"] is True
        assert event["payload"]["key_id"] == str(vk_ok.id)
