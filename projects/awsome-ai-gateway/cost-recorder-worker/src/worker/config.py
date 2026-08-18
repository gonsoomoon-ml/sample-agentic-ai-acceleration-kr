# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 흔히 쓰는 비-IANA 약어/레거시 alias → 정규 IANA 이름 힌트 (검증은 그대로 엄격하게
# 유지하되, 실수했을 때 바로 고칠 수 있도록 에러 메시지에 제안을 붙인다).
_TZ_ALIAS_HINTS: dict[str, str] = {
    "KST": "Asia/Seoul",
    "JST": "Asia/Tokyo",
    "IST": "Asia/Kolkata",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "GMT": "UTC",
    "US/PACIFIC": "America/Los_Angeles",
    "US/EASTERN": "America/New_York",
    "US/CENTRAL": "America/Chicago",
    "US/MOUNTAIN": "America/Denver",
}


def _reporting_timezone_error(v: str) -> str:
    """reporting_timezone 검증 실패 메시지 — 흔한 실수(약어/레거시 alias/대소문자)에
    대해 정규 IANA 이름을 제안한다."""
    hint = _TZ_ALIAS_HINTS.get(v.strip().upper())
    if hint is None and v.upper() == "UTC" and v != "UTC":
        hint = "UTC"  # 대소문자 오타 (예: "utc")
    msg = f"Invalid reporting_timezone {v!r}: not a valid IANA timezone name"
    if hint:
        msg += f" (did you mean {hint!r}?)"
    msg += ". Use a canonical IANA name, e.g. 'Asia/Seoul', 'UTC', 'America/Los_Angeles'."
    return msg


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database — gateway-proxy와 동일 schema, 쓰기 가능한 사용자 필요
    db_url: str = "postgresql+asyncpg://gateway:gateway_dev_password@postgres:5432/gateway"
    db_pool_size: int = 10
    db_pool_overflow: int = 5
    db_ssl_mode: str = "disable"
    # RDS Proxy 경유 시 0 으로 설정 (PostgreSQL pinning 회피). Aurora 직접 연결 시엔 기본값 유지.
    db_statement_cache_size: int = 100

    # Redis
    redis_url: str = "redis://redis:6379/0"
    redis_tls_enabled: bool = False

    # Cost stream configuration — gateway-proxy `services/cost_recorder.py` 와 일치해야 함
    cost_stream_key: str = "cost:stream"
    cost_stream_group: str = "cost-recorder-workers"
    cost_stream_consumer: str = "worker-1"  # replica 구분 (prod에서 hostname 기반 권장)

    # Batch flush: 두 조건 중 먼저 도달한 것으로 flush
    batch_max_size: int = 100  # entries
    batch_max_interval_sec: float = 5.0
    xread_block_ms: int = 5_000  # XREADGROUP BLOCK 대기

    # Daily aggregator cron. 기본 매일 00:10 (reporting_timezone 기준).
    daily_usage_agg_cron: str = "10 0 * * *"

    # daily_aggregates 의 일별 캘린더 경계 + cron 실행 타임존(§59). IANA TZ 이름.
    # admin-api 의 REPORTING_TIMEZONE 과 동일 값으로 맞춰야 한다 — 안 그러면
    # daily_aggregates(이 값 기준)와 usage_logs 실시간 집계(admin-api 기준)가
    # 서로 다른 날짜 경계를 갖게 된다.
    reporting_timezone: str = "Asia/Seoul"

    @field_validator("reporting_timezone")
    @classmethod
    def _validate_reporting_timezone(cls, v: str) -> str:
        """Fail fast at boot on an invalid IANA TZ name instead of a CrashLoop
        surfacing only once the aggregator cron first fires."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(_reporting_timezone_error(v)) from e
        return v

    # Graceful shutdown
    shutdown_grace_period_sec: float = 30.0

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json | console

    # OTel
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    otel_service_name: str = "cost-recorder-worker"
    otel_traces_sampler_arg: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
