FROM python:3.13-slim

# Prevent Python from writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System dependencies for healthcheck and optional OpenCV runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl git \
       libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY xarm_service/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Copy application source
COPY xarm_service/ /app/

# Copy shared_config package (used by app/config.py)
COPY shared_config/ /app/shared_config/

# Service configuration
ENV SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=8102

EXPOSE 8102

# Basic healthcheck against the service
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8102/healthz || exit 1

# Run the API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8102"]


