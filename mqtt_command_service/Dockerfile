FROM python:3.11.11-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy requirements first for better caching
COPY requirements.lock /app/
RUN pip install --no-cache-dir -r requirements.lock

# Copy all Python files from root
COPY *.py /app/

# Copy app directory with all subdirectories
COPY app/ /app/app/

# Copy scripts directory
COPY scripts/ /app/scripts/

# Create directories writable by appuser
RUN mkdir -p /app/data /app/certs /app/logs && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 7900

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python3", "healthcheck.py"]

CMD ["python3", "main.py"]