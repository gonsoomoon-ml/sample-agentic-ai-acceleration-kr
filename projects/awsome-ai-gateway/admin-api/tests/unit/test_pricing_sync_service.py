# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""PricingSyncService 정규화 로직 검증 — 가짜 pricing client(실제 AWS 호출 없음).

핵심 위험 지점: Price List SKU → per-1k 단위 정규화 + input/output/cache 분류 +
캐시 단가 파생(미게시 시). 실 AWS 호출은 통합테스트 영역이므로 여기선 결정적 로직만.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.services.pricing_sync_service import (
    FetchResult,
    LiteLLMPricingSyncService,
    NormalizedPrice,
    PricingSyncService,
)


class _FakePricingClient:
    """boto3 pricing client 흉내 — get_products 가 PriceList(JSON 문자열 배열) 반환."""

    def __init__(self, products: list[dict]):
        self._products = [json.dumps(p) for p in products]

    def get_products(self, **kwargs):
        return {"PriceList": self._products, "NextToken": None}


def _product(model_id: str, usagetype: str, usd: str, unit: str) -> dict:
    return {
        "product": {"attributes": {"model": model_id, "usagetype": usagetype}},
        "terms": {
            "OnDemand": {
                "x": {"priceDimensions": {"y": {"pricePerUnit": {"USD": usd}, "unit": unit}}}
            }
        },
    }


@pytest.mark.asyncio
async def test_normalizes_per_1m_to_per_1k():
    """per-1M tokens 단가를 per-1k 로 환산(÷1000)."""
    client = _FakePricingClient([
        _product("anthropic.claude-x", "InputTokenCount", "3.00", "1M tokens"),   # $3/1M → $0.003/1k
        _product("anthropic.claude-x", "OutputTokenCount", "15.00", "1M tokens"),  # $15/1M → $0.015/1k
    ])
    res = await PricingSyncService(client).fetch_bedrock_prices()
    assert not res.errors
    p = res.prices["anthropic.claude-x"]
    assert p.input_per_1k == Decimal("0.003")
    assert p.output_per_1k == Decimal("0.015")


@pytest.mark.asyncio
async def test_cache_derived_when_not_published():
    """캐시 SKU 가 없으면 input 기반 파생(5m=×1.25, 1h=×2.0, read=×0.1) + derived 플래그."""
    client = _FakePricingClient([
        _product("m1", "InputTokenCount", "1.00", "1K tokens"),   # $1/1k
        _product("m1", "OutputTokenCount", "2.00", "1K tokens"),
    ])
    res = await PricingSyncService(client).fetch_bedrock_prices()
    p = res.prices["m1"]
    assert p.cache_derived is True
    assert p.cache_5m_per_1k == Decimal("1.25")   # 1.00 × 1.25
    assert p.cache_1h_per_1k == Decimal("2.0")    # 1.00 × 2.0
    assert p.cache_read_per_1k == Decimal("0.1")  # 1.00 × 0.1


@pytest.mark.asyncio
async def test_cache_explicit_when_published():
    """캐시 SKU 가 있으면 파생 안 하고 그 값 사용, derived=False."""
    client = _FakePricingClient([
        _product("m2", "InputTokenCount", "1.00", "1K tokens"),
        _product("m2", "OutputTokenCount", "2.00", "1K tokens"),
        _product("m2", "CacheWriteInputTokenCount", "0.30", "1K tokens"),
        _product("m2", "CacheRead-InputTokenCount", "0.05", "1K tokens"),
        _product("m2", "CacheWrite-1h-InputTokenCount", "0.60", "1K tokens"),
    ])
    res = await PricingSyncService(client).fetch_bedrock_prices()
    p = res.prices["m2"]
    assert p.cache_derived is False
    assert p.cache_5m_per_1k == Decimal("0.3")
    assert p.cache_read_per_1k == Decimal("0.05")
    assert p.cache_1h_per_1k == Decimal("0.6")


