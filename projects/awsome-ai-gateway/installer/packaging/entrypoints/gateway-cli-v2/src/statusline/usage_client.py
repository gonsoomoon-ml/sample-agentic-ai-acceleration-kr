# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Gateway usage API client for statusline (BR-SL-05)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import requests
import structlog

from statusline.config import StatuslineConfig

log = structlog.get_logger(component="statusline")


@dataclass
class ModelUsage:
    model: str = ""
    cost_usd: Decimal = Decimal("0")
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    requests: int = 0


@dataclass
class UsageInfo:
    used: Decimal = Decimal("0")
    limit: Decimal = Decimal("0")
    remaining: Decimal = Decimal("0")
    percentage: float = 0.0
    period: str = ""
    fetched_at: datetime | None = None
    models: list[ModelUsage] = field(default_factory=list)
    # Operator's alert/THROTTLE ladder (budget.alert_thresholds). None = the gateway
    # did not tell us — an older build, or its budget-config cache was empty. Never
    # an empty list: see _parse_thresholds().
    alert_thresholds: list[int] | None = None


def _parse_thresholds(raw: object) -> list[int] | None:
    """budget.alert_thresholds → sorted ladder, or None when unknown.

    A missing key and ``null`` both mean "unknown" and must stay distinguishable
    from a real ladder, because determine_severity() falls back to 80/100 only for
    "unknown". An empty list collapses to None for the same reason: `[]` would pass
    a "field present" check while making every band comparison vacuous, silencing
    the indicator. Junk elements are dropped instead of raising — a malformed
    threshold must not turn a working statusline into the offline line.
    """
    if not isinstance(raw, list):
        return None
    keep = sorted(
        {
            int(t)
            for t in raw
            if isinstance(t, (int, float)) and not isinstance(t, bool) and 1 <= t <= 100
        }
    )
    return keep or None


def fetch_usage(config: StatuslineConfig, virtual_key: str) -> UsageInfo:
    """GET /v1/usage/me to retrieve current usage (BR-SL-05)."""
    url = f"{config.gateway_url}{config.usage_endpoint}"

    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {virtual_key}",
            "User-Agent": "claude-cli/gateway-cli-statusline",
        },
        timeout=(config.connect_timeout, config.read_timeout),
    )
    resp.raise_for_status()
    data = resp.json()

    budget = data.get("budget", {})
    used = Decimal(str(budget.get("used_usd", "0")))
    limit = Decimal(str(budget.get("max_usd", "0")))
    remaining = Decimal(str(budget.get("remaining_usd", "0")))
    percentage = float(budget.get("pct", 0.0))
    alert_thresholds = _parse_thresholds(budget.get("alert_thresholds"))

    models = []
    for m in data.get("model_breakdown", []):
        models.append(ModelUsage(
            model=m.get("model", ""),
            cost_usd=Decimal(str(m.get("cost_usd", "0"))),
            input_tokens=int(m.get("input_tokens", 0)),
            output_tokens=int(m.get("output_tokens", 0)),
            cache_write_tokens=int(m.get("cache_write_tokens", 0)),
            cache_read_tokens=int(m.get("cache_read_tokens", 0)),
            requests=int(m.get("requests", 0)),
        ))

    return UsageInfo(
        used=used,
        limit=limit,
        remaining=remaining,
        percentage=percentage,
        period=data.get("period", ""),
        fetched_at=datetime.now(timezone.utc),
        models=models,
        alert_thresholds=alert_thresholds,
    )
