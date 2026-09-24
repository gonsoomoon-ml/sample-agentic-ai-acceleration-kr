# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
"""Regression: VK 발급 시 admin-api 가 심는 AuthContext 스냅샷이 인가 필드를 빠뜨려
gateway-proxy 가 캐시 TTL 동안 **제한 없음**으로 동작하던 결함.

왜 스냅샷 하나가 인가를 좌우하는가:
  gateway-proxy 는 `key:cache:vk:{hash}` 히트 시 그 JSON 을 **검증 없이** 되살린다 —
  `return AuthContext(**data)` (gateway-proxy/src/app/services/auth_service.py:66).
  AuthContext 의 인가 필드는 전부 `None` 기본값이고 None = "제한 없음" 이다
  (gateway-proxy/src/app/schemas/domain.py:85-94). 그래서 admin-api 가 키를 빠뜨리면
  pydantic 은 조용히 None 을 채우고, 캐시가 사는 동안(min(300s, VK 잔여수명))
  gateway 는 DB 를 다시 보지 않으므로 아무도 눈치채지 못한다.

두 군데가 새고 있었다:
  1) allowed_models 를 team_allowed_models **만으로** 채웠다. gateway 의 정책은
     user > team > none 인데(auth_service.py:87-121), user_allowed_models 로 더 좁혀
     놓은 사용자가 VK 를 새로 받으면 캐시 TTL 동안 **팀 화이트리스트로 넓어졌다**.
     사용자 단위 제한(국가핵심기술)이 통째로 무력화된다.
  2) allowed_clients 를 아예 넣지 않았다 → 기본값 None = 전 클라이언트 허용이라
     ClientAuthorizationMiddleware 의 403 이 통과한다
     (gateway-proxy/src/app/middleware/client_authz.py:36).

수정 방향:
  admin-api 가 gateway 와 **같은 우선순위·같은 필드**로 스냅샷을 계산한다. 그리고
  스냅샷은 콜드캐시 DB 왕복을 아끼는 **최적화**일 뿐이므로, ACL 조회가 실패하면 넓은
  스냅샷을 심는 대신 아예 심지 않는다 — gateway 가 첫 요청에서 직접 조회하고 자기
  fail-closed 규칙(auth_service.py:99-107)을 적용한다.

이 파일이 잡는 것:
  * (구조) payload 의 키 집합 == AuthContext 의 필드 집합. 새 인가 필드가 gateway 에
    추가됐는데 여기 스냅샷에 안 들어가면 그 필드는 캐시 히트마다 기본값(=무제한)이
    된다. AST 대조라 실행 없이 두 레포를 붙여 검사한다.
  * (동작) user override 가 팀을 덮는다 / 팀 폴백이 여전히 동작한다 / allowed_clients
    가 실린다 / ACL 조회 실패 시 스냅샷만 생략되고 발급은 성공한다.
"""
from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.session_double import wire_savepoint

from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.encryption import AESEncryptionService
from app.models.auth import User, UserRole
from app.services.key_service import KeyService

TEST_ENCRYPTION_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

TEAM_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_KEY_SERVICE_PY = _REPO_ROOT / "admin-api" / "src" / "app" / "services" / "key_service.py"
_GATEWAY_DOMAIN_PY = _REPO_ROOT / "gateway-proxy" / "src" / "app" / "schemas" / "domain.py"


# ──────────────────────────────────────────────────────────────────────────────
# 1) 구조 계약 — 스냅샷 키 집합 == AuthContext 필드 집합
# ──────────────────────────────────────────────────────────────────────────────


def _snapshot_keys() -> set[str]:
    """key_service.py 의 auth_context_payload 딕셔너리 리터럴 키들."""
    tree = ast.parse(_KEY_SERVICE_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "auth_context_payload" for t in node.targets
        ):
            continue
        keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        if len(keys) != len(node.value.keys):
            pytest.fail(
                "auth_context_payload 에 리터럴이 아닌 키(**spread 등)가 있다 — "
                "이 대조 테스트가 필드를 놓친다. 리터럴 dict 로 유지할 것."
            )
        return keys
    pytest.fail(f"{_KEY_SERVICE_PY} 에서 auth_context_payload 대입을 찾지 못했다")


