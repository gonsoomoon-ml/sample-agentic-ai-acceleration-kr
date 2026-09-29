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
    for geo in ("us.", "eu.", "apac.", "global."):
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

        family = _family(provider_model_id) or _family_from_alias(alias)
        if family is None:
            return body

        # ⚠️ legacy 계열은 `output_config` 자체를 받지 않는다. `thinking` 이 이미
        #    `enabled` 라 아래 변환이 필요 없는 경우에도 이건 떨궈야 한다 — 그러지 않으면
        #    `output_config` 를 허용 필드에 넣은 순간 haiku 가 400 을 내기 시작한다.
        if family == "legacy" and "output_config" in body:
            body.pop("output_config", None)
            logger.info(
                "thinking_normalized",
                request_id=request_id,
                provider_model_id=provider_model_id,
                direction="output_config_dropped_legacy_family",
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
                body.pop("output_config", None)
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
            body.pop("output_config", None)
            logger.info(
                "thinking_normalized",
                request_id=request_id,
                provider_model_id=provider_model_id,
                direction="adaptive_to_enabled",
                budget_tokens=budget,
                dropped_output_config=had_output_config,
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
        family = _family(provider_model_id) or _family_from_alias(alias)
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
#: opus-4-6/4-8 · sonnet-4-6/5 · opus-5/5-5 는 128000 까지 받는다 — 클램프 대상 아님.
_LEGACY_MAX_OUTPUT_TOKENS = 64000


def _content_text(content: Any) -> str:
    """message ``content`` 에서 텍스트만 추출한다 (str 또는 content-block 리스트)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(t for t in parts if t)
    return ""


def _merge_system(existing: Any, hoisted: list[str]) -> Any:
    """hoist된 system 메시지들을 최상위 ``system`` 필드에 합친다."""
    blocks = [{"type": "text", "text": t} for t in hoisted]
    if existing is None:
        return hoisted[0] if len(hoisted) == 1 else blocks
    if isinstance(existing, str):
        return [{"type": "text", "text": existing}, *blocks]
    if isinstance(existing, list):
        return [*existing, *blocks]
    return blocks


def sanitize_bedrock_messages(
    body: dict[str, Any],
    provider_model_id: str | None = None,
    *,
    alias: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Bedrock Messages 와이어가 거부하는 본문 요소를 정리한다. 같은 dict 를 고쳐 돌려준다.

    세 변환은 전부 2026-09-28 ap-south-1 실측 거절 규칙이다:

      1. ``messages[].output_config`` — 어떤 모델도 메시지 단위 필드로 받지 않는다
         ("messages.N.output_config: Extra inputs are not permitted"). Claude Code
         v2.1.x 가 대화 이력 메시지에 싣는다. 무조건 제거.
      2. ``messages[].role == "system"`` — 어떤 모델도 받지 않는다 ("use the
         top-level 'system' parameter" / "role 'system' is not supported"). 내용을
         최상위 ``system`` 으로 옮기고 메시지에서 뺀다. 빼서 생기는 연속 동일 role 은
         Bedrock 이 허용한다(실측).
      3. legacy 계열(haiku-4-5)의 ``max_tokens`` — 상한 64000. 초과분만 클램프한다.

    예산 강등으로 생긴 조합(상위 모델용 파라미터 + 하위 모델)과 Claude Code 의 신형
    요청 필드가 구형 모델에서 400 을 내는 것을 막는다. Never raises.
    """
    try:
        msgs = body.get("messages")
        hoisted: list[str] = []
        if isinstance(msgs, list):
            kept_msgs: list[Any] = []
            oc_dropped = 0
            for m in msgs:
                if not isinstance(m, dict):
                    kept_msgs.append(m)
                    continue
                if "output_config" in m:
                    m = {k: v for k, v in m.items() if k != "output_config"}
                    oc_dropped += 1
                if m.get("role") == "system":
                    text = _content_text(m.get("content"))
                    if text:
                        hoisted.append(text)
                    continue
                kept_msgs.append(m)
            if oc_dropped or hoisted:
                body["messages"] = kept_msgs
                logger.info(
                    "bedrock_messages_sanitized",
                    request_id=request_id,
                    message_output_config_dropped=oc_dropped,
                    system_messages_hoisted=len(hoisted),
                )
        if hoisted:
            body["system"] = _merge_system(body.get("system"), hoisted)

        family = _family(provider_model_id) or _family_from_alias(alias)
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
