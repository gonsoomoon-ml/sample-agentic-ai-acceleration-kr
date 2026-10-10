# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
"""커밋이 실패했는데 클라이언트가 200 + 성공 본문을 받는 조용한 데이터 손실.

배경:
  get_db_session 은 `yield session; await session.commit()` 형태였다. FastAPI 는 yield
  의존성의 종료 블록을 **응답을 내보낸 뒤에** 실행하므로, 그 커밋이 실패하면
    * 트랜잭션은 롤백되고(쓰기가 사라진다),
    * 클라이언트는 이미 `200 {"success": true, ...}` 를 받았고,
    * 등록된 예외 핸들러는 호출조차 되지 않아 에러 봉투에도 안 남는다.
  즉 "저장됐습니다" 를 보고 사용자가 떠나는데 DB 에는 아무것도 없다.

  실측(fastapi 0.136.0)으로 재현했고, 수정은 커밋 지점을 응답 **전송 전**으로 옮기는
  CommittingRoute + install_commit_before_response 다.

이 파일은 프레임워크 동작에 의존하는 수정이므로 FastAPI 업그레이드가 전제를 깨면
바로 실패해야 한다 — 그래서 상류 동작(종료 블록은 늦다)까지 함께 못 박는다.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, AsyncIterator

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.db import (
    SESSION_STATE_ATTR,
    CommittingRoute,
    install_commit_before_response,
)
from app.main import create_app, register_exception_handlers


class FakeSession:
    """AsyncSession 중 CommittingRoute 가 실제로 쓰는 표면만 흉내낸다."""

    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.commits = 0
        self.rollbacks = 0
        self._in_transaction = True
        # 실물 AsyncSession.info — 커밋 후 Redis 발행 대기열이 쌓이는 평범한 dict.
        self.info: dict[str, list] = {}

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def commit(self) -> None:
        self.commits += 1
        if self.fail:
            raise RuntimeError("could not serialize access / deferred constraint")
        self._in_transaction = False

    async def rollback(self) -> None:
        self.rollbacks += 1
        self._in_transaction = False


def build_app(*, fail: bool, streaming: bool = False) -> tuple[FastAPI, dict[str, Any]]:
    """실제 앱과 같은 배선(에러 봉투 + 커밋 승격)으로 최소 앱을 만든다."""
    captured: dict[str, Any] = {}

    async def dep(request: Request) -> AsyncGenerator[FakeSession, None]:
        session = FakeSession(fail=fail)
        captured["session"] = session
        setattr(request.state, SESSION_STATE_ATTR, session)
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            # 실제 get_db_session 과 같은 안전망.
            if session.in_transaction():
                await session.commit()

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/write")
    async def write(session: FakeSession = Depends(dep)) -> dict[str, Any]:
        return {"success": True, "id": "created-123"}

    @app.get("/stream")
    async def stream(session: FakeSession = Depends(dep)) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b"data: hello\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    install_commit_before_response(app)
    return app, captured


async def _client(app: FastAPI) -> AsyncClient:
    """실제 HTTP 클라이언트가 보는 것을 본다.

    raise_app_eexceptions 를 기본값(True)으로 두면 안 된다: Starlette 의
    ServerErrorMiddleware 는 500 응답을 **보낸 뒤** 예외를 다시 올린다(서버 로그용).
    기본값이면 httpx 가 그 예외를 그대로 던져서, 정작 클라이언트가 받는 500 봉투를
    검사할 수 없다. uvicorn 뒤의 실제 클라이언트는 500 JSON 을 받는다.
    """
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_failed_commit_is_reported_as_an_error_not_success():
    """커밋 실패는 500 + 에러 봉투여야 한다 — 절대 200 성공 본문이 아니다."""
    app, captured = build_app(fail=True)
    async with await _client(app) as ac:
        res = await ac.post("/write")

    assert res.status_code == 500, (
        f"커밋이 실패했는데 {res.status_code} 를 반환했다 — 사용자는 저장됐다고 믿는다: {res.text}"
    )
    # 에러 봉투 형식까지 확인한다 — text/plain 으로 새면 클라이언트가 파싱에 실패한다.
    body = res.json()
    assert body["error"]["code"] == "INTERNAL_ERROR", body
    assert body["error"]["type"] == "internal_error", body
    assert "success" not in body
    # 커밋 실패 후 열린 트랜잭션을 커넥션 풀에 돌려보내면 안 된다.
    #
    # 횟수가 아니라 **상태**를 단정한다. 롤백은 두 곳에서 일어난다: CommittingRoute 가
    # 커밋 실패 직후 한 번(실패를 아는 코드가 정리한다), 그리고 그 예외가 의존성 제너레이터로
    # 다시 던져지면서 get_db_session 의 `except` 분기가 한 번 더. AsyncSession 의 두 번째
    # rollback 은 no-op 이고, 어느 한쪽이 사라져도 정리가 보장되는 편이 안전하다.
    # 그래서 "몇 번 불렸나" 대신 "끝에 트랜잭션이 닫혀 있나" 를 본다.
    session = captured["session"]
    assert session.rollbacks >= 1, "커밋 실패 후 롤백이 전혀 없었다"
    assert session.in_transaction() is False, "열린 트랜잭션이 풀로 반환된다"


@pytest.mark.asyncio
async def test_successful_path_still_commits_once_and_returns_200():
    """대조군 — 정상 경로가 망가지지 않았는지. 커밋은 정확히 한 번."""
    app, captured = build_app(fail=False)
    async with await _client(app) as ac:
        res = await ac.post("/write")

    assert res.status_code == 200
    assert res.json() == {"success": True, "id": "created-123"}
    session = captured["session"]
    assert session.commits == 1, f"커밋이 {session.commits}회 — 중복 커밋/미커밋"
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_streaming_response_is_left_to_the_exit_path():
    """스트리밍 응답은 라우트에서 커밋하지 않는다(본문 생성 전이라 세션을 계속 쓸 수 있다).

    상태줄이 이미 확정된 뒤라 어차피 실패를 응답에 반영할 수 없다 — 여기서는 커밋을
    종료 블록의 안전망에 맡기고, 그 커밋이 정확히 한 번 일어나는지만 확인한다.
    """
    app, captured = build_app(fail=False, streaming=True)
    async with await _client(app) as ac:
        res = await ac.get("/stream")

    assert res.status_code == 200
    assert res.text == "data: hello\n\n"
    assert captured["session"].commits == 1


@pytest.mark.asyncio
async def test_endpoints_without_a_session_are_untouched():
    """DB 를 안 쓰는 엔드포인트는 그대로 통과해야 한다(request.state 에 세션이 없다)."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    install_commit_before_response(app)
    async with await _client(app) as ac:
        res = await ac.get("/ping")
    assert res.status_code == 200
    assert res.json() == {"pong": "ok"}


