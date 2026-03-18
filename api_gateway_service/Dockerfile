# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8201 \
    PYTHONPATH=/app

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

COPY api_gateway_service/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY api_gateway_service/ /app/
COPY shared_config/ /app/shared_config/

# Create non-root user and ensure ownership for runtime user
RUN useradd -m -u 10001 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8201

# Healthcheck queries readiness endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/readyz || exit 1

# exec replaces shell with gunicorn as PID 1 — receives SIGTERM directly.
# Single worker is required: all stateful components (circuit breakers,
# hub_state, secret_store, service_metrics) live in-process memory.
# --timeout 120: async handlers never block the worker thread, but
# gunicorn kills workers that don't heartbeat within this window.
# 30s was too tight for camera streaming (cam_read_timeout=30s).
CMD ["sh", "-c", "exec gunicorn main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT} --workers 1 --timeout 120 --graceful-timeout 15"]


