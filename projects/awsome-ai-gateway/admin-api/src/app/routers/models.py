# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, require_admin, require_admin_or_team_leader
from app.core.config import get_settings
from app.core.db import get_db_session
from app.schemas.models import (
    ModelCreateRequest,
    ModelListResponse,
    ModelResponse,
    ModelUpdateRequest,
    PriceSyncApplyRequest,
    PriceSyncApplyResponse,
    PriceSyncPreviewResponse,
    PricingRequest,
    StatusPatchRequest,
    WireNameListResponse,
)
from app.services.model_service import ModelService

router = APIRouter(prefix="/admin/models", tags=["Model Management"])


def _build_pricing_sync_service(*, source: str = "aws"):
    """단가 동기화 서비스 생성.

    source:
      - "aws": AWS Price List API(boto3 pricing client)
      - "litellm": LiteLLM Model Catalog API(httpx)
    """
    settings = get_settings()
    if source == "litellm":
        import httpx

        from app.services.pricing_sync_service import LiteLLMPricingSyncService

        svc = LiteLLMPricingSyncService(
            http_client=httpx.AsyncClient(timeout=30.0),
            base_url=settings.LITELLM_API_URL,
            provider_filter=settings.LITELLM_PROVIDER_FILTER,
        )
        svc.region = settings.LITELLM_API_URL  # preview 응답에 소스 표시용
        return svc

    import boto3

    from app.services.pricing_sync_service import PricingSyncService

    region = settings.PRICING_API_REGION
    client = boto3.client("pricing", region_name=region)
    svc = PricingSyncService(client)
    svc.region = region  # preview 응답에 표시
    return svc


@router.get("", response_model=ModelListResponse)
async def list_models(
    request: Request,
    _actor: CurrentUser = Depends(require_admin_or_team_leader),
    session: AsyncSession = Depends(get_db_session),
):
    # 읽기 전용 모델 카탈로그 — 조직 전체에 동일하므로 팀별 스코핑 불필요
    # (대시보드 '활성 모델 수' KPI 등 TEAM_LEADER 화면에서도 사용).
    svc: ModelService = request.app.state.model_service
    items = await svc.list_models(session)
    return ModelListResponse(items=items)


@router.get("/wire-names", response_model=WireNameListResponse)
async def list_wire_names(
    request: Request,
    days: int = 30,
    _actor: CurrentUser = Depends(require_admin_or_team_leader),
    session: AsyncSession = Depends(get_db_session),
):
    """최근 N일 usage_logs 에 관측된 모델 이름 — alias 생성 시 확인용."""
    svc: ModelService = request.app.state.model_service
    return await svc.list_wire_names(
        session, days=days, redis=getattr(request.app.state, "redis", None)
    )


@router.post("", response_model=ModelResponse, status_code=201)
async def create_model(
    request: Request,
    body: ModelCreateRequest,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    svc: ModelService = request.app.state.model_service
    return await svc.create_model(
        session,
        data=body,
        actor=admin,
        ip_address=request.client.host if request.client else "0.0.0.0",
        request_id=request.headers.get("x-request-id", ""),
    )


@router.delete("/{alias}", status_code=204)
async def delete_model(
    request: Request,
    alias: str,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """모델 alias 삭제 — 라우팅 설정행은 cascade, 사용 이력은 보존.

    앱의 default_model 로 참조 중이면 409 (그 앱 요청이 전부 깨지므로).
    """
    svc: ModelService = request.app.state.model_service
    await svc.delete_model(
        session,
        alias=alias,
        actor=admin,
        ip_address=request.client.host if request.client else "0.0.0.0",
        request_id=request.headers.get("x-request-id", ""),
    )


@router.put("/{alias}", response_model=ModelResponse)
async def update_model(
    request: Request,
    alias: str,
    body: ModelUpdateRequest,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    svc: ModelService = request.app.state.model_service
    return await svc.update_model(
        session,
        alias=alias,
        data=body,
        actor=admin,
        ip_address=request.client.host if request.client else "0.0.0.0",
        request_id=request.headers.get("x-request-id", ""),
    )


@router.put("/{alias}/pricing", response_model=ModelResponse)
async def set_pricing(
    request: Request,
    alias: str,
    body: PricingRequest,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    svc: ModelService = request.app.state.model_service
    return await svc.set_pricing(
        session,
        alias=alias,
        data=body,
        actor=admin,
        ip_address=request.client.host if request.client else "0.0.0.0",
        request_id=request.headers.get("x-request-id", ""),
    )


@router.get("/pricing/sync-preview", response_model=PriceSyncPreviewResponse)
async def price_sync_preview(
    request: Request,
    source: str = "aws",
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """외부 단가 소스(AWS Price List / LiteLLM Catalog) vs DB 현재가 diff 미리보기.

    source: "aws" | "litellm". 기본값 "aws".
    운영자가 이 diff 를 확인한 뒤 sync-apply 로 명시 적용. 자동 적용 없음.
    """
    svc: ModelService = request.app.state.model_service
    pricing_sync = _build_pricing_sync_service(source=source)
    return await svc.preview_price_sync(session, pricing_sync_service=pricing_sync)


@router.post("/pricing/sync-apply", response_model=PriceSyncApplyResponse)
async def price_sync_apply(
    request: Request,
    body: PriceSyncApplyRequest,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """승인된 alias 목록만 외부 단가 소스(AWS / LiteLLM)로 적용(기존 set_pricing 재사용)."""
    svc: ModelService = request.app.state.model_service
    pricing_sync = _build_pricing_sync_service(source=body.source)
    return await svc.apply_price_sync(
        session,
        pricing_sync_service=pricing_sync,
        aliases=body.aliases,
        actor=admin,
        ip_address=request.client.host if request.client else "0.0.0.0",
        request_id=request.headers.get("x-request-id", ""),
    )


@router.patch("/{alias}/status", response_model=ModelResponse)
async def patch_status(
    request: Request,
    alias: str,
    body: StatusPatchRequest,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    svc: ModelService = request.app.state.model_service
    return await svc.patch_status(
        session,
        alias=alias,
        data=body,
        actor=admin,
        ip_address=request.client.host if request.client else "0.0.0.0",
        request_id=request.headers.get("x-request-id", ""),
    )
