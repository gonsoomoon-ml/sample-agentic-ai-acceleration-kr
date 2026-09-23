# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Usage display formatter and severity determination (BR-SL-02, BR-SL-03)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from statusline.usage_client import UsageInfo

# Model alias → short display name (prefix-match via _short_name())
_MODEL_SHORT = {
    "opus": "Opus",
    "sonnet": "Sonnet",
    "haiku": "Haiku",
}


def _short_name(alias: str) -> str:
    """Extract display name from model alias (e.g. 'claudecode-opus-4.8' → 'Opus')."""
    low = alias.lower()
    for key, name in _MODEL_SHORT.items():
        if key in low:
            return name
    return alias.split(".")[-1] if "." in alias else alias.split("-")[-1]


class Severity(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    OFFLINE = "offline"


@dataclass
class StatuslineState:
    current: Optional[UsageInfo] = None
    severity: Severity = Severity.OFFLINE
    is_online: bool = False
    last_success_at: Optional[datetime] = None
    error_count: int = 0


# Fallback warning band, used only when the gateway does not tell us the operator's
# ladder (older gateway build, or a budget-config cache miss). CRITICAL is pinned to
# 100 regardless: 100% is budget exhaustion — the point where a hard_block policy
# actually denies requests — which is a different fact from the alert ladder.
_DEFAULT_WARNING_PCT = 80.0
_CRITICAL_PCT = 100.0


def _warning_pct(thresholds: Optional[Sequence[int]]) -> float:
    """Lowest configured alert threshold, or the 80% fallback.

    min() and not max(): the gateway activates THROTTLE with
    ``any(pct >= t for t in thresholds)`` (budget_service.check_budget), so the
    effective trigger point IS the lowest rung, and that is also where the first
    budget alarm mail goes out.

    An empty list must NOT be read as "a ladder with no rungs" — that is how the
    indicator goes silent. The gateway already promises to send ``null`` rather
    than ``[]`` for "unknown", but a truthy check alone would not catch ``[]``, so
    the emptiness is re-checked here (this client is shipped independently of the
    gateway and routinely runs against an older or newer one).
    """
    if not thresholds:
        return _DEFAULT_WARNING_PCT
    usable = [
        float(t)
        for t in thresholds
        if isinstance(t, (int, float)) and not isinstance(t, bool) and 1 <= t <= 100
    ]
    if not usable:
        return _DEFAULT_WARNING_PCT
    return min(usable)


def determine_severity(
    percentage: float,
    is_online: bool,
    thresholds: Optional[Sequence[int]] = None,
) -> Severity:
    """Map spend percentage onto a display band.

    ``thresholds`` is ``budget.alert_thresholds`` from /v1/usage/me — the operator's
    configured alert/THROTTLE ladder. It is keyword-optional so every existing
    caller keeps working and so a gateway that does not send the field yet degrades
    to the historical 80/100 bands. Without it the indicator contradicted
    enforcement: an operator setting [70] made the gateway throttle and alarm at
    70% while this line stayed green until 80%.
    """
    if not is_online:
        return Severity.OFFLINE
    if percentage >= _CRITICAL_PCT:
        return Severity.CRITICAL
    if percentage >= _warning_pct(thresholds):
        return Severity.WARNING
    return Severity.NORMAL


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234567 → 1.23M, 12345 → 12.3K, 123 → 123."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ANSI color codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_BLUE = "\033[34m"
_WHITE = "\033[37m"

_SEVERITY_COLOR = {
    Severity.NORMAL: _GREEN,
    Severity.WARNING: _YELLOW,
    Severity.CRITICAL: _RED,
    Severity.OFFLINE: _DIM,
}

_MODEL_COLOR = {
    "Opus": _MAGENTA,
    "Sonnet": _CYAN,
    "Haiku": _BLUE,
}


def format_status(state: StatuslineState) -> str:
    """Format statusline with model breakdown and ANSI colors."""
    if state.current is None:
        return f"{_DIM}-- / -- (--){_RESET}"

    info = state.current
    color = _SEVERITY_COLOR.get(state.severity, _WHITE)

    if info.limit > 0:
        pct = f"{info.percentage:.0f}"
        header = f"{color}{_BOLD}${info.used:.2f}/${info.limit:.2f}({pct}%){_RESET}"
    else:
        # limit==0 means "no limit known", not "a limit of zero". The gateway sends
        # max_usd=0 both for a user with no personal budget (USER config unset is a
        # legitimate pass-through state) and when the 300s-TTL budget config cache
        # has expired and could not be rehydrated. Rendering "$12.35/$0.00(0%)"
        # in that case reads as "wildly over a zero budget", so show the spend and
        # mark the limit unknown instead. Spend is always real — it comes from the
        # no-TTL counter key, a different lineage from the config.
        header = f"{color}{_BOLD}${info.used:.2f}/--{_RESET}"

    suffix_map = {
        Severity.NORMAL: "",
        Severity.WARNING: f" {_YELLOW}{_BOLD}[!]{_RESET}",
        Severity.CRITICAL: f" {_RED}{_BOLD}[!!]{_RESET}",
        Severity.OFFLINE: f" {_DIM}[offline]{_RESET}",
    }
    header += suffix_map.get(state.severity, "")

    if not info.models:
        return header

    sorted_models = sorted(info.models, key=lambda m: m.cost_usd, reverse=True)

    parts = [header]
    for m in sorted_models:
        short = _short_name(m.model)
        mc = _MODEL_COLOR.get(short, _WHITE)

        tokens = []
        if m.input_tokens:
            tokens.append(f"in:{_fmt_tokens(m.input_tokens)}")
        if m.cache_write_tokens:
            tokens.append(f"cw:{_fmt_tokens(m.cache_write_tokens)}")
        if m.cache_read_tokens:
            tokens.append(f"cr:{_fmt_tokens(m.cache_read_tokens)}")
        if m.output_tokens:
            tokens.append(f"out:{_fmt_tokens(m.output_tokens)}")

        token_str = f" {_DIM}{' '.join(tokens)}{_RESET}" if tokens else ""
        parts.append(f"{mc}{_BOLD}{short}{_RESET}:${m.cost_usd:.2f}{token_str}")

    return f" {_DIM}|{_RESET} ".join(parts)
