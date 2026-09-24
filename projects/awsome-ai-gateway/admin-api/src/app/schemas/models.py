# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.clients import validate_clients
from app.schemas.common import ApiFormatEnum, ProviderEnum

# 단가 상한 — DB 컬럼에서 유도한 값이지 임의로 고른 숫자가 아니다.
#   app/models/model.py:100~109  → Numeric(10, 6)
#   db/init/02_create_tables.sql:222~226 → NUMERIC(10,6)  (0003_rename_cache_5m.py:34~38 도 동일)
# NUMERIC(10,6) = 전체 10자리 중 소수 6자리 ⇒ 정수부는 4자리뿐이므로 최대값이 9999.999999 다.
# 상한이 없으면 pydantic 은 통과시키고 asyncpg 가 INSERT 시점에 NumericValueOutOfRange 를
# 던져 그냥 500 이 된다(입력 오류인데 서버 장애처럼 보이고, 어느 필드가 문제인지도 안 나온다).
# ⚠️ 기존 decimal_places=6 **만으로는** 정수부를 전혀 제한하지 못한다(소수 자리 수만 본다).
#    max_digits=10 을 더하는 방법도 있다 — pydantic 2.13.2 실측으로는 max_digits-decimal_places
#    를 정수부 상한(4자리)으로 환산해 같은 결과를 낸다(decimal_whole_digits 에러). 그래도 여기서는
#    le 를 쓴다: pyproject 가 pydantic>=2.0.0 만 요구하므로 그 파생 규칙에 기대지 않고
#    DB 최대값을 그대로 적는 편이 버전에 무관하고 에러 메시지도 사람이 읽을 수 있다.
MAX_PRICE_PER_1K = Decimal("9999.999999")


# ── Requests ──


class ModelCreateRequest(BaseModel):
    # ⚠️ extra="forbid" — 오타 키 하나가 **201 Created 와 함께** 런타임에만 깨지는 행을
    #    만든다. 실측(적대적 검증 PROBE3): `endpoint_ur1` 로 보내면 endpoint_url=NULL 인
    #    BEDROCK_RUNTIME_OPENAI 행이 201 로 생성되는데, 그 어댑터는 endpoint_url 없이는
    #    서명 리전조차 유도할 수 없다. `displayName`(camelCase) → display_name NULL.
    #    0003 이전 이름 `cache_creation_price_per_1k_tokens` → 캐시 생성 단가 0 으로 등록.
    #    전부 등록 시점엔 성공으로 보이고 나중에 장애/오과금으로만 드러난다.
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(max_length=128)
    provider: ProviderEnum
    provider_model_id: str = Field(max_length=512)
    endpoint_url: str | None = None
    api_format: ApiFormatEnum
    description: str | None = None
    display_name: str | None = Field(default=None, max_length=128)
    input_price_per_1k_tokens: Decimal = Field(ge=0, le=MAX_PRICE_PER_1K, decimal_places=6)
    output_price_per_1k_tokens: Decimal = Field(ge=0, le=MAX_PRICE_PER_1K, decimal_places=6)
    cache_creation_5m_price_per_1k_tokens: Decimal = Field(
        default=Decimal("0"), ge=0, le=MAX_PRICE_PER_1K, decimal_places=6
    )
    cache_creation_1h_price_per_1k_tokens: Decimal = Field(
        default=Decimal("0"), ge=0, le=MAX_PRICE_PER_1K, decimal_places=6
    )
    cache_read_price_per_1k_tokens: Decimal = Field(
        default=Decimal("0"), ge=0, le=MAX_PRICE_PER_1K, decimal_places=6
    )

    #: 이 모델을 쓸 수 있는 앱 허용목록. **3-상태**(models/model.py 주석 참조):
    #:   생략/``null``  제한 없음
    #:   ``[]``         명시적으로 빈 허용목록 = 어떤 앱도 허용되지 않음
    #:   목록           그 앱들만 허용
    allowed_clients: list[str] | None = None

    @field_validator("allowed_clients")
    @classmethod
    def _validate_clients(cls, v: list[str] | None) -> list[str] | None:
        # ⚠️ None 을 그대로 통과시켜야 한다 — [] 로 정규화하면 "제한 없음" 이
        #    "전면 거부" 로 바뀐다(정확히 반대 방향의 사고).
        return validate_clients(v)


