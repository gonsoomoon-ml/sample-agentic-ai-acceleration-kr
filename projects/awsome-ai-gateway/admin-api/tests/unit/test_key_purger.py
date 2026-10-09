# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""key_purger 단위 테스트 — EXPIRED 만 삭제, REVOKED 보존, cutoff/rowcount/commit 검증."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.scheduler.key_purger import purge_expired_keys


def _session_double(rowcount: int = 0):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=rowcount))
    return session


@pytest.mark.asyncio
async def test_purge_returns_rowcount_and_commits():
    session = _session_double(rowcount=7)
    count = await purge_expired_keys(session, 90)
    assert count == 7
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_stmt_targets_expired_only_with_cutoff():
    session = _session_double()
    await purge_expired_keys(session, 90)
    stmt = session.execute.await_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "EXPIRED" in sql
    assert "REVOKED" not in sql  # 수동 폐기 키는 보안 추적용으로 보존
    assert "expires_at" in sql
