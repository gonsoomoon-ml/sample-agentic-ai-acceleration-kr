# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Unit tests for per-model `thinking` normalization.

Ground truth measured against Tokyo Mantle on 2026-08-06:

    model                       thinking:{enabled}  thinking:{adaptive}  omitted
    anthropic.claude-opus-4-8   400                 200                  200
    anthropic.claude-opus-4-7   400                 200                  200
    anthropic.claude-haiku-4-5  200                 400                  200
"""

import pytest

from app.services.thinking_normalizer import (
    normalize_thinking,
    sanitize_bedrock_messages,
    sanitize_output_config,
)

OPUS_48 = "anthropic.claude-opus-4-8"
OPUS_47 = "anthropic.claude-opus-4-7"
HAIKU_45 = "anthropic.claude-haiku-4-5"
SONNET_55 = "anthropic.claude-sonnet-5-5"
OPUS_55 = "anthropic.claude-opus-5-5"


def _body(**kw):
    b = {"model": "x", "max_tokens": 2048, "messages": [{"role": "user", "content": "hi"}]}
    b.update(kw)
    return b


# --- adaptive-only family (Opus 4.7 / 4.8): enabled → adaptive -----------------


@pytest.mark.parametrize("model_id", [OPUS_47, OPUS_48])
def test_enabled_converted_to_adaptive(model_id):
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024}), model_id
    )
    assert b["thinking"] == {"type": "adaptive"}


def test_enabled_to_adaptive_drops_budget_tokens():
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 32000}), OPUS_48
    )
    assert "budget_tokens" not in b["thinking"]


def test_enabled_to_adaptive_preserves_display():
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024, "display": "summarized"}),
        OPUS_48,
    )
    assert b["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_adaptive_untouched_on_adaptive_family():
    b = normalize_thinking(_body(thinking={"type": "adaptive"}), OPUS_48)
    assert b["thinking"] == {"type": "adaptive"}


def test_output_config_kept_on_adaptive_family():
    b = normalize_thinking(
        _body(thinking={"type": "adaptive"}, output_config={"effort": "high"}), OPUS_48
    )
    assert b["output_config"] == {"effort": "high"}


# --- legacy-only family (Haiku 4.5): adaptive → enabled -----------------------


def test_adaptive_converted_to_enabled():
    b = normalize_thinking(_body(thinking={"type": "adaptive"}), HAIKU_45)
    assert b["thinking"]["type"] == "enabled"
    assert b["thinking"]["budget_tokens"] >= 1024


def test_adaptive_to_enabled_strips_output_config():
    b = normalize_thinking(
        _body(thinking={"type": "adaptive"}, output_config={"effort": "high"}), HAIKU_45
    )
    assert "output_config" not in b


def test_budget_stays_below_max_tokens():
    b = normalize_thinking(
        _body(max_tokens=2000, thinking={"type": "adaptive"}), HAIKU_45
    )
    assert b["thinking"]["budget_tokens"] < 2000


def test_thinking_dropped_when_max_tokens_too_small():
    b = normalize_thinking(_body(max_tokens=64, thinking={"type": "adaptive"}), HAIKU_45)
    assert "thinking" not in b
    assert "output_config" not in b


def test_enabled_untouched_on_legacy_family():
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024}), HAIKU_45
    )
    assert b["thinking"] == {"type": "enabled", "budget_tokens": 1024}


def test_legacy_enabled_budget_clamped_below_max_tokens():
    """강등 haiku + enabled thinking: Bedrock 은 budget_tokens < max_tokens 를
    요구한다 — sanitize 의 64000 클램프 뒤 budget 이 크면 그대로 400."""
    b = normalize_thinking(
        _body(max_tokens=64000,
              thinking={"type": "enabled", "budget_tokens": 100000}), HAIKU_45
    )
    assert b["thinking"] == {"type": "enabled", "budget_tokens": 63999}


def test_legacy_enabled_dropped_when_no_budget_room():
    """max_tokens 가 최소 budget(1024)+1 이하면 thinking 을 통째로 뺀다 —
    1024 미만 budget 은 Bedrock 이 거절하므로 enabled 로는 못 살린다."""
    b = normalize_thinking(
        _body(max_tokens=1024,
              thinking={"type": "enabled", "budget_tokens": 5000}), HAIKU_45
    )
    assert "thinking" not in b
    assert "output_config" not in b


# --- pass-through cases -------------------------------------------------------


def test_disabled_never_touched():
    for model_id in (OPUS_48, HAIKU_45):
        b = normalize_thinking(_body(thinking={"type": "disabled"}), model_id)
        assert b["thinking"] == {"type": "disabled"}


def test_no_thinking_field_is_noop():
    b = normalize_thinking(_body(), OPUS_48)
    assert "thinking" not in b


def test_unknown_model_left_untouched():
    original = {"type": "enabled", "budget_tokens": 1024}
    b = normalize_thinking(_body(thinking=dict(original)), "anthropic.some-future-model")
    assert b["thinking"] == original


def test_none_model_id_left_untouched():
    original = {"type": "enabled", "budget_tokens": 1024}
    b = normalize_thinking(_body(thinking=dict(original)), None)
    assert b["thinking"] == original


def test_geo_prefixed_model_id_resolves():
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024}),
        "us.anthropic.claude-opus-4-8",
    )
    assert b["thinking"] == {"type": "adaptive"}


def test_versioned_haiku_id_resolves():
    b = normalize_thinking(
        _body(thinking={"type": "adaptive"}), "anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    assert b["thinking"]["type"] == "enabled"


def test_malformed_thinking_does_not_raise():
    for bad in ("enabled", 42, [], None):
        b = normalize_thinking(_body(thinking=bad), OPUS_48)
        assert b["thinking"] == bad


def test_other_fields_preserved():
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024}, system="sys",
              tools=[{"name": "t", "input_schema": {}}]),
        OPUS_48,
    )
    assert b["system"] == "sys"
    assert b["tools"] == [{"name": "t", "input_schema": {}}]
    assert b["messages"] == [{"role": "user", "content": "hi"}]


# --- Haiku 5.5: adaptive-only, unlike Haiku 4.5 (US-19) ---------------------------
# Measured on Bedrock us-west-2 (us.anthropic.claude-haiku-5-5, 2026-10-10):
#   thinking:{enabled}  -> 400 "thinking.type.enabled" is not supported for this model.
#                          Use "thinking.type.adaptive" and "output_config.effort" ...
#   thinking:{adaptive} -> 200
# So "haiku" alone cannot decide the family; the version does.

from app.services.thinking_normalizer import _family_from_alias  # noqa: E402

HAIKU_55 = "anthropic.claude-haiku-5-5"


@pytest.mark.parametrize("model_id", [HAIKU_55, "us.anthropic.claude-haiku-5-5"])
def test_haiku_55_enabled_converted_to_adaptive(model_id):
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024}), model_id
    )
    assert b["thinking"] == {"type": "adaptive"}


# --- sanitize_output_config: legacy keeps format, drops effort ----------------


def test_legacy_drops_effort_keeps_format():
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    b = sanitize_output_config(
        _body(output_config={"effort": "low", "format": fmt}), HAIKU_45
    )
    assert b["output_config"] == {"format": fmt}


def test_legacy_output_config_only_effort_popped():
    b = sanitize_output_config(_body(output_config={"effort": "low"}), HAIKU_45)
    assert "output_config" not in b


def test_legacy_format_only_untouched():
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    b = sanitize_output_config(_body(output_config={"format": fmt}), HAIKU_45)
    assert b["output_config"] == {"format": fmt}


# --- sanitize_bedrock_messages -------------------------------------------------


def test_message_level_output_config_stripped():
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b", "output_config": {"effort": "low"}},
            {"role": "user", "content": "c"},
        ]),
        OPUS_48,
    )
    assert all("output_config" not in m for m in b["messages"])
    assert len(b["messages"]) == 3


def test_system_role_hoisted_to_top_level():
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "user", "content": "a"},
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "c"},
        ]),
        OPUS_48,
    )
    assert [m["role"] for m in b["messages"]] == ["user", "user"]
    assert "be brief" in str(b["system"])


def test_system_role_merged_with_existing_system_string():
    b = sanitize_bedrock_messages(
        _body(system="orig sys",
              messages=[
                  {"role": "system", "content": [{"type": "text", "text": "extra"}]},
                  {"role": "user", "content": "c"},
              ]),
        OPUS_48,
    )
    assert isinstance(b["system"], list)
    texts = [x.get("text") for x in b["system"]]
    assert texts == ["orig sys", "extra"]


def test_empty_system_message_removed_for_rejecting_model():
    """텍스트가 없는 system 메시지도 목록에서 빠진다 — 남기면 거절 모델에서 400."""
    for empty in ("", [], [{"type": "text", "text": ""}]):
        b = sanitize_bedrock_messages(
            _body(messages=[
                {"role": "user", "content": "a"},
                {"role": "system", "content": empty},
                {"role": "user", "content": "c"},
            ]),
            OPUS_48,
        )
        assert [m["role"] for m in b["messages"]] == ["user", "user"]
        assert "system" not in b


@pytest.mark.parametrize("model_id", [SONNET_55, OPUS_55])
def test_new_fields_kept_for_beta_body_models(model_id):
    """신형 필드를 받는 모델 + 필드를 여는 beta 전달 = output_config·system 유지."""
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "system", "content": [
            {"type": "tool_addition", "tool": {"name": "t"}},
            {"type": "text", "text": "ctx", "cache_control": {"type": "ephemeral"}},
        ], "output_config": {"effort": "high"}},
        {"role": "assistant", "content": "b", "output_config": {"effort": "low"}},
    ]
    b = sanitize_bedrock_messages(
        _body(messages=msgs, max_tokens=128000), model_id,
        forwarded_betas=["per-turn-control-2026-07-01", "inline-tools-2026-09-15"],
    )
    assert b["messages"] == msgs
    assert b["max_tokens"] == 128000


def test_new_fields_dropped_on_accepting_model_without_beta():
    """5.5라도 필드를 여는 beta 가 전달되지 않으면 신형 필드는 걷어낸다 — beta 를
    끈 요청(`BEDROCK_FORWARD_BETAS=""`)에선 5.5도 `Extra inputs` 400(리뷰 지적)."""
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "user", "content": "a"},
            {"role": "system", "content": [{"type": "text", "text": "ctx"}],
             "output_config": {"effort": "high"}},
        ]), SONNET_55,
        forwarded_betas=[],
    )
    assert [m["role"] for m in b["messages"]] == ["user"]
    assert "ctx" in str(b["system"])


def test_max_tokens_clamped_on_legacy():
    b = sanitize_bedrock_messages(_body(max_tokens=128000), HAIKU_45)
    assert b["max_tokens"] == 64000


def test_max_tokens_untouched_on_adaptive():
    b = sanitize_bedrock_messages(_body(max_tokens=128000), OPUS_48)
    assert b["max_tokens"] == 128000


def test_max_tokens_clamped_by_alias_family():
    b = sanitize_bedrock_messages(
        _body(max_tokens=128000), None, alias="claude-haiku-4-5-20251001"
    )
    assert b["max_tokens"] == 64000


def test_beta_body_fields_kept_by_alias():
    """alias 로만 판정해도 신형 계열 + beta 전달이면 필드를 유지한다."""
    msgs = [{"role": "user", "content": "a"},
            {"role": "assistant", "content": "b", "output_config": {"effort": "low"}}]
    b = sanitize_bedrock_messages(
        _body(messages=list(msgs)), None, alias="claude-sonnet-5-5-latest",
        forwarded_betas=["per-turn-control-2026-07-01"],
    )
    assert b["messages"] == msgs


def test_forwarded_beta_strips_fields_on_rejecting_model():
    """필드를 여는 beta 가 전달돼도, 필드를 거절하는 모델이면 정규화한다.

    beta 는 모든 모델이 받지만 신형 필드는 모델이 받아야 한다 — 리뷰 실측에서
    opus-5·sonnet-5·4.x·haiku-4-5 는 beta 가 붙어도 400. 필드 유지는
    수용 실측 모델(opus-5-5·sonnet-5-5) AND beta 전달의 AND 조건이다.
    """
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "user", "content": "a"},
            {"role": "system", "content": [{"type": "text", "text": "ctx"}],
             "output_config": {"effort": "high"}},
        ]), OPUS_48,
        forwarded_betas=["per-turn-control-2026-07-01", "inline-tools-2026-09-15"],
    )
    assert [m["role"] for m in b["messages"]] == ["user"]
    assert "ctx" in str(b["system"])


def test_beta_without_opening_beta_still_strips():
    """무관한 beta 만 전달되면 신형 필드는 정규화된다."""
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "system", "content": "be brief"},
            {"role": "assistant", "content": "b", "output_config": {"effort": "low"}},
        ]),
        OPUS_48,
        forwarded_betas=["dangerous-tool-use-2026-09-03"],
    )
    assert all("output_config" not in m for m in b["messages"])
    assert [m["role"] for m in b["messages"]] == ["assistant"]
    assert "be brief" in str(b["system"])


def test_legacy_strips_even_with_beta():
    """legacy 계열은 beta 가 전달돼도 필드를 받지 못한다 — 강등 400 방지."""
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "system", "content": "be brief"},
            {"role": "assistant", "content": "b", "output_config": {"effort": "low"}},
        ]),
        HAIKU_45,
        forwarded_betas=["per-turn-control-2026-07-01", "inline-tools-2026-09-15"],
    )
    assert all("output_config" not in m for m in b["messages"])
    assert "be brief" in str(b["system"])


def test_bedrock_messages_noop_on_clean_body():
    b = sanitize_bedrock_messages(_body(system="s", max_tokens=100), OPUS_48)
    assert b["system"] == "s"
    assert b["max_tokens"] == 100
    assert b["messages"] == [{"role": "user", "content": "hi"}]


# --- normalize_thinking × output_config 일관성 + 추가 geo 접두사 ----------------

def test_enabled_on_legacy_keeps_format_drops_effort():
    """legacy(haiku-4-5) + thinking enabled + output_config — sanitize_output_config
    와 같은 규칙: effort 만 떨구고 format(구조화 출력)은 유지한다."""
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024},
              output_config={"effort": "low", "format": fmt}),
        HAIKU_45,
    )
    assert b["output_config"] == {"format": fmt}
    assert b["thinking"]["type"] == "enabled"


def test_adaptive_to_enabled_on_legacy_keeps_format():
    """adaptive→enabled 변환 경로에서도 format 은 살아야 한다 — 통째로 버리면
    thinking 을 쓰는 구조화 출력 요청이 조용히 깨진다."""
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    b = normalize_thinking(
        _body(thinking={"type": "adaptive"},
              output_config={"effort": "low", "format": fmt}),
        HAIKU_45,
    )
    assert b["thinking"]["type"] == "enabled"
    assert b["output_config"] == {"format": fmt}


def test_thinking_dropped_no_budget_keeps_format():
    """예산 부족으로 thinking 을 지우는 경로에서도 format 은 유지한다."""
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    b = normalize_thinking(
        _body(max_tokens=64, thinking={"type": "adaptive"},
              output_config={"effort": "low", "format": fmt}),
        HAIKU_45,
    )
    assert "thinking" not in b
    assert b["output_config"] == {"format": fmt}


@pytest.mark.parametrize("geo", ["jp.", "au.", "ca.", "us-gov.", "sa.", "eu.", "apac."])
def test_all_geo_prefixed_model_ids_resolve(geo):
    """cross-region inference profile 접두사 전부 family 판정에 걸려야 한다 —
    빠진 리전은 family=None 패스스루라 enabled 요청이 adaptive 전용 모델에 400."""
    b = normalize_thinking(
        _body(thinking={"type": "enabled", "budget_tokens": 1024}),
        f"{geo}anthropic.claude-opus-4-8",
    )
    assert b["thinking"] == {"type": "adaptive"}


def test_haiku_55_keeps_adaptive_and_output_config():
    b = normalize_thinking(
        _body(thinking={"type": "adaptive"}, output_config={"effort": "low"}), HAIKU_55
    )
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": "low"}


@pytest.mark.parametrize("alias,family", [
    ("claude-haiku-5-5", "adaptive"),
    ("team-haiku-5.5-fast", "adaptive"),
    ("claude-haiku-4-5", "legacy"),
    ("claude-haiku-4-5-20251001", "legacy"),
    ("claudecode-haiku-4.5", "legacy"),
    ("haiku45", "legacy"),
    ("cowork-haiku", "legacy"),
    ("claude-haiku-20251001", "legacy"),
])
def test_alias_family_reads_the_haiku_version(alias, family):
    assert _family_from_alias(alias) == family


def test_downgrade_to_haiku_55_alias_keeps_adaptive():
    # The budget-downgrade layer runs before model resolution: no provider id, alias only.
    b = normalize_thinking(
        _body(thinking={"type": "adaptive"}, output_config={"effort": "low"}),
        None, alias="claude-haiku-5-5",
    )
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": "low"}


def test_unrecognised_provider_id_is_not_guessed_from_the_alias():
    # A provider id the lists do not know means "leave the body alone" — the alias
    # guess is only for callers that have no provider id at all.
    body = _body(thinking={"type": "adaptive"}, output_config={"effort": "low"})
    b = normalize_thinking(body, "anthropic.claude-haiku-9-9", alias="team-haiku-cheap")
    assert b["thinking"] == {"type": "adaptive"}
    assert b["output_config"] == {"effort": "low"}


def test_system_hoist_preserves_cache_control_blocks():
    """cache_control 이 붙은 system 메시지를 hoist 해도 블록이 살아난다 —
    텍스트 추출 방식이면 프롬프트 캐시 표시가 통째로 사라진다."""
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "system",
             "content": [{"type": "text", "text": "policy", "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": "hi"},
        ]),
        OPUS_48,
    )
    assert b["system"] == [
        {"type": "text", "text": "policy", "cache_control": {"type": "ephemeral"}}
    ]
    assert [m["role"] for m in b["messages"]] == ["user"]


def test_system_hoist_skips_non_system_block_types():
    """tool_use 등 system 자리에 둘 수 없는 블록은 빠지고 text/guardContent 만 간다."""
    b = sanitize_bedrock_messages(
        _body(messages=[
            {"role": "system",
             "content": [{"type": "text", "text": "ok"},
                         {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
            {"role": "user", "content": "hi"},
        ]),
        OPUS_48,
    )
    assert b["system"] == "ok"  # 단일 text 블록은 문자열로 합쳐진다(기존 동작 유지)
