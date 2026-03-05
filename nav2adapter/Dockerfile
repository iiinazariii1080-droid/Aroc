# Попробуйте альтернативные образы если основной не работает:
FROM python:3.12-slim
# Альтернативы:
# FROM python:3.11-alpine
# FROM ubuntu:22.04

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates wget && \
    rm -rf /var/lib/apt/lists/* && \
    # Install gosu for user switching
    wget -O /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/1.17/gosu-$(dpkg --print-architecture)" && \
    chmod +x /usr/local/bin/gosu && \
    gosu --version

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy app
COPY . ./

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root user (best practice)
RUN useradd -m -u 10001 appuser && \
    chown -R appuser:appuser /app

# Use entrypoint to handle permissions at runtime
ENTRYPOINT ["docker-entrypoint.sh"]

# Default envs
ENV SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=7905 \
    LOG_LEVEL=info \
    ROBOT_POSITIONS_DB_PATH=/app/data/database.db

EXPOSE 7905 7906

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS "http://localhost:${SERVICE_PORT}/livez" || exit 1

# Run uvicorn (entrypoint will switch to appuser)
# Shell-form so SERVICE_PORT / LOG_LEVEL env vars are expanded at runtime
# (must match HEALTHCHECK which also uses ${SERVICE_PORT}).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${SERVICE_PORT} --log-level ${LOG_LEVEL}"]

