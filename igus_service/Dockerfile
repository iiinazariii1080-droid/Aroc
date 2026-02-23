# syntax=docker/dockerfile:1

# Use Python 3.13 slim image per project runtime
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVICE_HOST=0.0.0.0 \
    SERVICE_PORT=8101 \
    PYTHONPATH=/app:/app/drivers/dryve-d1-driver/src

WORKDIR /app

# Install runtime dependencies (none required beyond Python for this project)
# Add build essentials only if you later add packages needing compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    curl \
 && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching)
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application source
COPY . .

EXPOSE 8101

# Run via uvicorn — proper ASGI server with signal handling
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8101", "--log-level", "info"]


