"""config-api microservice — FastAPI REST source of truth for broker config."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from routes.api_keys import router as api_keys_router
from routes.broker import router as broker_router
from routes.certificates import router as certificates_router

from config import get_settings
from shared.logging_config import configure_logging
from shared.utils import now_iso
from storage import get_store, init_store

_settings = get_settings()
configure_logging(_settings.log_level)
# Initialize store early to break circular import with security/config
init_store(_settings.config_file_path)
logger = logging.getLogger(__name__)

# Validate security-critical settings at startup
if _settings.internal_service_key and len(_settings.internal_service_key) < 16:
    logger.warning(
        "INTERNAL_SERVICE_KEY is shorter than 16 characters — "
        'use a stronger key: python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )

if not os.environ.get("SSL_KEYFILE") or not os.environ.get("SSL_CERTFILE"):
    logger.critical(
        "config-api is running WITHOUT TLS — API keys and MQTT credentials "
        "will be transmitted in plaintext. Set SSL_KEYFILE and SSL_CERTFILE "
        "environment variables to enable HTTPS."
    )


def _seed_broker_config_from_env() -> None:
    """Populate config.json from environment variables on first start."""
    import os

    store = get_store()
    data = store.load()
    if data.get("MQTT_BROKER"):
        return  # already seeded

    env_map = {
        "MQTT_BROKER": os.environ.get("MQTT_BROKER", ""),
        "MQTT_PORT": os.environ.get("MQTT_PORT", "8883"),
        "MQTT_USER": os.environ.get("MQTT_USER", ""),
        "MQTT_PASS": os.environ.get("MQTT_PASS", ""),
        "MQTT_USE_TLS": os.environ.get("MQTT_USE_TLS", "false"),
    }
    updates = {k: v for k, v in env_map.items() if v}
    if not updates:
        return

    store.update(lambda d: {**d, **updates})
    logger.info("Seeded broker config from env: %s", list(updates.keys()))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    _seed_broker_config_from_env()
    logger.info("config-api starting on %s:%s", settings.api_host, settings.api_port)
    yield
    logger.info("config-api shutting down")


app = FastAPI(
    title="AROC Config API",
    description="Source of truth for MQTT broker settings, certificates, and API keys",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# Include routes
app.include_router(broker_router, prefix="/api/v1", tags=["config"])
app.include_router(certificates_router, prefix="/api/v1", tags=["certificates"])
app.include_router(api_keys_router, prefix="/api/v1/auth", tags=["auth"])


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health_check() -> dict:
    try:
        get_store().load()
        store_status = "ok"
    except Exception as e:
        store_status = f"error: {e}"

    healthy = store_status == "ok"
    body = {
        "status": "healthy" if healthy else "unhealthy",
        "store": store_status,
        "timestamp": now_iso(),
    }
    if not healthy:
        return JSONResponse(content=body, status_code=503)
    return body


@app.get("/")
async def root() -> dict:
    return {
        "service": "config-api",
        "version": "1.0.0",
        "endpoints": {
            "GET /health": "Health check",
            "GET /api/v1/config/broker": "Get broker settings",
            "PUT /api/v1/config/broker": "Update broker settings",
            "GET /api/v1/config/certificates/ca": "Download CA certificate",
            "POST /api/v1/config/certificates/upload": "Upload certificate",
            "GET /api/v1/auth/api-keys": "List API keys",
            "POST /api/v1/auth/api-keys": "Create API key",
            "DELETE /api/v1/auth/api-keys/{key_id}": "Revoke API key",
        },
    }


if __name__ == "__main__":
    settings = get_settings()
    ssl_keyfile = os.environ.get("SSL_KEYFILE")
    ssl_certfile = os.environ.get("SSL_CERTFILE")
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