class ModelUpdateRequest(BaseModel):
    # ⚠️ extra="forbid" 필수. pydantic 기본값(extra="ignore")이면 여기 선언되지 않은 키가
    #    **조용히 버려진다**. 특히 provider / api_format 은 이 스키마에 없고 update 경로
    #    (services/model_service.py:update_model → repositories/model_repository.py:update_model)
    #    에도 없어서 admin API 로 바꿀 방법이 아예 없는 불변 필드인데, admin-ui 편집 폼은
    #    provider 드롭다운을 그대로 보여준다. 예전엔 운영자가 provider 를 바꿔 저장하면
    #    200 + 성공 토스트가 뜨고 DB 는 새 provider_model_id/endpoint_url + **옛**
    #    provider/api_format 의 반쪽 상태로 남아, 그 alias 의 모든 게이트웨이 호출이
    #    런타임에만 실패했다(편집 시점 경고 0). 이제 422 로 즉시 거부한다.
    #    provider 를 정말 바꾸려면 모델을 새로 등록해야 한다(불변 유지가 의도된 설계).
    #
    #    ⚠️ 정정: 예전 주석은 "ModelCreateRequest/PricingRequest 의 extra 키는 무해한 오타"
    #       라며 그쪽엔 forbid 를 넣지 않았다. **틀렸다** — PricingRequest 는 캐시 단가
    #       default 가 0 이라 오타가 곧 0 원 청구이고 직전 단가 행은 이미 닫힌다.
    #       ModelCreateRequest 는 오타가 201 과 함께 런타임에만 깨지는 행을 만든다.
    #       세 스키마 모두 forbid 로 통일했다(각 클래스 주석에 실측 근거).
    model_config = ConfigDict(extra="forbid")

    provider_model_id: str | None = None
    endpoint_url: str | None = None
    description: str | None = None
    # max_length matches VARCHAR(128); without it an overlong update would 500 at the DB
    # instead of a clean 422 (mirrors ModelCreateRequest.display_name).
    # NOTE: update uses an is-not-None filter, so display_name can be SET/changed but not
    # cleared back to NULL via the API (repo-wide behavior for all nullable update fields).
    display_name: str | None = Field(default=None, max_length=128)
    #: 3-상태. ⚠️ 여기서 "생략 = 유지" 와 "명시적 null = 제한 해제" 를 구별해야 한다.
    #:    null 을 생략과 같이 다루면 한 번 목록이 박힌 모델을 "제한 없음" 으로 되돌릴 API
    #:    가 사라지고, 콘솔은 그 목적으로 ``[]`` 를 보내게 된다 — 그런데 ``[]`` 는 전면
    #:    거부이므로 "제한 해제" 버튼이 그 모델을 통째로 막는다.
    #:    구별은 서비스 계층에서 ``model_fields_set`` 으로 한다.
    allowed_clients: list[str] | None = None

    @field_validator("allowed_clients")
    @classmethod
    def _validate_update_clients(cls, v: list[str] | None) -> list[str] | None:
        return validate_clients(v)



class PricingRequest(BaseModel):
    # ⚠️ extra="forbid" — 여기서는 **돈이 걸린다.** 이 스키마의 캐시 단가 3개는 default 가
    #    Decimal("0") 이라, 키 이름이 틀리면 값이 버려지고 0 이 들어간다. 그리고 이 엔드포인트는
    #    쓰기 전에 직전 단가 행을 close 하므로(services/model_service.py close_current_pricing),
    #    **새 ACTIVE 단가 행이 캐시 생성 비용을 0 으로 청구**하게 되고 되돌아갈 행도 없다.
    #    실측(적대적 검증 PROBE2): 0003 이전 이름 `cache_creation_price_per_1k_tokens` 로
    #    보내면 200, 기록된 행은 5m=0 · 1h=0, 직전 행은 이미 닫힘.
    #    ⇒ "extra 키는 무해한 오타" 라는 이전 판단은 이 스키마에서 반증됐다.
    #
    # le=MAX_PRICE_PER_1K: ModelCreateRequest 와 같은 이유(NUMERIC(10,6) 오버플로 → 500).
    # ⚠️ 이 스키마는 사용자 입력 외에 model_service.apply_price_sync 도 만들어 쓴다.
    #    AWS Price List 값이 비정상이면 DB 쓰기 전에 여기서 걸린다(더 이른 실패가 낫다).
    #    apply_price_sync 는 선언된 필드만 kwargs 로 넘기므로 forbid 의 영향을 받지 않는다.
    model_config = ConfigDict(extra="forbid")

    input_price_per_1k_tokens: Decimal = Field(ge=0, le=MAX_PRICE_PER_1K, decimal_places=6)
    output_price_per_1k_tokens: Decimal = Field(ge=0, le=MAX_PRICE_PER_1K, decimal_places=6)
    cache_creation_5m_price_per_1k_tokens: Decimal = Field(
        default=Decimal("0"), ge=0, le=MAX_PRICE_PER_1K, decimal_places=6
    )
    cache_creation_1h_price_per_1k_tokens: Decimal = Field(
        default=Decimal("0"), ge=0, le=MAX_PRICE_PER_1K, decimal_places=6
    )
    cache_read_price_per_1k_tokens: Decimal = Field(
        default=Decimal("0"), ge=0, le=MAX_PRICE_PER_1K, decimal_places=6
    )
    effective_from: datetime


