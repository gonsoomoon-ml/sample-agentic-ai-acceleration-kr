# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Normalize the `thinking` field per target model for Bedrock/Mantle path.

Anthropic changed the extended-thinking API between model generations, and the
two shapes are mutually exclusive across models:

    model                       thinking:{type:"enabled"}   thinking:{type:"adaptive"}
    anthropic.claude-opus-4-8   400 (not supported)         200
    anthropic.claude-opus-4-7   400 (not supported)         200
    anthropic.claude-haiku-4-5  200                         400 (not supported)

Opus 4.7+ dropped the fixed-budget form (`enabled` + `budget_tokens`) in favour
of `adaptive` + `output_config.effort`; Haiku 4.5 predates adaptive thinking and
only accepts the old form. The gateway normalizes here since it's the only layer
that knows the target model.

Unknown models pass through untouched: a newly-added model behaves exactly as it
does today rather than inheriting a guessed rule. The shim never raises — worst
case is the provider's own error, never one we introduced.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Models that require `thinking:{type:"adaptive"}` and reject the legacy form.
_ADAPTIVE_ONLY_PREFIXES: tuple[str, ...] = (
    "anthropic.claude-opus-4-7",
    "anthropic.claude-opus-4-8",
    "anthropic.claude-opus-5",
    "anthropic.claude-sonnet-5",
    "anthropic.claude-fable-5",
    "anthropic.claude-mythos-5",
)

# Models that accept only the legacy `enabled` form and reject `adaptive`.
_LEGACY_ONLY_PREFIXES: tuple[str, ...] = ("anthropic.claude-haiku-4-5",)

_DEFAULT_BUDGET_TOKENS = 4096
_MIN_BUDGET_TOKENS = 1024


def _family(provider_model_id: str | None) -> str | None:
    """Return "adaptive", "legacy", or None (unknown — leave request alone)."""
    if not provider_model_id:
        return None
    mid = provider_model_id.lower()
    # AWS cross-region inference profile 접두사 — 빠진 리전은 family=None 패스스루라
    # 같은 400 을 맞는다. 알려진 profile 리전 전부를 벗긴다.
    for geo in (
        "us.", "eu.", "apac.", "global.", "jp.", "au.", "ca.", "sa.",
        "il.", "mx.", "us-gov.",
    ):
        if mid.startswith(geo):
            mid = mid[len(geo) :]
            break
    if mid.startswith(_ADAPTIVE_ONLY_PREFIXES):
        return "adaptive"
    if mid.startswith(_LEGACY_ONLY_PREFIXES):
        return "legacy"
    return None


def _family_from_alias(alias: str | None) -> str | None:
    """alias 문자열로 계열을 추정한다. ``provider_model_id`` 를 모르는 계층용.

    ⚠️ 왜 필요한가: 예산 강등 미들웨어는 모델 **해석 전**에 동작하므로 alias 만 안다.
       그 계층이 ``startswith("claude-haiku-4-5")`` 로 판정하고 있었는데, 운영자가 만든
       alias(예: ``team-haiku-cheap``)는 그 접두사로 시작하지 않아 판정을 빠져나갔다 —
       강등 대상이 haiku 인데 thinking 이 그대로 실려 400 이 됐다.

    ⚠️ 접두사가 아니라 **부분 문자열**로 본다. alias 는 운영자가 자유롭게 짓는 이름이라
       접두사 규약을 강제할 수 없다. 대가는 오탐 가능성이지만, 두 변환 모두 상류가
       거부하는 형태를 받아들이는 형태로 바꾸는 것이라 오탐의 비용이 낮다.
    """
    if not alias:
        return None
    a = alias.lower()
    if "haiku" in a:
        return "legacy"
    for token in ("opus-4-7", "opus-4-8", "opus-5", "sonnet-5", "fable-5", "mythos-5"):
        if token in a:
            return "adaptive"
    return None


def _resolve_family(provider_model_id: str | None, alias: str | None) -> str | None:
    """계열 판정을 한 곳으로 모은다 — 모델 ID 를 알면 그것만으로, 모를 때만 alias 추정.

    ⚠️ ``provider_model_id`` 가 있는데 알려진 접두사에 안 맞으면 alias 를 보지
       않는다 — alias 는 자유 이름이라(``team-haiku-cheap``) 실제 모델과 다른
       계열을 가리킬 수 있고, 미상 모델에 정규화를 거는 것보다 그대로 통과가 낫다.
    """
    if provider_model_id:
        return _family(provider_model_id)
    return _family_from_alias(alias)


