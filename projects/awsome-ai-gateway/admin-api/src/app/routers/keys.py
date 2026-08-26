# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_admin, require_admin_or_team_leader
from app.core.db import get_db_session
from app.models.auth import KeyStatus, UserRole
from app.schemas.common import PaginationMeta
from app.schemas.keys import KeyCountResponse, KeyListResponse

router = APIRouter(prefix="/admin/keys", tags=["Key Management"])


@router.get("", response_model=KeyListResponse)
async def list_keys(
    request: Request,
    user_id: str | None = None,
    team_id: str | None = None,
    status: KeyStatus | None = None,
    email: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    from app.services.key_service import KeyService

    svc: KeyService = request.app.state.key_service
    email_q = email.strip() if email else None
    items, has_more = await svc.list_keys(
        session,
        user_id=uuid.UUID(user_id) if user_id else None,
        team_id=uuid.UUID(team_id) if team_id else None,
        status=status,
        email=email_q or None,
        cursor=cursor,
        limit=limit,
    )
    last_id = str(items[-1].key_id) if items else None
    return KeyListResponse(
        items=items,
        pagination=PaginationMeta(cursor=last_id if has_more else None, limit=limit, has_more=has_more),
    )


@router.get("/count", response_model=KeyCountResponse)
async def count_keys(
    request: Request,
    user_id: str | None = None,
    team_id: str | None = None,
    status: KeyStatus | None = None,
    email: str | None = None,
    actor: CurrentUser = Depends(require_admin_or_team_leader),
    session: AsyncSession = Depends(get_db_session),
):
    """대시보드 '활성 API Key' KPI 등이 사용. TEAM_LEADER 는 team_id 쿼리 파라미터를
    무시하고 본인 팀으로 고정(다른 팀 키 개수를 조회하는 것을 막는다)."""
    from app.services.key_service import KeyService

    svc: KeyService = request.app.state.key_service
    email_q = email.strip() if email else None

    scoped_team_id: uuid.UUID | None
    if actor.role == UserRole.TEAM_LEADER:
        if actor.team_id is None:
            return KeyCountResponse(count=0)
        scoped_team_id = actor.team_id
    else:
        scoped_team_id = uuid.UUID(team_id) if team_id else None

    count = await svc.count_keys(
        session,
        user_id=uuid.UUID(user_id) if user_id else None,
        team_id=scoped_team_id,
        status=status,
        email=email_q or None,
    )
    return KeyCountResponse(count=count)


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    request: Request,
    key_id: str,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    from app.services.key_service import KeyService

    svc: KeyService = request.app.state.key_service
    await svc.revoke_key(
        session,
        key_id=uuid.UUID(key_id),
        actor=admin,
        ip_address=request.client.host if request.client else "0.0.0.0",
        request_id=request.headers.get("x-request-id", ""),
    )