class StatusPatchRequest(BaseModel):
    active: bool


# ── Responses ──


class ModelPricingResponse(BaseModel):
    input_price_per_1k_tokens: Decimal
    output_price_per_1k_tokens: Decimal
    cache_creation_5m_price_per_1k_tokens: Decimal = Decimal("0")
    cache_creation_1h_price_per_1k_tokens: Decimal = Decimal("0")
    cache_read_price_per_1k_tokens: Decimal = Decimal("0")
    effective_from: datetime
    effective_until: datetime | None = None


class ModelResponse(BaseModel):
    alias: str
    provider: ProviderEnum
    provider_model_id: str
    endpoint_url: str | None = None
    api_format: ApiFormatEnum
    status: str
    description: str | None = None
    display_name: str | None = None
    #: ``None`` = 제한 없음, ``[]`` = 허용 앱 없음, 목록 = 그 앱만. 화면이 이 세 상태를
    #: 구별해 보여줘야 한다 — ``[]`` 를 "제한 없음" 으로 렌더하면 운영자가 자기가 만든
    #: 전면 거부를 보지 못한다.
    allowed_clients: list[str] | None = None
    #: 스펙 정보 — LiteLLM 카탈로그 싱크로 채워진다. ``None`` = 미상(수동 커스텀 모델).
    context_window: int | None = None
    max_output_tokens: int | None = None
    current_pricing: ModelPricingResponse | None = None
    created_at: datetime
    updated_at: datetime


class ModelListResponse(BaseModel):
    items: list[ModelResponse]


# ── Price sync (AWS Price List API 동기화) ──


class PriceSyncDiff(BaseModel):
    """모델 1개의 현재 단가 vs AWS 공식 단가 diff(미리보기 전용, 쓰기 없음)."""

    alias: str
    provider_model_id: str
    matched: bool  # AWS Price List 에서 단가를 찾았나
    note: str | None = None  # 미매칭/주의 사유
    current: ModelPricingResponse | None = None  # DB 현재가(없을 수 있음)
    # AWS 에서 가져와 per-1k 정규화한 제안 단가(매칭 시)
    proposed_input_per_1k: Decimal | None = None
    proposed_output_per_1k: Decimal | None = None
    proposed_cache_5m_per_1k: Decimal | None = None
    proposed_cache_1h_per_1k: Decimal | None = None
    proposed_cache_read_per_1k: Decimal | None = None
    changed: bool = False  # 현재가와 제안가가 다른가
    #: 카탈로그 스펙(context_window/max_output_tokens)과 DB 값이 다른가.
    #: 단가 변경이 0이어도 이 플래그가 있으면 적용 대상에 포함되어야 한다 —
    #: 스펙 기록이 단가 변경에 묶여 있어 "변경 없음" 일 때 스펙이 영구히 비는
    #: 버그가 있었다.
    spec_changed: bool = False


class PriceSyncPreviewResponse(BaseModel):
    source: str = "aws_price_list_api"  # 출처 명시(IT 아님)
    region: str
    diffs: list[PriceSyncDiff]
    matched_count: int
    changed_count: int


class WireNameItem(BaseModel):
    """usage_logs 에 실제로 관측된 모델 이름 — alias 후보 확인용.

    ``registered`` = model_aliases 에 같은 이름이 있나. 미등록 이름은 곧
    필요한 alias 후보(클라이언트가 보내는데 아직 매칭 안 되는 이름).
    """

    name: str
    request_count: int
    last_seen_at: datetime | None = None
    registered: bool
    #: resolve 실패(404)로 관측된 횟수 — 게이트웨이가 Redis 에 집계.
    #: 0 이면 usage_logs 에만, >0 이면 미등록 이름으로 요청이 떨어진 것.
    rejected_count: int = 0


class WireNameListResponse(BaseModel):
    days: int
    items: list[WireNameItem]


class PriceSyncApplyRequest(BaseModel):
    """승인 후 적용할 alias 목록(명시 선택 — 자동 전체적용 금지)."""

    aliases: list[str] = Field(min_length=1)
    source: str = "aws"  # "aws" | "litellm"


class PriceSyncApplyResponse(BaseModel):
    applied: list[str]
    skipped: list[str]
    errors: list[str] = Field(default_factory=list)


# ── Team Allowed Models ──


class AllowedModelsSetRequest(BaseModel):
    """Replace-all semantics: provided list becomes the new full whitelist.

    빈 리스트 = 전체 허용 (엔트리 전부 삭제).
    """

    model_aliases: list[str] = Field(default_factory=list)


class AllowedModelsResponse(BaseModel):
    team_id: str
    model_aliases: list[str]
