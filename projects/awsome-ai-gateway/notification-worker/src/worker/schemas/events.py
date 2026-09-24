# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    # ⚠️ seed(notification.notification_configs, db/init/06_seed_notification_configs.sql)가
    #    tie-breaker 다 — enum 은 seed 의 event_type 집합과 정확히 일치해야 한다. 여기 없는
    #    타입으로 발행되면 pydantic 이 거부해 이벤트가 조용히 폐기된다(알림 0건).
    #    켜고 끄는 스위치는 enum 이 아니라 notification_configs.enabled 다.
    BUDGET_THRESHOLD = "budget_threshold"
    # api-key-helper가 자동 갱신하므로 현재 key_expiring/expired 발행자는 없다 —
    # 발행자가 생기면 notification_configs.enabled 로 제어한다.
    KEY_EXPIRING = "key_expiring"
    KEY_EXPIRED = "key_expired"
    # 관리자/정책에 의한 폐기 시 발행
    KEY_REVOKED = "key_revoked"
    AUTH_FAILURE_SPIKE = "auth_failure_spike"
    PERMISSION_VIOLATION = "permission_violation"
    SUSPICIOUS_USAGE = "suspicious_usage"
    DEGRADATION_MODE = "degradation_mode"
    PROVIDER_ERROR = "provider_error"
    SERVICE_HEALTH_CHANGE = "service_health_change"


class Channel(str, Enum):
    EMAIL = "email"


class ServiceSource(str, Enum):
    GATEWAY_PROXY = "gateway-proxy"
    ADMIN_API = "admin-api"
    COST_RECORDER_WORKER = "cost-recorder-worker"


class NotificationEvent(BaseModel):
    event_id: str
    type: EventType
    timestamp: datetime
    source: ServiceSource
    payload: dict[str, Any]

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


def parse_pubsub_message(raw_data: str | bytes) -> NotificationEvent | None:
    """Parse a raw Redis Pub/Sub message into a NotificationEvent.

    Returns None on parse failure so callers can skip and continue.
    """
    try:
        data = json.loads(raw_data)
        return NotificationEvent.model_validate(data)
    except Exception as exc:
        # ⚠️ 이유를 메시지 본문에 넣는다. 예전엔 `extra={...}` 로만 넘겼는데, stdlib
        #    기본 포매터는 extra 키를 출력하지 않으므로 로그에는 "pubsub_parse_failed"
        #    한 줄만 남았다. 실제로 봉투 불일치(payload 누락 / 대문자 type)로 전량이
        #    폐기되는 동안 원인이 로그에 전혀 드러나지 않았다.
        logger.error(
            "pubsub_parse_failed error=%s raw=%s",
            exc,
            str(raw_data)[:200],
            extra={"error": str(exc), "raw": str(raw_data)[:200]},
        )
        return None
