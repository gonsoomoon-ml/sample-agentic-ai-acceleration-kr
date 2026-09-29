# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_logger
from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.exceptions import ConflictError, NotFoundError
from app.models.model import ApiFormat, ModelAlias, ModelPricing, ModelStatus, Provider
from app.repositories.model_repository import ModelRepository
from app.schemas.models import (
    ModelCreateRequest,
    ModelPricingResponse,
    ModelResponse,
    ModelUpdateRequest,
    PricingRequest,
    StatusPatchRequest,
)

logger = structlog.get_logger()


class ModelService:
    def __init__(self, cache_mgr: CacheInvalidationManager) -> None:
        self._cache_mgr = cache_mgr

    async def list_models(self, session: AsyncSession) -> list[ModelResponse]:
        repo = ModelRepository(session)
        models = await repo.list_all()
        result: list[ModelResponse] = []
        for m in models:
            pricing = await repo.get_current_pricing(m.alias)
            result.append(self._to_response(m, pricing))
        return result

    async def create_model(
        self,
        session: AsyncSession,
        *,
        data: ModelCreateRequest,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ModelResponse:
        repo = ModelRepository(session)

        # BR-MOD-01: Case-insensitive alias uniqueness
        if await repo.alias_exists_ci(data.alias):
            raise ConflictError(f"Model alias already exists: {data.alias}")

        model = ModelAlias(
            alias=data.alias,
            provider=Provider(data.provider.value),
            provider_model_id=data.provider_model_id,
            endpoint_url=data.endpoint_url,
            api_format=ApiFormat(data.api_format.value),
            status=ModelStatus.ACTIVE,
            description=data.description,
            display_name=data.display_name,
            # None = 제한 없음(하위호환 기본값), [] = 허용 앱 없음, 목록 = 그 앱만.
            allowed_clients=data.allowed_clients,
            created_by=actor.user_id,
        )
        await repo.create_model(model)

        # Initial pricing
        pricing = ModelPricing(
            id=uuid.uuid4(),
            model_alias=data.alias,
            input_price_per_1k_tokens=data.input_price_per_1k_tokens,
            output_price_per_1k_tokens=data.output_price_per_1k_tokens,
            cache_creation_5m_price_per_1k_tokens=data.cache_creation_5m_price_per_1k_tokens,
            cache_creation_1h_price_per_1k_tokens=data.cache_creation_1h_price_per_1k_tokens,
            cache_read_price_per_1k_tokens=data.cache_read_price_per_1k_tokens,
            effective_from=datetime.now(timezone.utc),
            created_by=actor.user_id,
        )
        await repo.create_pricing(pricing)

        # BR-MOD-04 / P0-④: invalidate-only (do NOT pre-seed model:{alias}).
        # Pre-seeding a flat, TTL-less cache entry here was the cache-poison
        # pattern: DEL both keys and let the gateway populate model:{alias} with
        # the correct nested shape + TTL on first cache-miss (router_service
        # self-heal). Keeps admin writes and gateway reads on one cache contract.
        await self._cache_mgr.invalidate(
            [f"model:{model.alias}", "model:list"], session=session
        )

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="CREATE_MODEL",
            resource_type="ModelAlias",
            resource_id=model.alias,
            changes={"after": {"alias": model.alias, "provider": model.provider.value}},
            ip_address=ip_address,
            request_id=request_id,
        )

        return self._to_response(model, pricing)

    async def update_model(
        self,
        session: AsyncSession,
        *,
        alias: str,
        data: ModelUpdateRequest,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ModelResponse:
        repo = ModelRepository(session)
        update_kwargs = {k: v for k, v in data.model_dump().items() if v is not None}
        model = await repo.update_model(alias, **update_kwargs)
        if model is None:
            raise NotFoundError("ModelAlias", alias)

        # ── allowed_clients 의 "명시적 null = 제한 해제" ──
        #
        # 위 필터(`if v is not None`)는 이 저장소의 관례다: null 은 "생략" 과 같이
        # 취급해 값을 유지한다. 그런데 allowed_clients 는 그 관례에서 **한 칸 더**
        # 필요하다. canonical 의미가 `None`=제한 없음 / `[]`=허용 앱 없음 이므로,
        # null 을 필터로 버리면 **한 번 목록이 박힌 모델을 "제한 없음" 으로 되돌릴
        # API 가 사라진다.** 그러면 콘솔은 그 목적으로 `[]` 를 보낼 수밖에 없고,
        # `[]` 는 전면 거부이므로 "제한 해제" 버튼이 그 모델을 통째로 막는다.
        #
        # pydantic 의 `model_fields_set` 이 "키를 안 보냄" 과 "null 을 보냄" 을
        # 구별해 주므로, **명시적 null 만** 해제로 취급한다.
        #
        # ⚠️ 다른 nullable 필드(description / display_name / endpoint_url)는 오늘의
        #    "null = 무시" 동작을 그대로 둔다. 그 셋까지 바꾸면 null 을 "변경 안 함"
        #    으로 보내던 기존 호출자의 동작이 조용히 바뀐다 — 별건으로 다뤄야 한다.
        if "allowed_clients" in data.model_fields_set and data.allowed_clients is None:
            model.allowed_clients = None
            await session.flush()
            update_kwargs["allowed_clients"] = None

        pricing = await repo.get_current_pricing(alias)

        # BR-MOD-04: Cache invalidation
        await self._cache_mgr.invalidate([f"model:{alias}", "model:list"], session=session)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="UPDATE_MODEL",
            resource_type="ModelAlias",
            resource_id=alias,
            changes={"after": update_kwargs},
            ip_address=ip_address,
            request_id=request_id,
        )

        return self._to_response(model, pricing)

    async def set_pricing(
        self,
        session: AsyncSession,
        *,
        alias: str,
        data: PricingRequest,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ModelResponse:
        repo = ModelRepository(session)
        model = await repo.get_by_alias(alias)
        if model is None:
            raise NotFoundError("ModelAlias", alias)

        # BR-MOD-02: Close current pricing, preserve history
        await repo.close_current_pricing(alias, data.effective_from)

        pricing = ModelPricing(
            id=uuid.uuid4(),
            model_alias=alias,
            input_price_per_1k_tokens=data.input_price_per_1k_tokens,
            output_price_per_1k_tokens=data.output_price_per_1k_tokens,
            cache_creation_5m_price_per_1k_tokens=data.cache_creation_5m_price_per_1k_tokens,
            cache_creation_1h_price_per_1k_tokens=data.cache_creation_1h_price_per_1k_tokens,
            cache_read_price_per_1k_tokens=data.cache_read_price_per_1k_tokens,
            effective_from=data.effective_from,
            created_by=actor.user_id,
        )
        await repo.create_pricing(pricing)

        await self._cache_mgr.invalidate([f"model:{alias}"], session=session)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="SET_PRICING",
            resource_type="ModelPricing",
            resource_id=alias,
            changes={"after": {
                "input_price": str(data.input_price_per_1k_tokens),
                "output_price": str(data.output_price_per_1k_tokens),
                "cache_creation_price": str(data.cache_creation_5m_price_per_1k_tokens),
                "cache_creation_1h_price": str(data.cache_creation_1h_price_per_1k_tokens),
                "cache_read_price": str(data.cache_read_price_per_1k_tokens),
            }},
            ip_address=ip_address,
            request_id=request_id,
        )

        return self._to_response(model, pricing)

    async def preview_price_sync(
        self,
        session: AsyncSession,
        *,
        pricing_sync_service,
        quantize: Decimal = Decimal("0.000001"),
    ):
        """AWS Price List 단가 vs DB 현재가 diff 미리보기(쓰기 없음, deepdive 가격동기화).

        BEDROCK provider 모델만 대상. AWS 에서 못 찾으면 matched=False 로 표시(스킵 후보).
        """
        from app.schemas.models import (
            PriceSyncDiff,
            PriceSyncPreviewResponse,
        )

        repo = ModelRepository(session)
        models = await repo.list_all()
        fetched = await pricing_sync_service.fetch_bedrock_prices()

        diffs: list[PriceSyncDiff] = []
        matched = 0
        changed = 0
        for m in models:
            if m.provider != Provider.BEDROCK:
                continue  # OpenModel/vLLM 은 AWS 단가 없음
            cur = await repo.get_current_pricing(m.alias)
            cur_resp = self._to_response(m, cur).current_pricing
            np = fetched.prices.get(m.provider_model_id.lower())
            if np is None:
                diffs.append(PriceSyncDiff(
                    alias=m.alias,
                    provider_model_id=m.provider_model_id,
                    matched=False,
                    note="AWS Price List 에서 단가 미발견(모델ID 매칭 실패 또는 미게시)",
                    current=cur_resp,
                ))
                continue
            matched += 1
            p_in = np.input_per_1k.quantize(quantize)
            p_out = np.output_per_1k.quantize(quantize)
            p_5m = np.cache_5m_per_1k.quantize(quantize)
            p_1h = np.cache_1h_per_1k.quantize(quantize)
            p_rd = np.cache_read_per_1k.quantize(quantize)
            is_changed = cur is None or any([
                cur.input_price_per_1k_tokens != p_in,
                cur.output_price_per_1k_tokens != p_out,
                cur.cache_creation_5m_price_per_1k_tokens != p_5m,
                cur.cache_creation_1h_price_per_1k_tokens != p_1h,
                cur.cache_read_price_per_1k_tokens != p_rd,
            ])
            if is_changed:
                changed += 1
            diffs.append(PriceSyncDiff(
                alias=m.alias,
                provider_model_id=m.provider_model_id,
                matched=True,
                note="캐시 단가 일부 파생(AWS 미게시 → input 기반 추정)" if np.cache_derived else None,
                current=cur_resp,
                proposed_input_per_1k=p_in,
                proposed_output_per_1k=p_out,
                proposed_cache_5m_per_1k=p_5m,
                proposed_cache_1h_per_1k=p_1h,
                proposed_cache_read_per_1k=p_rd,
                changed=is_changed,
            ))

        return PriceSyncPreviewResponse(
            region=getattr(pricing_sync_service, "region", "us-east-1"),
            diffs=diffs,
            matched_count=matched,
            changed_count=changed,
        )

    async def apply_price_sync(
        self,
        session: AsyncSession,
        *,
        pricing_sync_service,
        aliases: list[str],
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
        quantize: Decimal = Decimal("0.000001"),
    ):
        """승인된 alias 목록만 AWS 단가로 적용 — 기존 set_pricing 재사용(시계열·감사·캐시).

        자동 전체적용 금지: 호출자가 preview 후 명시 선택한 aliases 만.
        """
        from app.schemas.models import PriceSyncApplyResponse, PricingRequest

        repo = ModelRepository(session)
        fetched = await pricing_sync_service.fetch_bedrock_prices()
        now = datetime.now(timezone.utc)

        applied: list[str] = []
        skipped: list[str] = []
        errors: list[str] = list(fetched.errors)

        for alias in aliases:
            model = await repo.get_by_alias(alias)
            if model is None:
                errors.append(f"{alias}: 모델 없음")
                continue
            if model.provider != Provider.BEDROCK:
                skipped.append(alias)
                continue
            np = fetched.prices.get(model.provider_model_id.lower())
            if np is None:
                skipped.append(alias)  # AWS 단가 미발견 → 적용 안 함
                continue
            req = PricingRequest(
                input_price_per_1k_tokens=np.input_per_1k.quantize(quantize),
                output_price_per_1k_tokens=np.output_per_1k.quantize(quantize),
                cache_creation_5m_price_per_1k_tokens=np.cache_5m_per_1k.quantize(quantize),
                cache_creation_1h_price_per_1k_tokens=np.cache_1h_per_1k.quantize(quantize),
                cache_read_price_per_1k_tokens=np.cache_read_per_1k.quantize(quantize),
                effective_from=now,
            )
            # 기존 set_pricing 재사용 → close_current_pricing + 새 행 + 캐시무효화 + SET_PRICING 감사
            await self.set_pricing(
                session, alias=alias, data=req, actor=actor,
                ip_address=ip_address, request_id=request_id,
            )
            applied.append(alias)

        return PriceSyncApplyResponse(applied=applied, skipped=skipped, errors=errors)

    async def patch_status(
        self,
        session: AsyncSession,
        *,
        alias: str,
        data: StatusPatchRequest,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> ModelResponse:
        repo = ModelRepository(session)
        new_status = ModelStatus.ACTIVE if data.active else ModelStatus.INACTIVE
        model = await repo.patch_status(alias, new_status)
        if model is None:
            raise NotFoundError("ModelAlias", alias)

        pricing = await repo.get_current_pricing(alias)

        # BR-MOD-03/04: Immediate cache invalidation on INACTIVE
        await self._cache_mgr.invalidate([f"model:{alias}", "model:list"], session=session)

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="PATCH_MODEL_STATUS",
            resource_type="ModelAlias",
            resource_id=alias,
            changes={"after": {"status": new_status.value}},
            ip_address=ip_address,
            request_id=request_id,
        )

        return self._to_response(model, pricing)

    @staticmethod
    def _to_response(model: ModelAlias, pricing: ModelPricing | None) -> ModelResponse:
        pricing_resp = None
        if pricing:
            pricing_resp = ModelPricingResponse(
                input_price_per_1k_tokens=pricing.input_price_per_1k_tokens,
                output_price_per_1k_tokens=pricing.output_price_per_1k_tokens,
                cache_creation_5m_price_per_1k_tokens=pricing.cache_creation_5m_price_per_1k_tokens,
                cache_creation_1h_price_per_1k_tokens=pricing.cache_creation_1h_price_per_1k_tokens,
                cache_read_price_per_1k_tokens=pricing.cache_read_price_per_1k_tokens,
                effective_from=pricing.effective_from,
                effective_until=pricing.effective_until,
            )
        return ModelResponse(
            alias=model.alias,
            provider=model.provider,
            provider_model_id=model.provider_model_id,
            endpoint_url=model.endpoint_url,
            api_format=model.api_format,
            status=model.status.value,
            # 3-상태를 그대로 노출한다 — [] 를 None 으로 뭉개면 운영자가 자기가 만든
            # 전면 거부를 화면에서 볼 수 없다.
            allowed_clients=model.allowed_clients,
            description=model.description,
            # display_name 은 표시 전용 — 비어 있으면 alias 로 대체해 모든 API
            # 소비자(목록·피커·정책 표시)가 같은 이름을 보게 한다. DB 는 NULL 유지.
            display_name=model.display_name or model.alias,
            context_window=model.context_window,
            max_output_tokens=model.max_output_tokens,
            current_pricing=pricing_resp,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
