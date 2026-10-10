# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

# Build stage
FROM public.ecr.aws/docker/library/python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock* ./
# --frozen 이 실패하면 uv.lock 이 없거나 오래된 것 — 그 경우만 비고정으로 간다
# (조용한 폴백이 lockfile drift 를 숨기지 않도록 로그를 남긴다).
RUN uv sync --frozen --no-dev --extra http --extra ses --extra smtp || \
    { echo "WARN: uv sync --frozen failed — syncing without lockfile" >&2; \
      uv sync --no-dev --extra http --extra ses --extra smtp; }

# Runtime stage
FROM public.ecr.aws/docker/library/python:3.11-slim

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import redis, os; r=redis.Redis.from_url(os.environ['REDIS_URL']); r.ping()" || exit 1

CMD ["python", "-m", "worker.main"]