#: ``messages[]`` 안의 신형 필드(메시지 단위 ``output_config`` · ``role=system`` ·
#: ``tool_addition`` 등)를 그대로 받는 모델을 가리키는 토큰. per-turn-control ·
#: mid-conversation-system · inline-tools beta 가 Bedrock 본문으로 넘어가므로 이 계열은
#: 필드를 손대지 않는다 — 지우면 정의한 도구·턴별 effort·캐시 표시가 함께 사라진다.
#: 나머지 계열(opus-5 · sonnet-5 · 4.x · haiku-4-5)은 이 필드들을 거절한다(2026-10-09
#: 리뷰 재현: opus-5-5 본문을 정규화하면 tool_addition·cache_control·턴별 effort 손실).
_BETA_BODY_FIELD_TOKENS: tuple[str, ...] = ("opus-5-5", "sonnet-5-5")


def _accepts_beta_body_fields(provider_model_id: str | None, alias: str | None) -> bool:
    """대상 모델이 ``messages[]`` 안의 신형 필드를 받으면 True — 그 모델엔 정규화를 걸지 않는다.

    ``provider_model_id`` 를 모르는 계층(예산 강등 미들웨어 등)은 alias 로 판정한다.
    alias 는 자유 형식이라 부분 문자열로 본다 — ``"sonnet-5"`` 가 ``"sonnet-5-5"`` 를
    포함하는 문제가 없도록 ``"-5-5"`` 까지 붙은 토큰만 본다. 모델 ID 가 있으면
    그것만 본다 — alias(``my-sonnet-5-5-pool`` → 실제로는 4.x)가 신형 필드를
    받지 못하는 모델 요청을 통과시키는 오탐을 막는다.
    """
    cand = provider_model_id or alias
    return bool(cand) and any(t in cand.lower() for t in _BETA_BODY_FIELD_TOKENS)


#: ``messages[].output_config`` 를 여는 anthropic-beta 접두사(짝 필드: 메시지 단위
#: output_config — 턴별 effort).
_OUTPUT_CONFIG_BETA = "per-turn-control"
#: ``messages[]`` 안의 ``role=system`` 이 의미를 갖는 beta 접두사 — inline-tools 는
#: system 메시지 안의 ``tool_addition``/``tool_removal`` 블록을, mid-conversation-*
#: 계열은 메시지 단위 system 블록 자체를 연다.
_SYSTEM_MESSAGE_BETAS = ("inline-tools", "mid-conversation-system", "mid-conversation-tool-changes")


def _beta_opens(forwarded_betas: Iterable[str], *prefixes: str) -> bool:
    return any(b.startswith(p) for b in forwarded_betas for p in prefixes)