def _auth_context_fields() -> set[str]:
    """gateway-proxy AuthContext 의 pydantic 필드들."""
    tree = ast.parse(_GATEWAY_DOMAIN_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AuthContext":
            return {
                s.target.id
                for s in node.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            }
    pytest.fail(f"{_GATEWAY_DOMAIN_PY} 에서 class AuthContext 를 찾지 못했다")


def test_snapshot_covers_every_authcontext_field():
    """gateway 의 AuthContext 필드 전부가 스냅샷에 있어야 한다.

    빠진 필드는 pydantic 기본값(인가 필드는 전부 None = 무제한)으로 되살아난다.
    """
    assert _GATEWAY_DOMAIN_PY.exists(), f"gateway 스키마가 없다: {_GATEWAY_DOMAIN_PY}"
    fields = _auth_context_fields()
    keys = _snapshot_keys()

    # 대조군 — 두 파서가 실제로 무언가를 찾았는지. 빈 집합끼리는 항상 같다.
    assert len(fields) >= 8, f"AuthContext 필드를 {len(fields)}개만 찾았다: {fields}"
    assert len(keys) >= 8, f"스냅샷 키를 {len(keys)}개만 찾았다: {keys}"

    missing = fields - keys
    assert not missing, (
        f"스냅샷에 없는 AuthContext 필드 {sorted(missing)} — gateway 는 캐시 히트마다 "
        f"이 필드를 기본값으로 되살린다(인가 필드면 '제한 없음'이 된다). "
        f"key_service.auth_context_payload 에 추가할 것."
    )
    extra = keys - fields
    assert not extra, (
        f"AuthContext 에 없는 키 {sorted(extra)} — pydantic 이 무시하므로 여기서 "
        f"넣어도 gateway 에는 반영되지 않는다(오해를 남긴다)."
    )


def test_authorization_fields_are_pinned_explicitly():
    """인가 축 두 개는 이름으로 못 박는다.

    위 테스트는 '두 쪽이 같다' 만 본다 — 양쪽에서 동시에 사라지면 통과한다.
    allowed_models/allowed_clients 는 이 결함의 본체라 따로 고정한다.
    """
    keys = _snapshot_keys()
    for field in ("allowed_models", "allowed_clients"):
        assert field in keys, f"{field} 가 스냅샷에서 사라졌다 — 무제한으로 되살아난다"


# ──────────────────────────────────────────────────────────────────────────────
# 2) 동작 — user > team 우선순위, allowed_clients, 실패 시 스냅샷 생략
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    # redis-py 의 async pipeline 은 명령을 **동기로** 버퍼링하고 execute() 만 await 한다.
    redis.setex = MagicMock()
    redis.sadd = MagicMock()
    redis.execute = AsyncMock()
    redis.pipeline = MagicMock(return_value=redis)
    return redis


@pytest.fixture
def key_service(mock_redis: AsyncMock) -> KeyService:
    return KeyService(
        encryption=AESEncryptionService(TEST_ENCRYPTION_KEY),
        cache_mgr=CacheInvalidationManager(mock_redis),
    )


@pytest.fixture
def actor() -> CurrentUser:
    return CurrentUser(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        email="admin@test.com",
        role=UserRole.ADMIN,
        team_id=TEAM_A,
    )


def _stub_user(user_id: uuid.UUID, team_id: uuid.UUID | None) -> MagicMock:
    u = MagicMock(spec=User)
    u.id = user_id
    u.team_id = team_id
    u.sso_subject = None  # MagicMock 속성은 JSON 직렬화가 안 된다
    return u


def _snapshot_written(mock_redis: AsyncMock) -> dict | None:
    """파이프라인에 버퍼된 key:cache:vk: 페이로드. 없으면 None."""
    for call in mock_redis.setex.call_args_list:
        if call[0][0].startswith("key:cache:vk:"):
            return json.loads(call[0][2])
    return None


def _vk_lookup_written(mock_redis: AsyncMock) -> bool:
    return any(c[0][0].startswith("key:vk:") for c in mock_redis.setex.call_args_list)


async def _issue(
    key_service: KeyService,
    actor: CurrentUser,
    *,
    user_id: uuid.UUID,
    team_id: uuid.UUID | None,
    user_aliases: list[str] | Exception,
    team_aliases: list[str],
    clients: list[str] | Exception,
):
    session = AsyncMock()
    wire_savepoint(session)  # begin_nested 는 sync 호출 → async CM (실물과 동일)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    def _mk(value):
        if isinstance(value, Exception):
            return AsyncMock(side_effect=value)
        return AsyncMock(return_value=value)

    with patch("app.services.key_service.KeyRepository") as MockRepo, \
         patch("app.services.key_service.UserRepository") as MockUserRepo, \
         patch("app.services.key_service.TeamAllowedModelRepository") as MockTam, \
         patch("app.services.key_service.UserAllowedModelRepository") as MockUam, \
         patch("app.services.key_service.UserAllowedClientRepository") as MockUac, \
         patch("app.services.key_service.audit_logger") as mock_audit:
        MockRepo.return_value.expire_and_create = AsyncMock(return_value=(0, uuid.uuid4()))
        # VK dedup(VK_DEDUP_SECONDS)이 발급 전 최근 ACTIVE 키를 조회한다 — 빈 리스트면
        # dedup 없이 expire_and_create 경로로 진행한다.
        MockRepo.return_value.list_active_for_user = AsyncMock(return_value=[])
        MockUserRepo.return_value.get_user = AsyncMock(
            return_value=_stub_user(user_id, team_id)
        )
        MockTam.return_value.list_by_team = _mk(team_aliases)
        MockUam.return_value.list_by_user = _mk(user_aliases)
        MockUac.return_value.list_by_user = _mk(clients)
        mock_audit.log = AsyncMock()

        return await key_service.issue_key(session, user_id=user_id, actor=actor)


@pytest.mark.asyncio
async def test_user_level_whitelist_beats_the_team_whitelist(
    key_service: KeyService, mock_redis: AsyncMock, actor: CurrentUser
):
    """결함의 본체 — user override 가 있으면 팀 목록으로 넓어져선 안 된다."""
    await _issue(
        key_service,
        actor,
        user_id=uuid.uuid4(),
        team_id=TEAM_A,
        user_aliases=["claude-haiku"],
        team_aliases=["claude-haiku", "claude-opus", "gpt-5.6"],
        clients=[],
    )

    snap = _snapshot_written(mock_redis)
    assert snap is not None, "스냅샷이 심기지 않았다 — 아래 단정이 공허하다"
    assert snap["allowed_models"] == ["claude-haiku"], (
        f"user override 를 무시하고 팀 목록으로 넓어졌다: {snap['allowed_models']} — "
        f"gateway 는 이 스냅샷을 검증 없이 신뢰하므로 캐시 TTL 동안 제한이 풀린다"
    )


@pytest.mark.asyncio
async def test_team_whitelist_is_still_used_when_the_user_has_no_override(
    key_service: KeyService, mock_redis: AsyncMock, actor: CurrentUser
):
    """대조군 ① — 팀 폴백을 깨뜨리지 않았는지(gateway 우선순위 user > team > none)."""
    await _issue(
        key_service,
        actor,
        user_id=uuid.uuid4(),
        team_id=TEAM_A,
        user_aliases=[],
        team_aliases=["claude-haiku", "claude-opus"],
        clients=[],
    )

    snap = _snapshot_written(mock_redis)
    assert snap is not None
    assert snap["allowed_models"] == ["claude-haiku", "claude-opus"]


@pytest.mark.asyncio
async def test_no_policy_anywhere_means_unrestricted(
    key_service: KeyService, mock_redis: AsyncMock, actor: CurrentUser
):
    """대조군 ② — 정책이 없으면 None(전체 허용)이 **의도**다. 여기까지 좁히면 전원 차단된다."""
    await _issue(
        key_service,
        actor,
        user_id=uuid.uuid4(),
        team_id=TEAM_A,
        user_aliases=[],
        team_aliases=[],
        clients=[],
    )

    snap = _snapshot_written(mock_redis)
    assert snap is not None
    assert snap["allowed_models"] is None
    assert snap["allowed_clients"] is None


@pytest.mark.asyncio
async def test_allowed_clients_is_snapshotted(
    key_service: KeyService, mock_redis: AsyncMock, actor: CurrentUser
):
    """예전엔 키 자체가 없어 클라이언트 인가가 캐시 TTL 동안 통째로 꺼졌다."""
    await _issue(
        key_service,
        actor,
        user_id=uuid.uuid4(),
        team_id=TEAM_A,
        user_aliases=[],
        team_aliases=[],
        clients=["claude-code"],
    )

    snap = _snapshot_written(mock_redis)
    assert snap is not None
    assert snap["allowed_clients"] == ["claude-code"], (
        f"allowed_clients 가 실리지 않았다({snap.get('allowed_clients')!r}) — "
        f"None 은 '전 클라이언트 허용'이라 ClientAuthorizationMiddleware 가 통과시킨다"
    )


@pytest.mark.asyncio
async def test_a_user_with_no_team_still_gets_its_user_level_whitelist(
    key_service: KeyService, mock_redis: AsyncMock, actor: CurrentUser
):
    """팀 없는 사용자도 user override 는 적용돼야 한다(팀 조회 분기를 타지 않는다)."""
    await _issue(
        key_service,
        actor,
        user_id=uuid.uuid4(),
        team_id=None,
        user_aliases=["claude-haiku"],
        team_aliases=["should-not-be-read"],
        clients=[],
    )

    snap = _snapshot_written(mock_redis)
    assert snap is not None
    assert snap["allowed_models"] == ["claude-haiku"]
    assert snap["team_id"] == ""


@pytest.mark.parametrize(
    "failing",
    ["user_models", "clients"],
    ids=["user_allowed_models 조회 실패", "allowed_clients 조회 실패"],
)
@pytest.mark.asyncio
async def test_acl_lookup_failure_skips_the_snapshot_but_still_issues_the_key(
    key_service: KeyService, mock_redis: AsyncMock, actor: CurrentUser, failing: str
):
    """ACL 을 못 읽으면 **넓은 스냅샷을 심지 않는다**. 발급 자체는 성공한다.

    스냅샷은 콜드캐시 DB 왕복을 아끼는 최적화다. 못 읽었을 때 심어 버리면 gateway 가
    그 무제한 스냅샷을 신뢰해 버리므로, 최적화를 포기하는 쪽이 맞다 — gateway 는
    캐시 미스로 직접 조회하고 자기 fail-closed 규칙을 적용한다.
    """
    boom = RuntimeError("connection reset by peer")
    result = await _issue(
        key_service,
        actor,
        user_id=uuid.uuid4(),
        team_id=TEAM_A,
        user_aliases=boom if failing == "user_models" else [],
        team_aliases=["claude-opus"],
        clients=boom if failing == "clients" else [],
    )

    assert _snapshot_written(mock_redis) is None, (
        "ACL 조회가 실패했는데 스냅샷을 심었다 — gateway 가 무제한으로 되살린다"
    )
    assert _vk_lookup_written(mock_redis), (
        "key:vk: 매핑까지 빠졌다 — VK 가 발급됐는데 gateway 가 인증할 수 없다"
    )
    assert result.virtual_key.startswith("vk-")


@pytest.mark.asyncio
async def test_the_snapshot_is_written_on_the_happy_path(
    key_service: KeyService, mock_redis: AsyncMock, actor: CurrentUser
):
    """대조군 ③ — 위 '심지 않는다' 단정의 공허함 방지.

    스냅샷을 아예 안 심는 구현으로 망가져도 위 테스트는 통과한다. 정상 경로에서는
    반드시 심겨야 한다(안 심으면 콜드캐시 왕복이 매번 발생).
    """
    await _issue(
        key_service,
        actor,
        user_id=uuid.uuid4(),
        team_id=TEAM_A,
        user_aliases=[],
        team_aliases=["claude-opus"],
        clients=["claude-code"],
    )
    assert _snapshot_written(mock_redis) is not None
