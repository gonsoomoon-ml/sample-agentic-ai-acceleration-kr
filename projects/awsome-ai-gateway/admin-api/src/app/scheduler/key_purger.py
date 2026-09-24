# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""VK Purge Job — deletes old EXPIRED Virtual Keys from DB.

key_expirer 가 ACTIVE→EXPIRED 상태 전환만 하므로 EXPIRED 행은 무한 누적된다.
보관기간(기본 90일)이 지난 EXPIRED 행만 삭제해 최근 만료 키의 감사 추적은 유지한다.

REVOKED 키는 삭제하지 않는다 — 수동 폐기는 보안 사고 추적 가치가 있어 영구 보관.
Redis 는 발급 시 TTL 로 이미 정리되고, 다른 테이블의 FK 참조도 없어 행 삭제가 안전하다.
audit.audit_logs 는 resource_id 문자열 + changes 스냅샷이라 이력은 남는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import KeyStatus, VirtualKey

logger = structlog.get_logger()


async def purge_expired_keys(session: AsyncSession, retention_days: int) -> int:
    """보관기간이 지난 EXPIRED VK 행을 삭제한다. 삭제 건수 반환."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    stmt = (
        delete(VirtualKey)
        .where(VirtualKey.status == KeyStatus.EXPIRED)
        .where(VirtualKey.expires_at < cutoff)
    )
    result = await session.execute(stmt)
    count: int = result.rowcount  # type: ignore[assignment]
    if count > 0:
        logger.info("key_purger.purged", count=count, retention_days=retention_days)
    await session.commit()
    return count
