# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=8101

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates build-essential curl && \
    rm -rf /var/lib/apt/lists/*

COPY igus_service/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY igus_service/ .
COPY shared_config/ ./shared_config/

RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8101

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8101/ready || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8101", "--log-level", "info"]