@pytest.mark.asyncio
async def test_skips_partial_models_without_input_output():
    """input/output 둘 다 없으면 비용계산 불가 → 스킵(부분 데이터)."""
    client = _FakePricingClient([
        _product("partial", "CacheReadInputTokenCount", "0.05", "1K tokens"),  # cache만
    ])
    res = await PricingSyncService(client).fetch_bedrock_prices()
    assert "partial" not in res.prices


@pytest.mark.asyncio
async def test_fetch_failure_is_fail_soft():
    """get_products 예외 → errors 에 담고 빈 결과(fail-soft, hot-path 안 죽임)."""

    class _Boom:
        def get_products(self, **kwargs):
            raise RuntimeError("AccessDenied")

    res = await PricingSyncService(_Boom()).fetch_bedrock_prices()
    assert res.prices == {}
    assert res.errors and "AccessDenied" in res.errors[0]


# ── LiteLLM Model Catalog API ──


class _MockAsyncResponse:
    def __init__(self, json_data: dict, status_code: int = 200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=None,
                response=self,  # type: ignore[arg-type]
            )

    def json(self):
        return self._json


class _FakeAsyncClient:
    """httpx.AsyncClient 흉내 — get() 호출 시 미리 등록한 페이지를 순서대로 반환."""

    def __init__(self, pages: list[dict]):
        self._pages = pages
        self._idx = 0
        self.closed = False

    async def get(self, url: str, params: dict | None = None):
        data = self._pages[self._idx]
        self._idx += 1
        return _MockAsyncResponse(data)

    async def aclose(self):
        self.closed = True


def _llm_model(
    model_id: str,
    input_per_token: float,
    output_per_token: float,
    cache_read: float | None = None,
    cache_5m: float | None = None,
    cache_1h: float | None = None,
    max_input: int | None = None,
    max_output: int | None = None,
) -> dict:
    return {
        "id": model_id,
        "provider": "bedrock_converse",
        "mode": "chat",
        "input_cost_per_token": input_per_token,
        "output_cost_per_token": output_per_token,
        "cache_read_input_token_cost": cache_read,
        "cache_creation_input_token_cost": cache_5m,
        "cache_creation_input_token_cost_above_1hr": cache_1h,
        "max_input_tokens": max_input,
        "max_output_tokens": max_output,
    }


@pytest.mark.asyncio
async def test_litellm_normalizes_per_token_to_per_1k():
    """per-token 단가를 per-1k 로 변환(×1000)."""
    client = _FakeAsyncClient([
        {
            "data": [
                _llm_model("us.writer.palmyra-x4-v1:0", 2.5e-06, 1e-05),
            ],
            "has_more": False,
        }
    ])
    res = await LiteLLMPricingSyncService(http_client=client).fetch_bedrock_prices()
    assert not res.errors
    p = res.prices["writer.palmyra-x4-v1:0"]  # region prefix stripped
    assert p.input_per_1k == Decimal("0.0025")
    assert p.output_per_1k == Decimal("0.01")


@pytest.mark.asyncio
async def test_litellm_cache_derived_when_not_provided():
    """캐시 필드가 없으면 input 기반 파생 + derived=True."""
    client = _FakeAsyncClient([
        {
            "data": [
                _llm_model("writer.palmyra-x5-v1:0", 6e-07, 6e-06),
            ],
            "has_more": False,
        }
    ])
    res = await LiteLLMPricingSyncService(http_client=client).fetch_bedrock_prices()
    p = res.prices["writer.palmyra-x5-v1:0"]
    assert p.cache_derived is True
    assert p.cache_5m_per_1k == Decimal("0.00075")   # 6e-07 * 1.25 * 1000
    assert p.cache_1h_per_1k == Decimal("0.0012")   # 6e-07 * 2.0 * 1000
    assert p.cache_read_per_1k == Decimal("0.00006") # 6e-07 * 0.1 * 1000


