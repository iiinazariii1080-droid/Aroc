FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy app
COPY . ./

# Provide an example config for k8s (can be mounted/overridden via ConfigMap)
RUN printf "server_ip: 127.0.0.1\n\nigus_container_ip: 127.0.0.1\nigus_container_port: 8101\n\nxarm_container_ip: 127.0.0.1\nxarm_container_port: 8102\n\nsymovo_container_ip: 127.0.0.1\n\n# nav2adapter teleop (host:7905), robot_id for /api/v1/robots/<id>/move/speed\nsymovo_nav2adapter_host: 192.168.1.10\nsymovo_robot_id: fahrdummy-01\n\ndepth_camera_container_ip: 192.168.1.55\ndepth_camera_container_port: 8000\n\n# joystick defaults\njoystick_deadzone: 0.12\njoystick_default_ttl_ms: 150\njoystick_move_acc: 1000\njoystick_is_move_tool: false\njoystick_mode: 0\n" > /app/config.yaml
# Default envs
ENV SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=8110 \
    LOG_LEVEL=info \
    UVICORN_WORKERS=2 \
    APP_CONFIG_FILE=/app/config.yaml

EXPOSE 8110

# Healthcheck: ensure process is responsive and joystick worker is running
HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=30s CMD \
  curl -fsS --max-time 5 http://127.0.0.1:8110/healthz || exit 1

# Run uvicorn with multiple workers for isolation of long handlers
CMD ["/bin/sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8110 --workers ${UVICORN_WORKERS} --log-level ${LOG_LEVEL}"]

