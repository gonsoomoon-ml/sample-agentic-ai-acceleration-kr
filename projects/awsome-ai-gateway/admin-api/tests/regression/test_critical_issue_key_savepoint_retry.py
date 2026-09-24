# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""``KeyService.issue_key`` 의 세이브포인트 재시도를 고정한다.

배경 — 마이그레이션 0034 는 ``idx_virtual_keys_user_active_unique``
(사용자당 ACTIVE VK 1개, 부분 유니크)를 넣는다. 발급 경로는 CTE 하나로
"기존 ACTIVE 만료 + 신규 삽입" 을 한다.

실 PG16 실측:

  * CTE + 인덱스 **공존은 정상** — 단일 요청은 ``expired=1 inserted=1`` 로 통과한다.
    (CTE 의 UPDATE 와 INSERT 가 같은 명령이라 인덱스는 명령 끝에서 판정된다.)
  * 그러나 **동시 발급 두 건은 둘 다 실패**한다. CTE 문장들은 한 스냅샷을 공유해서
    양쪽이 같은 기존 키를 만료 대상으로 보고, 양쪽이 새 키를 넣으려 하며,
    나중 커밋이 유니크 위반으로 죽는다. 인덱스가 없을 때는 ACTIVE 가 **2개**로
    남았다(조용한 오염).

즉 인덱스만 넣으면 조용한 오염이 **CLI 로그인 500** 으로 바뀐다. 재시도까지
넣어야 개선이다: 첫 시도가 IntegrityError 면 세이브포인트를 되감고 새 스냅샷으로
한 번 더 시도한다(그때는 상대의 키가 보이므로 정상적으로 만료시킨다).

이 파일이 고정하는 것:
  1. 정상 경로에서 세이브포인트가 **한 번만** 열린다(재시도 오버헤드 없음).
  2. 첫 시도 IntegrityError → 두 번째 시도로 성공하고, 호출자는 예외를 못 본다.
  3. 두 번 연속 실패 → **삼키지 않고** 다시 던진다(무한 재시도·조용한 실패 금지).
  4. IntegrityError 가 아닌 예외는 재시도하지 않는다(1회만 시도).
