FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY mqtt-bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendor shared library
COPY shared/ /app/shared/

# Copy service code
COPY mqtt-bridge/ /app/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "from shared.healthcheck import check; check()"

CMD ["python", "main.py"]