@pytest.mark.asyncio
async def test_litellm_cache_explicit_when_provided():
    """캐시 필드가 제공되면 그 값을 사용하고 derived=False."""
    client = _FakeAsyncClient([
        {
            "data": [
                _llm_model(
                    "anthropic.claude-sonnet-4-6",
                    3e-06,
                    1.5e-05,
                    cache_read=3e-07,
                    cache_5m=3.75e-07,
                    cache_1h=6e-07,
                ),
            ],
            "has_more": False,
        }
    ])
    res = await LiteLLMPricingSyncService(http_client=client).fetch_bedrock_prices()
    p = res.prices["anthropic.claude-sonnet-4-6"]
    assert p.cache_derived is False
    assert p.cache_read_per_1k == Decimal("0.0003")
    assert p.cache_5m_per_1k == Decimal("0.000375")
    assert p.cache_1h_per_1k == Decimal("0.0006")


@pytest.mark.asyncio
async def test_litellm_pagination():
    """has_more 가 True 이면 다음 페이지를 추가로 요청."""
    client = _FakeAsyncClient([
        {
            "data": [_llm_model("model-a", 1e-06, 2e-06)],
            "has_more": True,
        },
        {
            "data": [_llm_model("model-b", 3e-06, 4e-06)],
            "has_more": False,
        },
    ])
    res = await LiteLLMPricingSyncService(http_client=client).fetch_bedrock_prices()
    assert set(res.prices.keys()) == {"model-a", "model-b"}
    assert client._idx == 2  # 2페이지 호출


@pytest.mark.asyncio
async def test_litellm_fetch_failure_is_fail_soft():
    """LiteLLM API 예외 → errors 에 담고 빈 결과."""

    class _BoomClient:
        async def get(self, url: str, params: dict | None = None):
            raise RuntimeError("timeout")

        async def aclose(self):
            pass

    res = await LiteLLMPricingSyncService(http_client=_BoomClient()).fetch_bedrock_prices()
    assert res.prices == {}
    assert res.errors and "timeout" in res.errors[0]


def test_fetch_result_lookup_with_region_prefix_variants():
    """region prefix 유무에 따른 매칭: DB=global.xxx 를 source=us.xxx / xxx 로 찾을 수 있다."""
    result = FetchResult(
        prices={
            "anthropic.claude-sonnet-4-6": NormalizedPrice(
                input_per_1k=Decimal("0.003"),
                output_per_1k=Decimal("0.015"),
                cache_5m_per_1k=Decimal("0"),
                cache_1h_per_1k=Decimal("0"),
                cache_read_per_1k=Decimal("0"),
            )
        }
    )
    assert result.lookup("global.anthropic.claude-sonnet-4-6") is not None
    assert result.lookup("us.anthropic.claude-sonnet-4-6") is not None
    assert result.lookup("anthropic.claude-sonnet-4-6") is not None
    assert result.lookup("anthropic.claude-opus-4-6") is None


@pytest.mark.asyncio
async def test_litellm_extracts_context_spec():
    """max_input_tokens/max_output_tokens 가 NormalizedPrice 에 실린다."""
    client = _FakeAsyncClient([
        {
            "data": [
                _llm_model("anthropic.claude-opus-5", 5e-06, 2.5e-05,
                           max_input=1000000, max_output=64000),
            ],
            "has_more": False,
        }
    ])
    res = await LiteLLMPricingSyncService(http_client=client).fetch_bedrock_prices()
    p = res.prices["anthropic.claude-opus-5"]
    assert p.context_window == 1000000
    assert p.max_output_tokens == 64000


@pytest.mark.asyncio
async def test_litellm_context_spec_absent():
    """스펙 필드가 없으면 None — 기존 모델 값을 덮어쓰지 않도록 구별한다."""
    client = _FakeAsyncClient([
        {
            "data": [_llm_model("anthropic.claude-sonnet-5", 3e-06, 1.5e-05)],
            "has_more": False,
        }
    ])
    res = await LiteLLMPricingSyncService(http_client=client).fetch_bedrock_prices()
    p = res.prices["anthropic.claude-sonnet-5"]
    assert p.context_window is None
    assert p.max_output_tokens is None
