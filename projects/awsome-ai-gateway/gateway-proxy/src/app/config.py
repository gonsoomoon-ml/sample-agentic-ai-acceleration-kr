# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    uvicorn_workers: int = 4
    uvicorn_host: str = "0.0.0.0"
    uvicorn_port: int = 8000
    max_body_size: int = 20_971_520  # 20MB
    app_version: str = "0.1.0"

    # Redis
    redis_url: str = "redis://redis:6379/0"
    redis_pool_size: int = 150
    redis_cluster_mode: bool | None = None  # None = auto-detect
    redis_tls_enabled: bool = False

    # Redis 연결 복원력 (부하 강건성, deepdive Q50 Phase 2).
    # 과거 클라이언트는 max_connections 만 줘 socket_timeout 이 None(무한 블로킹)이라,
    # 느린/블랙홀 노드 하나가 모든 awaited 호출을 멈춰 풀(150)을 전 pod 에서 고갈시켰다.
    #  - socket_timeout: 명령(read) 상한. 초과 시 TimeoutError → 상위 fallback(DB) 로.
    #  - socket_connect_timeout: 연결 수립 상한(failover 직후 죽은 노드 빠른 포기).
    #  - retry: 단발 blip 을 백오프 재시도로 흡수(느린 DB 강등 전 1~2회).
    #  - health_check_interval: idle 연결 주기 ping → failover 후 stale 연결 회수.
    # 0/None 비활성(과거 동작). hot-path 라 값 조정 시 load A/B 권장.
    redis_socket_timeout: float = 2.0
    redis_connect_timeout: float = 1.0
    redis_retries: int = 1
    redis_health_check_interval: float = 30.0

    # 읽기를 replica 로 라우팅(deepdive Q50 Phase4-f). cluster 모드 + replica 있을 때만
    # 의미. read 스케일 확보(GET 류를 replica 가 분담)하나 replica lag 으로 약간의
    # stale read 가능 → TTL 캐시·rate-limit ZSET 처럼 lag 허용 워크로드에서만 ON.
    # 기본 False(primary-only, 강한 일관성 — 기존 동작). standalone 에선 무영향.
    redis_read_from_replicas: bool = False

    # rate-limit 회로 차단기(deepdive Q50 — per-request fast-fail). Redis 가 죽었을 때
    # 매 요청이 socket_timeout 을 다 기다리는 대신, 연속 실패가 임계를 넘으면 회로를
    # 열어 즉시 fallback 으로 보낸다. 기본 활성(소켓 타임아웃 대기 누적 방지). 끄려면
    # rl_breaker_enabled=false. 임계/복구는 아래 값으로.
    rl_breaker_enabled: bool = True
    rl_breaker_fail_threshold: int = 5
    rl_breaker_recovery_timeout: float = 5.0

    # rate-limit eval 이 (Redis 장애 등으로) 실패할 때의 정책(deepdive Q50).
    #  - "open"(기본·기존 동작): 통과시킴(가용성 우선, NFR-2.4 graceful degradation).
    #    Redis 장애가 사용자 차단으로 번지지 않게. rl_fail_open_total 로 가시화.
    #  - "closed": 차단함(보안/쿼타 정확성 우선). 무제한 통과를 허용 못 하는 환경용.
    # **무단 전환 금지** — 이건 가용성↔정확성 트레이드오프라 명시 설정으로만 바꾼다.
    # budget/auth 는 이미 fail-closed(예산 미확인 시 차단) — 의도적 비대칭(예산 초과
    # 과금 방지 > 가용성). rate-limit 만 기본 open 인 건 RL 이 남용방지지 과금 게이트가
    # 아니기 때문. 운영 정책에 따라 closed 로 정렬 가능.
    rl_fail_mode: str = "open"

    # Database
    db_url: str = "postgresql+asyncpg://gateway:gateway@postgres:5432/gateway"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_ssl_mode: str = "disable"
    # RDS Proxy 경유 시 0 으로 설정 (PostgreSQL pinning 회피). Aurora 직접 연결 시엔 기본값 유지.
    db_statement_cache_size: int = 100
    # 풀 고갈 시 커넥션 대기 상한. SQLAlchemy 기본은 30s 라 풀 포화 시 요청당 최대 30초
    # 무한대기성 지연("CPU 정상인데 느림" 시그니처)을 유발한다 → 짧은 값으로 fast-fail(TimeoutError).
    # hot-path(gateway-proxy)는 admin-api(core/db.py)와 정합되게 명시. (견고성 6축검증 축③)
    db_pool_timeout: int = 10
    # 커넥션 최대 수명(초). RDS Proxy/Aurora 가 유휴 커넥션을 끊으면 죽은 커넥션이 풀에 잔존.
    # pool_pre_ping 이 1차 방어하나, recycle 로 오래된 커넥션을 선제 폐기(기본 -1=무제한 회피).
    db_pool_recycle: int = 3600

    # Timeouts
    lua_timeout_ms: int = 1000
    stream_timeout: int = 300
    # SSE 청크 간 무응답 상한. 초과 시 `event: error`(timeout_error) 프레임을 보내고 종료.
    #
    # ⚠️ 두 개의 상한 사이에 끼워야 한다:
    #   ① upstream(Bedrock/Mantle) 첫 토큰 지연 — extended thinking 은 60s 를 넘길 수 있다.
    #      기본값 60 은 Opus extended thinking 요청을 정상 응답 중에 끊어버렸다.
    #   ② ALB idle_timeout — dev 300s / prod 600s
    #      (values-eks-fargate-{dev,prod}.yaml: load-balancer-attributes).
    #      이 값이 ALB 보다 **크거나 같으면** ALB 가 먼저 커넥션을 끊어 클라이언트는
    #      깔끔한 SSE 에러 대신 truncated stream 을 본다. 반드시 ALB 보다 작아야 한다.
    #
    # 기본 240 = dev ALB(300) 보다 60s 아래. 차트가 환경별로 override 한다
    # (STREAM_IDLE_TIMEOUT). ALB 값을 바꿀 때 이 값도 함께 조정할 것.
    stream_idle_timeout: int = 240
    # 클라이언트 끊김 후 백그라운드로 upstream 을 계속 소비하는 상한.
    # 이 시간 안에 받은 토큰까지 usage 로 기록된다(과금 정확성).
    stream_disconnect_drain_timeout: int = 30

    # Reliability
    usage_buffer_max: int = 10_000
    background_task_max_retries: int = 3

    # Redis-down in-memory rate-limit fallback (middleware/rate_limit.py).
    # 이 fallback 의 USER RPM 카운터는 **프로세스(uvicorn worker)마다 독립**이라,
    # 함대 전체엔 `replicas × uvicorn_workers` 개의 독립 카운터가 존재한다. 사용자
    # 트래픽이 그만큼 분산되므로 각 카운터는 `limit // (replicas × uvicorn_workers)`
    # 를 허용해야 클러스터 총합이 limit 에 맞는다. 과거엔 divisor 가 4(=uvicorn_workers,
    # 1 pod 가정)로 **하드코딩**되어 HPA replica 수(3~30)를 무시 → fallback 진입 시
    # 쿼타가 현실과 불일치(부하테스트 429×6 의 배경, deepdive Q46/Q50).
    #
    # rl_fallback_replicas 를 **현재 환경의 대표 replica 수(예: HPA minReplicas)**로
    # 설정하면 divisor 가 현실을 추종한다. 기본 1 = 과거 동작(divisor=uvicorn_workers)
    # 보존(무행동변경) — hot-path 라 변경 전 load A/B 필요. Redis 장애 시 발동하는
    # 경로라 SCARD 로 실시간 pod 수를 셀 수 없어 env 설정이 현실적 해법.
    rl_fallback_replicas: int = 1

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # OTel
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    otel_traces_sampler_arg: float = 1.0
    otel_service_name: str = "gateway-proxy"

    # AWS
    aws_region: str = "ap-northeast-2"
    aws_profile: str | None = None

    # Mantle (Cowork cross-account) — region for the STS client used to assume the
    # 905 Mantle role; and the httpx timeout for Mantle streaming calls.
    mantle_assume_region: str = "ap-northeast-2"
    mantle_http_timeout: float = 120.0

    # OpenModel
    openmodel_base_url: str = "http://mock-vllm:8080"

    # --- Model availability fallback / circuit breaker (2026-06-22) ---
    cb_window_sec: int = 30
    cb_min_calls: int = 5
    cb_error_rate: float = 0.5
    cb_open_sec: int = 30
    cb_halfopen_probe_ttl_ms: int = 8000
    cb_open_jitter_sec: int = 5
    bedrock_inference_read_timeout: int = 60
    bedrock_max_attempts: int = 1

    # Features
    cors_enabled: bool = False

    # Request tracing (reasoning/tool_use observability — services/trace_extractor.py)
    # trace_enabled: 응답 본문에서 trace 추출 자체를 켤지(미배선 — 추후 hot-path 배선용).
    # trace_mask_pii: PII 마스킹 토글. **기본 True(fail-safe)** — tool input 값 +
    #   thinking/text PII 패턴을 마스킹. 신뢰 환경에서만 false 로 명시 해제(평문 저장).
    #   설정 누락 시 안전(마스킹)으로 동작하도록 기본값이 True.
    trace_enabled: bool = False
    trace_mask_pii: bool = True

    # ── Body logging (요청/응답 **본문** → Firehose → S3) ──
    #
    # ⚠️ 이 기능을 켜면 사용자가 프롬프트에 넣은 것이 그대로 durable 저장소로 나간다.
    #    현재 구현은 **마스킹하지 않는다** — 같은 코드베이스의 trace 경로는
    #    `trace_mask_pii` 기본 True 로 마스킹하는데, 본문 로거에는 그것이 적용되지
    #    않는다. 그건 알고 있는 격차이고 향후 개선 대상이다. 그래서 두 겹으로 잠근다:
    #      (1) `firehose_stream_name` 이 없으면 로거 자체가 no-op(로컬/compose 안전),
    #      (2) 런타임 토글(`bodylog:enabled`)이 **기본 OFF** — 배포만으로는 켜지지 않고
    #          관리자가 명시적으로 켜야 한다(admin-api PUT /admin/settings/body-logging,
    #          그 액션은 audit.audit_logs 에 불변 행을 남긴다).
    body_logging_enabled: bool = True
    firehose_stream_name: str | None = None
    body_log_s3_bucket: str | None = None
    body_log_max_queue: int = 10_000
    body_log_batch_size: int = 100
    body_log_flush_interval: float = 5.0
    #: Firehose 레코드 상한(1 MB) 아래로. 초과분은 S3 에 직접 넣는다.
    body_log_max_record_bytes: int = 900_000
    #: 워커가 캐시된 플래그를 믿는 시간(초). 토글은 이 시간 안에 반영된다.
    body_log_flag_cache_ttl: float = 5.0
    #: 플래그를 읽지 못했을 때의 값. **False 여야 한다** — 설정을 못 읽었다는 이유로
    #: 본문 수집이 켜지면 안 된다(fail-safe 방향).
    body_log_flag_default: bool = False

    # --- AgentCore Gateway web search (server-side tool-use loop, 2026-07-01) ---
    # We inject a web_search tool, intercept the model's tool_use, call AgentCore
    # Gateway's managed WebSearch connector over MCP (SigV4/IRSA), feed results
    # back, and stream the final answer — emulating Anthropic 1P server-side search.
    # Bedrock does NOT expose native web_search (verified: ValidationException), so
    # this gateway loop is the only path. The managed connector is us-east-1-only,
    # so AGENTCORE_REGION differs from AWS_REGION (ap-northeast-2) — cross-region;
    # IRSA creds are global, only the SigV4 signing scope + endpoint use us-east-1.
    #   agentcore_gateway_url: full MCP endpoint (…gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp)
    #   agentcore_target_id:   Gateway target name → tool is "<target_id>___WebSearch"
    #   web_search_enabled:    global kill-switch (per-client toggle lives in routing_profiles)
    agentcore_gateway_url: str = ""
    agentcore_region: str = "us-east-1"
    agentcore_target_id: str = "web-search-tool"
    agentcore_http_timeout: float = 30.0
    #: Cap on the whole MCP handshake (three HTTP calls). agentcore_http_timeout is PER CALL,
    #: so on its own one handshake could take 3× that, and the handshake lock is
    #: process-global, so concurrent requests would queue behind it (see the field comments
    #: in services/agentcore_mcp_client.py).
    agentcore_handshake_timeout: float = 10.0
    #: How long to suppress handshake retries after a failure, so every request does not pay
    #: the full timeout against a dead gateway. The price is a recovery delayed by up to this.
    agentcore_handshake_negative_ttl: float = 30.0
    web_search_enabled: bool = False
    #: Defaults of the web-search settings below (2026-09-19): the values that were measured
    #: and verified on US dev are the DEFAULTS, so an install that sets nothing gets the cheap
    #: caps and the fixed behaviour instead of having to discover a script. Every one of them
    #: is still an env var — a client or model update that breaks something is rolled back
    #: by config, without a rebuild. Previous defaults: 5 rounds / 10 results / 60000 chars /
    #: 4 per turn / text trace / cowork only / mixed-turn and soft-final off.
    #:
    #: Search ROUNDS per request. 2→3 measured +50% searches and +47% cost with the same
    #: answer on narrow questions; only comparisons of 7+ items gained (2026-09-19).
    web_search_max_iterations: int = 2
    web_search_total_deadline_sec: float = 90.0
    #: Results kept per search (after URL/mirror de-duplication). The model's ``max_results``
    #: is honoured only up to this value — despite the name it is a MAXIMUM (2026-09-17: the
    #: model asked for 15 and the 12k-char cap cut the result JSON mid-record). 0 = unlimited.
    #: The connector is asked for up to 2× this number so de-duplication has candidates.
    web_search_max_results_default: int = 5
    #: Cap on ONE search's result text (chars). 0 = unlimited (pre-cap behaviour).
    #: max_iterations bounds turns and total_deadline_sec bounds time; the only bound on the
    #: bill-deciding axis — bytes injected into the next turn's input — is this one. One
    #: search once injected ~17.4K tokens on dev; 60000 chars ≈ 15K tokens, 12000 ≈ 3–4K.
    web_search_max_result_chars: int = 12000
    #: Searches run per TURN. 0 = unlimited. The model may issue many parallel web_search
    #: calls in one turn and the loop supports that; 20 of them would put 20× results into
    #: the next turn's input — an uncapped single-request cost, or a context overflow that
    #: 400s the continuation turn and loses everything billed so far. Observed: the model
    #: never asked for more than 3 in one turn.
    web_search_max_searches_per_turn: int = 3
    #: Put a cache_control marker on the search results (tool_result) the loop injects so later
    #: turns of the same request read them at the cached price (10% of list). 2026-09-16: most
    #: of a multi-search request's input cost was results re-sent every turn (3 searches: 6R
    #: result tokens, about 3.8R with the marker). _place_cache_breakpoint keeps the 4-marker
    #: limit. Set False to disable if it misbehaves.
    web_search_cache_results: bool = True
    #: Per-RESULT body cap (chars). Unlike max_result_chars (which cuts the whole JSON) this
    #: keeps every result's title/URL/lead and cuts only the body at a sentence boundary,
    #: dropping URL duplicates (2026-09-16: raw results were 13–24k chars; 1500 per item saves
    #: 40–50% of the tokens per search and no trailing result is lost). 0 = off.
    web_search_result_text_chars: int = 1500
    #: Language of the trace line left for the client ("en" | "ko"). The prefix
    #: `🔎 [gateway web_search]` is shared (the imitation filter and the tool description
    #: match on it).
    web_search_trace_lang: str = "en"
    #: "native" (default) | "text". Native leaves the trace as Anthropic native search blocks
    #: (server_tool_use + web_search_tool_result) in addition to the text line — a text line
    #: alone is denied under questioning ("I never searched") and imitated when it shares a
    #: message with a client tool call. Replayed blocks are converted back on the way in
    #: (2026-09-17). Only the client classes listed below get the blocks; every other client
    #: keeps the text line, so "native" is safe as a default. "text" turns the blocks off.
    web_search_trace_mode: str = "native"
    #: Client classes that get native traces (cowork | claude-code | codex, comma-separated;
    #: empty = all). Classification comes from ClientIdentificationMiddleware, not from a raw
    #: header value. Only clients that REPLAY the blocks verbatim belong here — otherwise the
    #: results never reach the model on the next request. Verified: cowork (2026-09-17),
    #: claude-code CLI 2.1.276 (2026-09-18: byte-identical replay, also with --continue, and
    #: its built-in WebSearch helper builds proper link results only from native blocks).
    web_search_trace_native_clients: str = "cowork,claude-code"
    #: Per-result excerpt length (chars) carried in the native blocks — what the replayed
    #: tool_result shows the model on later turns. 0 (default) = the same length as the trimmed
    #: result the model saw (web_search_result_text_chars). Shorter cuts evidence.
    web_search_digest_chars: int = 0
    #: Web search next to the CLIENT's own tools (2026-09-18). On by default since both were
    #: confirmed with real Cowork and real Claude Code (2026-09-19); set to false to get the
    #: previous behaviour back if a client or model update breaks them.
    #: - mixed_turn_run: a turn that calls a client tool AND web_search — run the searches and
    #:   send their native block pairs in the same message as the client's tool_use (native
    #:   trace mode only). Off: the searches are not run and the model re-issues them later.
    #: - final_turn_soft: when the search budget is used up, keep every tool callable; a
    #:   further web_search gets an error instead of running (like a server tool's max_uses),
    #:   and only then the hard final turn (tool_choice: none) follows. Off: hard final turn
    #:   at once, which also blocks the client's tools ("search and save a file" saved nothing).
    web_search_mixed_turn_run: bool = True
    web_search_final_turn_soft: bool = True

    #: Tool ``type`` prefixes (comma-separated) removed from /v1/messages requests before they
    #: go upstream: Anthropic-only SERVER tools that Bedrock rejects with a 400 for the whole
    #: request. 2026-09-18: Claude Code's advisor (``advisor_20260301``) made every request of
    #: an affected user fail, "hi" included. A new Anthropic-only tool type is a config change
    #: here, not a release. Blank = pass everything through. See services/upstream_compat.py.
    bedrock_unsupported_tool_type_prefixes: str = "advisor_"

    # ── Reporting timezone (§59) ──
    # 비용/사용량 집계의 "월/일 경계" 기준 타임존. admin-api·cost-recorder-worker·
    # notification-worker 와 같은 env(REPORTING_TIMEZONE)를 읽는다 — 네 서비스가
    # 같은 budget_usages.period 행과 budget:* Redis 키를 쓰므로 값이 갈리면 월 경계
    # 몇 시간 동안 서로 다른 행/키를 본다. **정규 IANA 이름만**(예: "Asia/Seoul",
    # "UTC", "America/Los_Angeles") — "KST"/"US/Pacific" 같은 약어·레거시 alias 는
    # 배포 이미지의 tzdata 에 없을 수 있어 거부.
    reporting_timezone: str = "Asia/Seoul"

    @field_validator("reporting_timezone")
    @classmethod
    def _validate_reporting_timezone(cls, v: str) -> str:
        """기동 시점에 잘못된 IANA 이름을 거부한다 — 쿼리 시점의 500 보다 부팅
        실패가 낫다(admin-api core/config.py 의 동명 검증과 같은 규약)."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(
                f"Invalid REPORTING_TIMEZONE {v!r}: not a valid IANA timezone name. "
                "Use a canonical IANA name, e.g. 'Asia/Seoul', 'UTC', 'America/Los_Angeles'."
            ) from e
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