"""

from __future__ import annotations

import ast
import contextlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.encryption import AESEncryptionService
from app.models.auth import User, UserRole
from app.services.key_service import KeyService
from tests.session_double import wire_savepoint

SERVICE_PY = Path(__file__).resolve().parents[2] / "src" / "app" / "services" / "key_service.py"


def _integrity() -> IntegrityError:
    orig = Exception(
        'duplicate key value violates unique constraint "idx_virtual_keys_user_active_unique"'
    )
    orig.constraint_name = "idx_virtual_keys_user_active_unique"  # type: ignore[attr-defined]
    return IntegrityError("INSERT ...", {}, orig)


@pytest.fixture
def session():
    s = AsyncMock()
    wire_savepoint(s)
    return s


@pytest.fixture
def key_service() -> KeyService:
    """tests/unit/conftest.py 의 fixture 를 빌리지 않고 직접 만든다.

    regression/ 은 별도 conftest 를 쓰므로 unit 의 encryption/cache_mgr fixture 가
    보이지 않는다. 여기서 자급하면 unit conftest 변경에 끌려다니지 않는다.
    """
    redis = AsyncMock()
    # redis-py 의 파이프라인 명령(setex/delete/…)은 **동기 호출**이다 — 버퍼에 쌓고
    # `execute()` 만 await 한다. AsyncMock 으로 두면 명령마다 대기 없는 코루틴이 생겨
    # RuntimeWarning 이 쌓이고, 무엇보다 double 이 실물과 다른 계약을 흉내낸다.
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    redis.pipeline = MagicMock(return_value=pipe)
    return KeyService(
        encryption=AESEncryptionService("0" * 64),
        cache_mgr=CacheInvalidationManager(redis),
    )


@pytest.fixture
def actor() -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role=UserRole.ADMIN,
        team_id=None,
    )


def _stub_user(user_id: uuid.UUID) -> MagicMock:
    u = MagicMock(spec=User)
    u.id = user_id
    u.team_id = None
    u.sso_subject = None  # JSON 스냅샷에 들어가므로 직렬화 가능한 값이어야 한다
    return u


class _CountingRepo:
    """``expire_and_create`` 의 **시도별** 결과를 스크립트로 정한다.

    AsyncMock(side_effect=[...]) 로도 되지만, 시도 횟수가 이 테스트의 본질이라
    카운터를 눈에 보이게 둔다.
    """

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def expire_and_create(self, user_id, vk):
        self.calls += 1
        assert self._outcomes, f"예상보다 많이 호출됐다({self.calls}회)"
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def list_active_for_user(self, user_id):
        # VK dedup(VK_DEDUP_SECONDS)이 발급 전에 최근 ACTIVE 키를 조회한다.
        # 빈 리스트 = 최근 키 없음 → 테스트 대상인 expire_and_create 경로로 진행.
        return []


@contextlib.contextmanager
def _isolated(repo, user_id):
    """issue_key 가 건드리는 외부 협력자 전부를 대체한다.

    ⚠️ 하나라도 빠지면 AsyncMock 세션 위에서 진짜 repo 가 돌아 MagicMock 이 흘러들고,
       json.dumps 가 터져 **재시도와 무관한 이유로** 실패한다(원인 오진의 원천).
    """
    with patch("app.services.key_service.KeyRepository", return_value=repo), patch(
        "app.services.key_service.UserRepository"
    ) as MockUser, patch("app.services.key_service.TeamAllowedModelRepository") as MockTam, patch(
        "app.services.key_service.UserAllowedModelRepository"
    ) as MockUam, patch(
        "app.services.key_service.UserAllowedClientRepository"
    ) as MockUac, patch("app.services.key_service.audit_logger") as mock_audit:
        MockUser.return_value.get_user = AsyncMock(return_value=_stub_user(user_id))
        MockTam.return_value.list_by_team = AsyncMock(return_value=[])
        MockUam.return_value.list_by_user = AsyncMock(return_value=[])
        MockUac.return_value.list_by_user = AsyncMock(return_value=[])
        mock_audit.log = AsyncMock()
        yield


# ─────────────────────────────────────────────────────────────────────────────
# 1. 소스 수준 계약 — 재시도가 실제로 코드에 있는가(AST)
# ─────────────────────────────────────────────────────────────────────────────


def _issue_key_source() -> ast.AST:
    tree = ast.parse(SERVICE_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "issue_key":
            return node
    raise AssertionError("key_service.py 에서 issue_key 를 찾지 못했다")


def test_issue_key_wraps_the_cte_in_a_savepoint():
    """``begin_nested`` 없이 재시도하면 세션이 오염된 채 남아 이후 쿼리가 전부 죽는다."""
    src = ast.dump(_issue_key_source())
    assert "begin_nested" in src, (
        "issue_key 가 세이브포인트를 쓰지 않는다 — IntegrityError 후 세션이 "
        "aborted 상태로 남아 재시도 자체가 불가능하다"
    )


def _savepoint_try() -> ast.Try:
    """``begin_nested`` 를 감싸는 **그** try 를 찾는다.

    ⚠️ issue_key 안의 모든 handler 를 보면 안 된다 — 이 함수엔 AuthContext ACL
       스냅샷 실패를 fail-open 하는 별개의 ``except Exception`` 이 있고(의도된 설계),
       그걸 같이 세면 이 가드는 영원히 실패한다. 재시도 경로만 판정한다.
    """
    node = _issue_key_source()
    for n in ast.walk(node):
        if not isinstance(n, ast.Try):
            continue
        if "begin_nested" in ast.dump(ast.Module(body=n.body, type_ignores=[])):
            return n
    raise AssertionError("begin_nested 를 감싸는 try 를 찾지 못했다 — 재시도 구조가 없다")


def test_issue_key_handles_integrity_error_specifically():
    """넓은 ``except Exception`` 으로 잡으면 무관한 결함까지 재시도한다."""
    try_node = _savepoint_try()
    caught = set()
    for h in try_node.handlers:
        for name in ast.walk(h.type) if h.type is not None else []:
            if isinstance(name, ast.Name):
                caught.add(name.id)
    assert "IntegrityError" in caught, f"IntegrityError 를 잡지 않는다(잡는 것: {caught})"
    assert "Exception" not in caught, (
        "재시도 try 가 except Exception 으로 잡고 있다 — 유니크 경합이 아닌 결함까지 "
        "재시도해 원인을 감춘다"
    )
    assert any(h.type is None for h in try_node.handlers) is False, (
        "bare except 가 있다 — KeyboardInterrupt/CancelledError 까지 재시도한다"
    )


def test_second_failure_reraises_in_source():
    """재시도 소진 시 ``raise`` 가 실제로 있는지 — 없으면 조용히 키 없이 진행한다."""
    try_node = _savepoint_try()
    has_reraise = any(
        isinstance(n, ast.Raise) for h in try_node.handlers for n in ast.walk(h)
    )
    assert has_reraise, (
        "예외 처리부에 raise 가 없다 — 두 번째 실패를 삼키면 VK 없이 200 이 나간다"
    )


def test_retry_is_bounded():
    """무한 재시도 금지 — 상한이 리터럴로 보여야 한다."""
    node = _issue_key_source()
    has_bounded_loop = False
    for n in ast.walk(node):
        if isinstance(n, ast.For) and isinstance(n.iter, (ast.Tuple, ast.List)):
            has_bounded_loop = True
        if isinstance(n, ast.While):
            # while True 는 상한이 코드에 안 보인다.
            if isinstance(n.test, ast.Constant) and n.test.value is True:
                raise AssertionError("while True 재시도 — 상한이 코드에 드러나지 않는다")
    assert has_bounded_loop, "재시도 상한이 유한 이터러블로 표현되지 않았다"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 동작 — 시도 횟수와 예외 전파
# ─────────────────────────────────────────────────────────────────────────────


async def test_happy_path_opens_savepoint_once(session, key_service, actor):
    user_id = uuid.uuid4()
    repo = _CountingRepo([(1, uuid.uuid4())])
    with _isolated(repo, user_id):
        await key_service.issue_key(session, user_id=user_id, actor=actor)
    assert repo.calls == 1, f"정상 경로에서 {repo.calls} 번 시도했다 — 1 이어야 한다"
    assert session.begin_nested.call_count == 1, (
        f"세이브포인트가 {session.begin_nested.call_count} 번 열렸다 — 정상 경로는 1"
    )


async def test_first_attempt_conflict_succeeds_on_retry(session, key_service, actor):
    """동시 발급 경합 — 호출자는 예외를 보지 않아야 한다(CLI 로그인이 죽으면 안 된다)."""
    user_id = uuid.uuid4()
    repo = _CountingRepo([_integrity(), (1, uuid.uuid4())])
    with _isolated(repo, user_id):
        result = await key_service.issue_key(session, user_id=user_id, actor=actor)
    assert result.virtual_key.startswith("vk-"), "재시도 후에도 정상 VK 를 돌려줘야 한다"
    assert repo.calls == 2, f"재시도가 일어나지 않았다(시도 {repo.calls}회)"
    assert session.begin_nested.call_count == 2, (
        "두 번째 시도가 새 세이브포인트에서 돌지 않았다 — 첫 시도의 오염이 남는다"
    )


async def test_second_conflict_is_reraised_not_swallowed(session, key_service, actor):
    """두 번 연속 실패는 조용히 넘기면 안 된다 — 키 없이 200 을 주는 게 최악이다."""
    user_id = uuid.uuid4()
    repo = _CountingRepo([_integrity(), _integrity()])
    with _isolated(repo, user_id):
        with pytest.raises(IntegrityError):
            await key_service.issue_key(session, user_id=user_id, actor=actor)
    assert repo.calls == 2, f"상한을 넘겨 {repo.calls} 번 시도했다"


async def test_non_integrity_error_is_not_retried(session, key_service, actor):
    """DB 연결 끊김 등은 재시도해도 의미가 없고, 재시도하면 원인이 흐려진다."""

    class _Boom(RuntimeError):
        pass

    user_id = uuid.uuid4()
    repo = _CountingRepo([_Boom("connection reset")])
    with _isolated(repo, user_id):
        with pytest.raises(_Boom):
            await key_service.issue_key(session, user_id=user_id, actor=actor)
    assert repo.calls == 1, f"IntegrityError 가 아닌데 {repo.calls} 번 시도했다"
