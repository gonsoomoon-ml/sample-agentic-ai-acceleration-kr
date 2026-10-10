# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.routing import APIRoute, request_response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

logger = structlog.get_logger()

# 요청 단위 세션을 매달아 두는 request.state 속성명. get_db_session 이 넣고
# CommittingRoute 가 읽는다 — 커밋 시점을 응답 생성 **전**으로 옮기기 위한 유일한 연결점.
SESSION_STATE_ATTR = "db_session"

# session.info 키 — 커밋 성공 후에야 Redis 로 나가야 하는 이벤트 대기열.
# 서비스가 트랜잭션 안에서 publish 하면 커밋 실패(롤백) 시 "취소된 쓰기의
# 알림"이 나간다(key_revoked 메일이 대표적). (channel, payload_json) 튜플 목록.
PENDING_REDIS_KEY = "pending_redis_publishes"


def enqueue_redis_publish(session: AsyncSession, channel: str, payload: str) -> None:
    """커밋 성공 시 발행할 이벤트를 세션 대기열에 넣는다.

    ⚠️ drain 은 요청 스코프(CommittingRoute · get_db_session)에만 있다 —
       요청 밖의 세션(스케줄러·직접 ``AsyncSessionLocal()``)이 커밋하면 대기열은
       아무 데도 가지 않고 세션과 함께 조용히 버려진다. 그런 호출자는 커밋 후
       ``drain_pending_redis(session, redis=...)`` 를 직접 불러야 한다.
    """
    session.info.setdefault(PENDING_REDIS_KEY, []).append((channel, payload))


async def drain_pending_redis(
    session: AsyncSession, request: Request | None = None, *, redis=None
) -> None:
    """커밋 **성공 직후** 대기열의 Redis publish 를 실행한다. 발송은 최선노력.

    ``redis`` 를 넘기면 request 없이도 쓸 수 있다 — 스케줄러 등 요청 밖 호출자용.
    """
    # 실물 AsyncSession.info 는 항상 평범한 dict 다. 테스트 double(AsyncMock·
    # FakeSession)은 dict 가 아니거나 attr 자체가 없을 수 있으니 방어한다 —
    # 이 경우 대기열에 들어갔을 이벤트도 없으니 무시해도 안전하다.
    info = getattr(session, "info", None)
    pending = info.pop(PENDING_REDIS_KEY, None) if isinstance(info, dict) else None
    if not pending:
        return
    if redis is None and request is not None:
        redis = getattr(request.app.state, "redis", None)
    if redis is None:
        logger.warning("pending_redis_publish_skipped", count=len(pending), reason="no redis")
        return
    for channel, payload in pending:
        try:
            await redis.publish(channel, payload)
        except Exception:
            logger.warning("pending_redis_publish_failed", channel=channel, exc_info=True)


def create_engine():
    settings = get_settings()
    return create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
        echo=settings.DB_ECHO,
        connect_args={"statement_cache_size": settings.DB_STATEMENT_CACHE_SIZE},
    )


engine = create_engine()
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """요청 단위 AsyncSession.

    ⚠️ 정상 경로의 커밋은 **여기서 하지 않는다**. yield 이후 블록은 FastAPI 가 응답을
       클라이언트로 내보낸 **뒤에** 실행하므로, 여기서 commit() 이 실패하면 그 예외는
       응답을 바꿀 수 없다. 실측(fastapi 0.136.0): 커밋이 RuntimeError 로 실패해도
       클라이언트는 `200 {"success": true, ...}` 를 받았고 등록된 예외 핸들러는 호출조차
       되지 않았다 — 즉 롤백된 쓰기를 성공으로 보고하는 조용한 데이터 손실이다.

       그래서 커밋은 CommittingRoute 가 **응답을 만든 직후, 내보내기 전에** 수행한다.
       아래 남은 커밋은 CommittingRoute 가 커밋할 수 없는 경우(스트리밍 응답)의
       마지막 안전망이다. 이미 커밋됐으면 in_transaction() 이 False 라 중복되지 않는다.
    """
    async with AsyncSessionLocal() as session:
        setattr(request.state, SESSION_STATE_ATTR, session)
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            if session.in_transaction():
                try:
                    await session.commit()
                except Exception:
                    # 여기까지 온 커밋 실패는 응답에 반영할 길이 없다(이미 전송됨).
                    # 최소한 조용히 사라지지 않게 크게 남긴다.
                    await session.rollback()
                    logger.error(
                        "db.commit_failed_after_response",
                        path=request.url.path,
                        method=request.method,
                        hint="응답은 이미 전송되어 상태코드를 바꿀 수 없다. 스트리밍 응답 경로.",
                        exc_info=True,
                    )
                    raise
                await drain_pending_redis(session, request)


class CommittingRoute(APIRoute):
    """응답을 내보내기 **전에** 커밋해서, 커밋 실패가 정상 에러 봉투(500)로 나가게 한다.

    엔드포인트 함수가 반환한 뒤 · 응답이 전송되기 전 지점이 커밋의 유일하게 올바른 위치다.
    의존성의 종료 블록(get_db_session 의 yield 이후)은 이미 응답이 나간 뒤라 늦다.

    직렬화 순서: original(request) 가 이미 response_model 직렬화를 마친 뒤에 커밋한다.
    세션이 expire_on_commit=False 라 커밋 후 객체 접근도 안전하고, 직렬화 중 발생하는
    오류와 커밋 오류가 섞이지 않는다.
    """

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def commit_before_response(request: Request) -> Response:
            response = await original(request)

            session: AsyncSession | None = getattr(request.state, SESSION_STATE_ATTR, None)
            if session is None or not session.in_transaction():
                return response  # DB 를 안 쓰는 엔드포인트이거나 이미 커밋됨

            if isinstance(response, (StreamingResponse, FileResponse)):
                # 본문이 아직 생성되지 않았다(SSE·파일 스트림). 상태줄은 이미 확정이라
                # 여기서 커밋해 실패해도 응답에 반영할 수 없고, 생성기가 세션을 계속
                # 쓸 수도 있다. → get_db_session 의 안전망에 맡긴다.
                return response

            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise  # 아직 응답 전이므로 예외 핸들러가 정상적으로 500 봉투를 만든다
            await drain_pending_redis(session, request)

            return response

        return commit_before_response


def install_commit_before_response(app: FastAPI) -> int:
    """등록된 모든 APIRoute 를 CommittingRoute 로 승격한다. 승격 개수를 돌려준다.

    왜 라우터마다 `route_class=` 를 주지 않는가: include_router 는 **원본 라우터의**
    route_class 를 보존한다(실측 — app.router.route_class 를 바꿔도 include 된 라우터는
    APIRoute 로 남았다). 라우터가 17개라 개별 지정은 새 라우터가 조용히 빠뜨릴 수 있다.
    앱 조립이 끝난 뒤 한 곳에서 일괄 승격하면 이후 추가되는 라우터도 자동으로 덮인다.

    __class__ 교체 후 route.app 을 다시 만들어야 한다 — APIRoute.__init__ 이
    get_route_handler() 결과를 route.app 에 캐시해 두기 때문이다.
    """
    upgraded = 0
    for route in app.routes:
        if isinstance(route, APIRoute) and not isinstance(route, CommittingRoute):
            route.__class__ = CommittingRoute
            route.app = request_response(route.get_route_handler())
            upgraded += 1
    return upgraded
