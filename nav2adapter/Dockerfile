FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates wget && \
    rm -rf /var/lib/apt/lists/* && \
    wget -O /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/1.17/gosu-$(dpkg --print-architecture)" && \
    chmod +x /usr/local/bin/gosu && \
    gosu --version

# Copy requirements and install
COPY nav2adapter/requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy app
COPY nav2adapter/ ./
# Copy shared_config (needed by app/config.py)
COPY shared_config/ ./shared_config/

# Copy entrypoint script
COPY nav2adapter/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root user
RUN useradd -m -u 10001 appuser && \
    chown -R appuser:appuser /app

ENTRYPOINT ["docker-entrypoint.sh"]

ENV SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=7905 \
    LOG_LEVEL=info

EXPOSE 7905 7906

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS "http://localhost:${SERVICE_PORT}/livez" || exit 1

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${SERVICE_PORT} --log-level ${LOG_LEVEL}"]
