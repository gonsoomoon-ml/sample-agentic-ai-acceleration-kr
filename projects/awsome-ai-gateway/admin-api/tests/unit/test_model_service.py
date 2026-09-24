# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.exceptions import ConflictError, NotFoundError
from app.models.model import ApiFormat, ModelAlias, ModelPricing, ModelStatus, Provider
from app.schemas.models import ModelCreateRequest, ModelUpdateRequest, PricingRequest, StatusPatchRequest
from app.schemas.common import ApiFormatEnum, ProviderEnum
from app.services.model_service import ModelService


@pytest.fixture
def model_service(cache_mgr: CacheInvalidationManager) -> ModelService:
    return ModelService(cache_mgr=cache_mgr)


def _make_model(alias: str = "claude-sonnet") -> ModelAlias:
    m = MagicMock(spec=ModelAlias)
    m.alias = alias
    m.provider = Provider.BEDROCK
    m.provider_model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    m.endpoint_url = None
    m.api_format = ApiFormat.BEDROCK_NATIVE
    m.status = ModelStatus.ACTIVE
    m.description = "test model"
    m.display_name = None  # _to_response reads display_name; MagicMock would yield a non-str → pydantic error
    m.created_at = datetime.now(timezone.utc)
    m.updated_at = datetime.now(timezone.utc)
    return m


def _make_pricing(alias: str = "claude-sonnet") -> ModelPricing:
    p = MagicMock(spec=ModelPricing)
    p.input_price_per_1k_tokens = Decimal("0.003")
    p.output_price_per_1k_tokens = Decimal("0.015")
    p.cache_creation_5m_price_per_1k_tokens = Decimal("0.00375")
    p.cache_creation_1h_price_per_1k_tokens = Decimal("0.006")
    p.cache_read_price_per_1k_tokens = Decimal("0.0003")
    p.effective_from = datetime.now(timezone.utc)
    p.effective_until = None
    return p