def normalize_thinking(
    body: dict[str, Any],
    provider_model_id: str | None,
    *,
    alias: str | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    """Return `body` with `thinking` adjusted to what `provider_model_id` accepts.

    Mutates and returns the same dict (callers build a throwaway body dict).
    Never raises: any unexpected shape is passed through untouched.
    """
    try:
        thinking = body.get("thinking")
        if not isinstance(thinking, dict):
            return body

        t_type = thinking.get("type")
        if t_type not in ("enabled", "adaptive"):
            return body

        family = _resolve_family(provider_model_id, alias)
        if family is None:
            return body

        # ⚠️ legacy 계열(haiku-4-5)은 `output_config.effort` 만 거절하고 `format`
        #    (구조화 출력)은 받는다 — sanitize_output_config 의 legacy 분기와 같은
        #    규칙으로 effort 만 떨군다. 통째로 버리면 thinking 을 쓰는 요청의
        #    구조화 출력이 조용히 깨진다(2026-09-28 실측: format→200, effort→400).
        if family == "legacy" and "output_config" in body:
            _drop_legacy_output_config_fields(body)
            logger.info(
                "thinking_normalized",
                request_id=request_id,
                provider_model_id=provider_model_id,
                direction="output_config_effort_dropped_legacy_family",
            )

        if family == "adaptive" and t_type == "enabled":
            new_thinking: dict[str, Any] = {"type": "adaptive"}
            if "display" in thinking:
                new_thinking["display"] = thinking["display"]
            body["thinking"] = new_thinking
            logger.info(
                "thinking_normalized",
                request_id=request_id,
                provider_model_id=provider_model_id,
                direction="enabled_to_adaptive",
                dropped_budget_tokens=thinking.get("budget_tokens"),
            )
            return body

        if family == "legacy" and t_type == "adaptive":
            max_tokens = body.get("max_tokens")
            budget = _DEFAULT_BUDGET_TOKENS
            if isinstance(max_tokens, int):
                budget = min(budget, max_tokens - 1)
            if budget < _MIN_BUDGET_TOKENS:
                body.pop("thinking", None)
                _drop_legacy_output_config_fields(body)
                logger.info(
                    "thinking_normalized",
                    request_id=request_id,
                    provider_model_id=provider_model_id,
                    direction="adaptive_dropped_no_budget_room",
                    max_tokens=max_tokens,
                )
                return body
            new_thinking = {"type": "enabled", "budget_tokens": budget}
            if "display" in thinking:
                new_thinking["display"] = thinking["display"]
            body["thinking"] = new_thinking
            had_output_config = "output_config" in body
            _drop_legacy_output_config_fields(body)
            logger.info(
                "thinking_normalized",
                request_id=request_id,
                provider_model_id=provider_model_id,
                direction="adaptive_to_enabled",
                budget_tokens=budget,
                had_output_config=had_output_config,
            )
            return body

        return body
    except Exception:
        logger.warning(
            "thinking_normalize_failed",
            request_id=request_id,
            provider_model_id=provider_model_id,
            exc_info=True,
        )
        return body


#: Bedrock 의 Anthropic Messages 가 `output_config` 안에서 받는 키 — **adaptive 계열**
#: (opus-4-7/4-8·opus-5·sonnet-5 …) 기준. 2026-09-16 US 실측:
#:   opus-5 · sonnet-5 + output_config.format
#:       → 400 "output_config.format: Extra inputs are not permitted"
#:   haiku-4-5           + output_config.format → 200, 스키마대로 응답(구조화 출력 동작)
#: 그래서 legacy 계열(haiku)에서는 손대지 않고, adaptive/미상 계열에서만 `format` 을 걷어낸다.
_BEDROCK_OUTPUT_CONFIG_KEYS = frozenset({"effort"})


def _drop_legacy_output_config_fields(body: dict[str, Any]) -> None:
    """legacy 계열(haiku-4-5)용 `output_config` 정리 — 거절하는 `effort` 만 떨군다.

    haiku-4-5 는 `format`(구조화 출력)을 받지만 `effort` 는 400 이라(2026-09-28
    실측) `sanitize_output_config` 의 legacy 분기와 같은 규칙을 둔다 — 여기서
    통째로 버리면 normalize_thinking 만 돌리는 경로(예산 강등 미들웨어)에서
    thinking + format 요청의 구조화 출력이 조용히 깨진다. 비어 버리면 통째로 뺀다.
    """
    oc = body.get("output_config")
    if not isinstance(oc, dict):
        return
    kept = {k: v for k, v in oc.items() if k != "effort"}
    if kept:
        body["output_config"] = kept
    else:
        body.pop("output_config", None)


def sanitize_output_config(
    body: dict[str, Any],
    provider_model_id: str | None = None,
    *,
    alias: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Bedrock 이 받지 않는 `output_config` 하위 키를 걷어낸다. 같은 dict 를 고쳐서 돌려준다.

    왜 필요한가: Cowork 는 `/v1/messages?beta=true` 로 `output_config: {"format": {...}}`
    (구조화 출력)를 보낸다. `output_config` 자체는 `effort` 때문에 허용 필드에 있어서
    그대로 Bedrock 까지 가는데, adaptive 계열 모델은 `format` 을 모른다 → 400 이 그대로
    클라이언트에 돌아가고 Cowork 는 재시도만 반복한다(2026-09-16 US 실측: 3종목 주가 비교
    요청이 400 ×4 뒤 실패). `format` 을 빼면 모델은 프롬프트로 형식을 맞추고 요청은
    성공한다 — 일부 형식 강제를 잃는 것이 400 보다 낫다. 비어 버린 `output_config` 는
    통째로 뺀다. legacy 계열(haiku)은 `format` 을 실제로 지원하므로 그대로 둔다. 계열을
    모르면 보수적으로 걷어낸다(400 이 기능 손실보다 나쁘다). Never raises.
    """
    try:
        oc = body.get("output_config")
        if oc is None:
            return body
        if not isinstance(oc, dict):
            body.pop("output_config", None)
            logger.info("output_config_sanitized", request_id=request_id, dropped=["<non-object>"])
            return body
        family = _resolve_family(provider_model_id, alias)
        if family == "legacy":
            # haiku-4-5는 output_config.format(구조화 출력)은 받지만 effort 는 거절한다
            # ("This model does not support the effort parameter" — 2026-09-28 실측).
            # effort 만 떨구고 format 등 나머지는 둔다 — 통째로 버리면 구조화 출력이 깨진다.
            if "effort" not in oc:
                return body
            kept_legacy = {k: v for k, v in oc.items() if k != "effort"}
            if kept_legacy:
                body["output_config"] = kept_legacy
            else:
                body.pop("output_config", None)
            logger.info(
                "output_config_sanitized",
                request_id=request_id,
                family=family,
                dropped=["effort"],
            )
            return body
        dropped = [k for k in oc if k not in _BEDROCK_OUTPUT_CONFIG_KEYS]
        if not dropped:
            return body
        kept = {k: v for k, v in oc.items() if k in _BEDROCK_OUTPUT_CONFIG_KEYS}
        if kept:
            body["output_config"] = kept
        else:
            body.pop("output_config", None)
        logger.info(
            "output_config_sanitized",
            request_id=request_id,
            provider_model_id=provider_model_id,
            family=family,
            dropped=sorted(dropped),
        )
        return body
    except Exception:
        logger.warning("output_config_sanitize_failed", request_id=request_id)
        return body


#: legacy 계열(haiku-4-5)의 출력 상한 — 2026-09-28 ap-south-1 실측.
#: opus-4-6/4-8 · sonnet-4-6/5 · opus-5/5-5 · sonnet-5-5 는 128000 까지 받는다 — 클램프 대상 아님.
_LEGACY_MAX_OUTPUT_TOKENS = 64000


def _system_blocks(content: Any) -> list[dict]:
    """system 메시지 ``content`` 를 최상위 ``system`` 에 실을 수 있는 블록으로 바꾼다.

    텍스트만 추출하지 않고 블록을 보존한다 — ``cache_control`` 이 붙은 블록까지
    날리면 프롬프트 캐시가 깨지고, ``guardContent`` 등 Bedrock 이 system 에서
    받는 비텍스트 블록도 그대로 간다. 빈 text 블록은 뺀다(Bedrock 이 거절).
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and not b.get("text"):
            continue
        if b.get("type") in ("text", "guardContent"):
            out.append(b)
    return out


def _merge_system(existing: Any, hoisted: list[dict]) -> Any:
    """hoist된 system 블록들을 최상위 ``system`` 필드에 합친다.

    빈 기존 값(``""``·``[]``)은 없는 것으로 본다 — ``""` 을 text 블록으로
    옮기면 ``{"type":"text","text":""}`` 가 들어가는데 Bedrock 은 빈 text 블록을
    거절한다(2026-10-09 ap-south-1 재현: messages.N.text empty input).
    """
    if existing is None or existing == "" or existing == []:
        if len(hoisted) == 1 and set(hoisted[0]) <= {"type", "text"}:
            return hoisted[0]["text"]
        return hoisted
    if isinstance(existing, str):
        return [{"type": "text", "text": existing}, *hoisted]
    if isinstance(existing, list):
        return [*existing, *hoisted]
    return hoisted


def sanitize_bedrock_messages(
    body: dict[str, Any],
    provider_model_id: str | None = None,
    *,
    alias: str | None = None,
    request_id: str | None = None,
    forwarded_betas: Iterable[str] = (),
) -> dict[str, Any]:
    """Bedrock Messages 와이어가 거부하는 본문 요소를 정리한다. 같은 dict 를 고쳐 돌려준다.

    **그 필드를 받지 않는 요청에만** 아래 두 변환을 한다(2026-09-28 ap-south-1 실측
    거절 규칙). 필드는 둘 경우가 그 뿐이다 — 신형 필드를 네이티브로 받는 모델
    (``_accepts_beta_body_fields`` 판정: opus-5-5 · sonnet-5-5), 또는 그 필드를 여는
    anthropic-beta 가 실제로 전달될 때(``forwarded_betas``). 지우면 ``tool_addition``
    (도구 정의) · ``cache_control`` · 턴별 effort 가 통째로 사라진다(리뷰 재현).
    legacy 계열은 beta 가 붙어도 필드를 받지 못하므로 항상 정규화한다 — 강등된
    하위 모델에서 그대로 400 이 나는 것을 막는다.

      1. ``messages[].output_config`` — 거절 모델은 메시지 단위 필드를 받지 않는다
         ("messages.N.output_config: Extra inputs are not permitted"). Claude Code
         v2.1.x 가 대화 이력 메시지에 싣는다. per-turn-control beta 가 전달되면 둔다.
      2. ``messages[].role == "system"`` — 거절 모델은 받지 않는다 ("use the
         top-level 'system' parameter"). 텍스트를 최상위 ``system`` 으로 옮기고
         메시지에서 뺀다. 내용이 없는 system 메시지(``""``·``[]``)도 목록에서
         제거한다 — 남겨 두면 거절 모델에서 그대로 400 이 난다. 메시지가 하나라도
         빠지면 ``body["messages"]`` 를 항상 다시 쓴다. inline-tools ·
         mid-conversation-* beta 가 전달되면 메시지를 그대로 둔다.

    두 변환은 계열을 몰라도(미상 모델) 적용한다 — 신형 필드를 몰라서 400 나는 것보다
    보수적으로 걷어내는 편이 낫다. 세 번째 변환은 계열이 legacy 일 때만:

      3. legacy 계열(haiku-4-5)의 ``max_tokens`` — 상한 64000. 초과분만 클램프한다.

    예산 강등으로 생긴 조합(상위 모델용 파라미터 + 하위 모델)과 Claude Code 의 신형
    요청 필드가 구형 모델에서 400 을 내는 것을 막는다. Never raises.
    """
    try:
        msgs = body.get("messages")
        family = _resolve_family(provider_model_id, alias)
        if _accepts_beta_body_fields(provider_model_id, alias):
            keep_oc = keep_sys = True
        elif family == "legacy":
            keep_oc = keep_sys = False
        else:
            keep_oc = _beta_opens(forwarded_betas, _OUTPUT_CONFIG_BETA)
            keep_sys = _beta_opens(forwarded_betas, *_SYSTEM_MESSAGE_BETAS)

        hoisted: list[str] = []
        if isinstance(msgs, list) and not (keep_oc and keep_sys):
            kept_msgs: list[Any] = []
            oc_dropped = 0
            system_dropped = 0
            for m in msgs:
                if not isinstance(m, dict):
                    kept_msgs.append(m)
                    continue
                if not keep_oc and "output_config" in m:
                    m = {k: v for k, v in m.items() if k != "output_config"}
                    oc_dropped += 1
                if not keep_sys and m.get("role") == "system":
                    # 텍스트 추출이 아니라 블록 보존 — cache_control·guardContent 가
                    # 붙은 system 메시지를 hoist 해도 캐시 표시가 살아있다.
                    blocks = _system_blocks(m.get("content"))
                    hoisted.extend(blocks)
                    if not blocks:
                        system_dropped += 1  # 호이스트할 텍스트가 없어 완전히 유실
                    continue
                kept_msgs.append(m)
            if oc_dropped or system_dropped or hoisted:
                body["messages"] = kept_msgs
                logger.info(
                    "bedrock_messages_sanitized",
                    request_id=request_id,
                    message_output_config_dropped=oc_dropped,
                    system_blocks_hoisted=len(hoisted),
                    system_messages_dropped=system_dropped,
                )
        if hoisted:
            body["system"] = _merge_system(body.get("system"), hoisted)

        mt = body.get("max_tokens")
        if family == "legacy" and isinstance(mt, int) and mt > _LEGACY_MAX_OUTPUT_TOKENS:
            body["max_tokens"] = _LEGACY_MAX_OUTPUT_TOKENS
            logger.info(
                "max_tokens_clamped",
                request_id=request_id,
                max_tokens=mt,
                clamped_to=_LEGACY_MAX_OUTPUT_TOKENS,
            )
        return body
    except Exception:
        logger.warning("bedrock_messages_sanitize_failed", request_id=request_id)
        return body
