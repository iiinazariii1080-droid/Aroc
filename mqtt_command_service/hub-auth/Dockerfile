FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY hub-auth/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendor shared library
COPY shared/ /app/shared/

# Copy service code
COPY hub-auth/ /app/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8101

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8101/health')"

CMD ["python", "main.py"]