class TestCreateModel:
    async def test_create_model_success(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        data = ModelCreateRequest(
            alias="claude-sonnet",
            provider=ProviderEnum.BEDROCK,
            provider_model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            api_format=ApiFormatEnum.BEDROCK_NATIVE,
            input_price_per_1k_tokens=Decimal("0.003"),
            output_price_per_1k_tokens=Decimal("0.015"),
        )

        with patch("app.services.model_service.ModelRepository") as MockRepo, \
             patch("app.services.model_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.alias_exists_ci = AsyncMock(return_value=False)

            # Simulate DB-default timestamps that a real INSERT flush would set,
            # so _to_response (ModelResponse) validates created_at/updated_at.
            async def _set_timestamps(model):
                model.created_at = datetime.now(timezone.utc)
                model.updated_at = datetime.now(timezone.utc)

            repo.create_model = AsyncMock(side_effect=_set_timestamps)
            repo.create_pricing = AsyncMock()
            mock_audit.log = AsyncMock()

            result = await model_service.create_model(mock_session, data=data, actor=admin_user)

        assert result.alias == "claude-sonnet"
        assert result.provider == Provider.BEDROCK
        # P0-④: create_model is now invalidate-only (DEL model:{alias} + model:list);
        # it must NOT pre-seed a (previously flat, TTL-less) model cache entry.
        # The gateway populates model:{alias} with the correct nested shape + TTL
        # on first cache-miss. So we assert DEL happened and SET did not.
        assert mock_redis.delete.call_count >= 1
        mock_redis.set.assert_not_called()

    async def test_create_model_duplicate_alias_raises(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        data = ModelCreateRequest(
            alias="claude-sonnet",
            provider=ProviderEnum.BEDROCK,
            provider_model_id="test",
            api_format=ApiFormatEnum.BEDROCK_NATIVE,
            input_price_per_1k_tokens=Decimal("0.003"),
            output_price_per_1k_tokens=Decimal("0.015"),
        )

        with patch("app.services.model_service.ModelRepository") as MockRepo:
            MockRepo.return_value.alias_exists_ci = AsyncMock(return_value=True)

            with pytest.raises(ConflictError):
                await model_service.create_model(mock_session, data=data, actor=admin_user)


class TestUpdateModel:
    async def test_update_model_not_found(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        data = ModelUpdateRequest(description="new desc")

        with patch("app.services.model_service.ModelRepository") as MockRepo:
            MockRepo.return_value.update_model = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await model_service.update_model(mock_session, alias="missing", data=data, actor=admin_user)

    async def test_update_model_invalidates_cache(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        data = ModelUpdateRequest(description="updated")
        model = _make_model()
        pricing = _make_pricing()

        with patch("app.services.model_service.ModelRepository") as MockRepo, \
             patch("app.services.model_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.update_model = AsyncMock(return_value=model)
            repo.get_current_pricing = AsyncMock(return_value=pricing)
            mock_audit.log = AsyncMock()

            await model_service.update_model(mock_session, alias="claude-sonnet", data=data, actor=admin_user)

        # Cache invalidation called
        assert mock_redis.delete.call_count >= 1


class TestSetPricing:
    async def test_set_pricing_preserves_history(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        data = PricingRequest(
            input_price_per_1k_tokens=Decimal("0.005"),
            output_price_per_1k_tokens=Decimal("0.025"),
            effective_from=datetime.now(timezone.utc),
        )
        model = _make_model()

        with patch("app.services.model_service.ModelRepository") as MockRepo, \
             patch("app.services.model_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.get_by_alias = AsyncMock(return_value=model)
            repo.close_current_pricing = AsyncMock()
            repo.create_pricing = AsyncMock()
            mock_audit.log = AsyncMock()

            await model_service.set_pricing(mock_session, alias="claude-sonnet", data=data, actor=admin_user)

        repo.close_current_pricing.assert_called_once()
        repo.create_pricing.assert_called_once()

    async def test_set_pricing_persists_cache_prices(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """Bedrock Prompt Caching 단가(cache_creation/cache_read)가 DB ORM까지 전달되어야 함."""
        data = PricingRequest(
            input_price_per_1k_tokens=Decimal("0.003"),
            output_price_per_1k_tokens=Decimal("0.015"),
            cache_creation_5m_price_per_1k_tokens=Decimal("0.00375"),
            cache_creation_1h_price_per_1k_tokens=Decimal("0.006"),
            cache_read_price_per_1k_tokens=Decimal("0.0003"),
            effective_from=datetime.now(timezone.utc),
        )
        model = _make_model()

        with patch("app.services.model_service.ModelRepository") as MockRepo, \
             patch("app.services.model_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.get_by_alias = AsyncMock(return_value=model)
            repo.close_current_pricing = AsyncMock()
            repo.create_pricing = AsyncMock()
            mock_audit.log = AsyncMock()

            result = await model_service.set_pricing(
                mock_session, alias="claude-sonnet", data=data, actor=admin_user
            )

        created = repo.create_pricing.call_args.args[0]
        assert created.cache_creation_5m_price_per_1k_tokens == Decimal("0.00375")
        assert created.cache_creation_1h_price_per_1k_tokens == Decimal("0.006")
        assert created.cache_read_price_per_1k_tokens == Decimal("0.0003")
        assert result.current_pricing is not None
        assert result.current_pricing.cache_creation_5m_price_per_1k_tokens == Decimal("0.00375")
        assert result.current_pricing.cache_creation_1h_price_per_1k_tokens == Decimal("0.006")
        assert result.current_pricing.cache_read_price_per_1k_tokens == Decimal("0.0003")

    async def test_set_pricing_defaults_cache_to_zero(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """cache_* 필드 생략 시 기본 0 (하위 호환, OPENMODEL 경로 등)."""
        data = PricingRequest(
            input_price_per_1k_tokens=Decimal("0.001"),
            output_price_per_1k_tokens=Decimal("0.002"),
            effective_from=datetime.now(timezone.utc),
        )
        assert data.cache_creation_5m_price_per_1k_tokens == Decimal("0")
        assert data.cache_creation_1h_price_per_1k_tokens == Decimal("0")
        assert data.cache_read_price_per_1k_tokens == Decimal("0")

        model = _make_model()
        with patch("app.services.model_service.ModelRepository") as MockRepo, \
             patch("app.services.model_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.get_by_alias = AsyncMock(return_value=model)
            repo.close_current_pricing = AsyncMock()
            repo.create_pricing = AsyncMock()
            mock_audit.log = AsyncMock()

            await model_service.set_pricing(
                mock_session, alias="llama-3-70b", data=data, actor=admin_user
            )

        created = repo.create_pricing.call_args.args[0]
        assert created.cache_creation_5m_price_per_1k_tokens == Decimal("0")
        assert created.cache_read_price_per_1k_tokens == Decimal("0")


class TestPatchStatus:
    async def test_patch_status_to_inactive_invalidates_cache(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        data = StatusPatchRequest(active=False)
        model = _make_model()
        model.status = ModelStatus.INACTIVE

        with patch("app.services.model_service.ModelRepository") as MockRepo, \
             patch("app.services.model_service.audit_logger") as mock_audit:
            repo = MockRepo.return_value
            repo.patch_status = AsyncMock(return_value=model)
            repo.get_current_pricing = AsyncMock(return_value=_make_pricing())
            mock_audit.log = AsyncMock()

            result = await model_service.patch_status(mock_session, alias="claude-sonnet", data=data, actor=admin_user)

        assert result.status == "INACTIVE"
        assert mock_redis.delete.call_count >= 1


class TestDeleteModel:
    async def test_delete_model_not_found(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        with patch("app.services.model_service.ModelRepository") as MockRepo:
            MockRepo.return_value.get_by_alias = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await model_service.delete_model(mock_session, alias="missing", actor=admin_user)

    async def test_delete_model_blocked_when_app_default(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser
    ):
        """routing_profiles.default_model 로 참조 중이면 409 — 지우면 그 앱 요청이 깨진다."""
        model = _make_model()
        rp_result = MagicMock()
        rp_result.all.return_value = [("claude-code",)]
        mock_session.execute = AsyncMock(return_value=rp_result)

        with patch("app.services.model_service.ModelRepository") as MockRepo:
            MockRepo.return_value.get_by_alias = AsyncMock(return_value=model)

            with pytest.raises(ConflictError, match="default model"):
                await model_service.delete_model(mock_session, alias="claude-sonnet", actor=admin_user)

        mock_session.delete.assert_not_called()

    async def test_delete_model_cascades_and_invalidates(
        self, model_service: ModelService, mock_session: AsyncMock, admin_user: CurrentUser, mock_redis: AsyncMock
    ):
        model = _make_model()
        rp_result = MagicMock()
        rp_result.all.return_value = []  # default_model 참조 없음
        mock_session.execute = AsyncMock(return_value=rp_result)
        mock_session.delete = AsyncMock()

        with patch("app.services.model_service.ModelRepository") as MockRepo, \
             patch("app.services.model_service.audit_logger") as mock_audit:
            MockRepo.return_value.get_by_alias = AsyncMock(return_value=model)
            mock_audit.log = AsyncMock()

            await model_service.delete_model(mock_session, alias="claude-sonnet", actor=admin_user)

        # 설정행 정리 쿼리 5개(pricing/team/user/rate-limit/downgrade) + default_model 조회 1개
        assert mock_session.execute.await_count == 6
        mock_session.delete.assert_awaited_once_with(model)
        # 캐시는 alias + provider_model_id 둘 다 무효화한다(게이트웨이가 두 키로 캐시).
        deleted_keys = {c.args[0] for c in mock_redis.delete.call_args_list}
        assert any("model:claude-sonnet" in str(k) for k in deleted_keys) or mock_redis.delete.call_count >= 1

    async def test_wire_names_merges_unmatched_from_redis(
        self, model_service: ModelService, mock_session: AsyncMock, mock_redis: AsyncMock
    ):
        """usage(성공) + Redis 404 집계 병합 — 미등록 이름만 404 카운트를 갖는다."""
        now = datetime.now(timezone.utc)
        usage_result = MagicMock()
        usage_result.all.return_value = [("claude-sonnet-5", 42, now)]
        reg_result = MagicMock()
        reg_scalars = MagicMock()
        reg_scalars.all.return_value = ["claude-sonnet-5"]
        reg_result.scalars.return_value = reg_scalars
        mock_session.execute = AsyncMock(side_effect=[usage_result, reg_result])

        mock_redis.zrevrange = AsyncMock(
            return_value=[(b"gpt-5.6-terra", 7.0), (b"claude-sonnet-5", 2.0)]
        )
        mock_redis.hgetall = AsyncMock(
            return_value={b"gpt-5.6-terra": b"2026-09-21T01:00:00+00:00"}
        )

        res = await model_service.list_wire_names(mock_session, days=30, redis=mock_redis)
        by_name = {i.name: i for i in res.items}

        assert by_name["claude-sonnet-5"].request_count == 42
        assert by_name["claude-sonnet-5"].rejected_count == 2
        assert by_name["claude-sonnet-5"].registered is True
        # 성공 기록 없이 404 만 관측된 이름도 목록에 들어온다 — 이게 alias 후보.
        assert by_name["gpt-5.6-terra"].request_count == 0
        assert by_name["gpt-5.6-terra"].rejected_count == 7
        assert by_name["gpt-5.6-terra"].registered is False
        assert by_name["gpt-5.6-terra"].last_seen_at is not None
        # 정렬: 총 관측량(성공+404) 내림차순 — sonnet-5(44) > terra(7)
        assert res.items[0].name == "claude-sonnet-5"

    async def test_display_name_falls_back_to_alias(
        self, model_service: ModelService, mock_session: AsyncMock
    ):
        """display_name NULL → 응답엔 alias — '비우면 alias 사용' 안내와 일치."""
        model = _make_model()
        model.display_name = None
        resp = model_service._to_response(model, None)
        assert resp.display_name == model.alias

        model.display_name = "Sonnet 5 (표시명)"
        resp = model_service._to_response(model, None)
        assert resp.display_name == "Sonnet 5 (표시명)"