def test_every_route_in_the_real_app_commits_before_responding():
    """실제 앱의 모든 APIRoute 가 승격돼 있어야 한다.

    이 검사가 이 수정의 핵심 가드다. install_commit_before_response 호출이 지워지거나
    새 라우터가 include 된 뒤에 호출되도록 순서가 바뀌면(=승격 누락) 그 엔드포인트만
    조용히 예전 동작(커밋 실패 → 200)으로 돌아간다.
    """
    app = create_app()
    stale = [
        f"{sorted(r.methods)} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute) and not isinstance(r, CommittingRoute)
    ]
    # 실패 시 96개를 전부 쏟아내면 읽히지 않는다 — 개수 + 앞 5개만 보여 준다.
    assert not stale, (
        f"CommittingRoute 로 승격되지 않은 라우트 {len(stale)}개 "
        f"(install_commit_before_response 호출이 빠졌거나 include_router 보다 앞에 있다). "
        f"예: {stale[:5]}"
    )

    # 대조군: 라우트를 하나도 못 찾았다면 이 단정은 공허하다.
    total = sum(1 for r in app.routes if isinstance(r, APIRoute))
    assert total > 50, f"APIRoute 를 {total}개만 찾았다 — 검사 범위가 잘못됐다"


def test_upgrade_is_idempotent():
    """두 번 호출해도 두 번째는 승격할 것이 없다(중복 래핑 방지)."""
    app, _ = build_app(fail=False)
    assert install_commit_before_response(app) == 0
