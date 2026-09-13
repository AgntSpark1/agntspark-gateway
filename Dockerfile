# ── AgntSpark Gateway Image ─────────────────────────────────────────────
# API Gateway + Auth service for the AgntSpark AI Agent hosting platform.
# ─────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

LABEL org.opencontainers.image.title="AgntSpark Gateway"
LABEL org.opencontainers.image.description="API Gateway + Auth service for the AgntSpark AI Agent hosting platform"
LABEL org.opencontainers.image.authors="dev@agntspark.com"
LABEL org.opencontainers.image.source="https://github.com/AgntSpark1/agntspark-gateway"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml LICENSE README.md ./
COPY agntspark_gateway/ ./agntspark_gateway/
COPY alembic/ ./alembic/
COPY alembic.ini ./

RUN pip install --upgrade pip && \
    pip install -e "."

RUN useradd -m -u 1000 -s /bin/bash agntspark && \
    chown -R agntspark:agntspark /app
USER agntspark

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

ENTRYPOINT ["python", "-m", "agntspark_gateway"]
