# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import delete as sa_delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_logger
from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.exceptions import ConflictError, NotFoundError
from app.models.budget import DowngradePolicy
from app.models.model import (
    ApiFormat,
    ModelAlias,
    ModelPricing,
    ModelStatus,
    Provider,
    RateLimitConfig,
    TeamAllowedModel,
    UserAllowedModel,
)
from app.models.routing import RoutingProfile
from app.repositories.model_repository import ModelRepository
from app.schemas.models import (
    ModelCreateRequest,
    ModelPricingResponse,
    ModelResponse,
    ModelUpdateRequest,
    PricingRequest,
    WireNameItem,
    WireNameListResponse,
    StatusPatchRequest,
)

logger = structlog.get_logger()


def _parse_iso(value) -> datetime | None:
    """Redis 에 저장한 ISO 문자열 → datetime. 파싱 실패 시 None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


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

    async def list_wire_names(
        self, session: AsyncSession, *, days: int = 30, redis=None
    ) -> WireNameListResponse:
        """최근 N일간 클라이언트가 실제로 보낸 모델 이름(와이어 키) 목록.

        두 소스를 합친다:
          - usage_logs: resolve 성공 요청(보통 등록된 이름)
          - Redis gw:unmatched_models: resolve 실패(404) 이름 — 게이트웨이가
            집계. **미등록 이름으로 들어오는 신호는 여기에만 있다** — 새 모델이
            나와 클라이언트가 새 이름을내면 이 목록에 뜬다.
        """
        from app.models.usage import UsageLog

        days = max(1, min(days, 365))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = (
            await session.execute(
                select(
                    UsageLog.model_alias,
                    func.count(),
                    func.max(UsageLog.requested_at),
                )
                .where(UsageLog.requested_at >= cutoff)
                .group_by(UsageLog.model_alias)
                .order_by(func.count().desc())
                .limit(100)
            )
        ).all()
        registered = set(
            (await session.execute(select(ModelAlias.alias))).scalars().all()
        )

        items: dict[str, WireNameItem] = {
            name: WireNameItem(
                name=name,
                request_count=cnt,
                last_seen_at=last,
                registered=name in registered,
            )
            for name, cnt, last in rows
        }

        # 404 집계 병합 — 성공 기록이 없는 미등록 이름도 rows 에 추가한다.
        if redis is not None:
            try:
                rejected = await redis.zrevrange(
                    "gw:unmatched_models", 0, 99, withscores=True
                )
                last_seen = await redis.hgetall("gw:unmatched_models:last_seen")
                for raw_name, score in rejected:
                    name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name
                    item = items.get(name)
                    if item is None:
                        raw_last = last_seen.get(raw_name) or last_seen.get(name)
                        if isinstance(raw_last, bytes):
                            raw_last = raw_last.decode()
                        item = WireNameItem(
                            name=name,
                            request_count=0,
                            last_seen_at=_parse_iso(raw_last),
                            registered=name in registered,
                        )
                        items[name] = item
                    item.rejected_count = int(score)
            except Exception:  # noqa: BLE001 — Redis 장애가 목록을 깨면 안 된다
                logger.warning("wire_names_unmatched_fetch_failed")

        merged = sorted(
            items.values(),
            key=lambda i: (i.rejected_count + i.request_count, i.name),
            reverse=True,
        )
        return WireNameListResponse(days=days, items=merged)

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

    async def delete_model(
        self,
        session: AsyncSession,
        *,
        alias: str,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> None:
        """모델 alias 삭제.

        alias 는 요청 라우팅 키이므로, 없는 모델을 가리키는 설정행은 무의미하다 —
        FK(RESTRICT) 참조를 같은 트랜잭션에서 같이 지운다: pricing 이력,
        team/user_allowed_models, 모델별 rate_limit, downgrade 규칙(from/to).
        usage_logs 는 FK 없는 과금 이력이라 보존한다.

        차단(409): routing_profiles.default_model 로 참조 중이면 거부한다.
        default_model 은 FK 가 없어 DB 가 못 막고, 지우면 그 앱의 모든 요청이
        런타임에 404/resolve 실패로 깨진다 — 먼저 다른 기본 모델로 바꿔야 한다.
        """
        repo = ModelRepository(session)
        model = await repo.get_by_alias(alias)
        if model is None:
            raise NotFoundError("ModelAlias", alias)

        rp_clients = (
            await session.execute(
                select(RoutingProfile.client).where(
                    RoutingProfile.default_model == alias
                )
            )
        ).all()
        if rp_clients:
            raise ConflictError(
                f"Model '{alias}' is the default model of app(s): "
                f"{', '.join(sorted(r[0] for r in rp_clients))}. "
                f"Change the default model first."
            )

        await session.execute(
            sa_delete(ModelPricing).where(ModelPricing.model_alias == alias)
        )
        await session.execute(
            sa_delete(TeamAllowedModel).where(TeamAllowedModel.model_alias == alias)
        )
        await session.execute(
            sa_delete(UserAllowedModel).where(UserAllowedModel.model_alias == alias)
        )
        await session.execute(
            sa_delete(RateLimitConfig).where(RateLimitConfig.model_alias == alias)
        )
        await session.execute(
            sa_delete(DowngradePolicy).where(
                or_(
                    DowngradePolicy.from_model_alias == alias,
                    DowngradePolicy.to_model_alias == alias,
                )
            )
        )
        await session.delete(model)

        # 게이트웨이는 model:{alias} 와 model:{provider_model_id} 두 키로 캐시한다.
        await self._cache_mgr.invalidate(
            [f"model:{alias}", f"model:{model.provider_model_id}", "model:list"],
            session=session,
        )

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="DELETE_MODEL",
            resource_type="ModelAlias",
            resource_id=alias,
            changes={"before": {"alias": alias, "provider_model_id": model.provider_model_id}},
            ip_address=ip_address,
            request_id=request_id,
        )

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
            np = fetched.lookup(m.provider_model_id)
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
            # 스펙(context_window/max_output_tokens) 차이도 별도 플래그 —
            # 단가 동일해도 스펙만 새로 채워지는 경우가 있다.
            spec_changed = bool(
                (np.context_window and m.context_window != np.context_window)
                or (np.max_output_tokens and m.max_output_tokens != np.max_output_tokens)
            )
            note = "캐시 단가 일부 파생(AWS 미게시 → input 기반 추정)" if np.cache_derived else None
            if spec_changed:
                note = (note + " · " if note else "") + "스펙 갱신(context/max output)"
            diffs.append(PriceSyncDiff(
                alias=m.alias,
                provider_model_id=m.provider_model_id,
                matched=True,
                note=note,
                current=cur_resp,
                proposed_input_per_1k=p_in,
                proposed_output_per_1k=p_out,
                proposed_cache_5m_per_1k=p_5m,
                proposed_cache_1h_per_1k=p_1h,
                proposed_cache_read_per_1k=p_rd,
                changed=is_changed,
                spec_changed=spec_changed,
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
            np = fetched.lookup(model.provider_model_id)
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
            # 카탈로그가 스펙을 주면 같이 채운다 — LiteLLM만 제공, AWS 소스는 None.
            # 모델 행은 캐시 키(model:{alias})를 공유하므로 변경 시 무효화 필요.
            spec_changed = False
            if np.context_window and model.context_window != np.context_window:
                model.context_window = np.context_window
                spec_changed = True
            if np.max_output_tokens and model.max_output_tokens != np.max_output_tokens:
                model.max_output_tokens = np.max_output_tokens
                spec_changed = True
            if spec_changed:
                await session.flush()
                await self._cache_mgr.invalidate(
                    [f"model:{alias}", f"model:{model.provider_model_id}"],
                    session=session,
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
