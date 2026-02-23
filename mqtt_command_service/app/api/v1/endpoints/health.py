"""Health check endpoints."""
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services import mqtt_state
from app.utils.certificate import check_disk_space
from config_storage import get_storage
from constants import HEALTH_CHECK_MIN_DISK_SPACE

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/health",
    summary="Health check endpoint",
    description="Returns service health status. Used by Docker healthcheck and monitoring tools.",
    response_model=None,
    responses={
        200: {
            "description": "Service is healthy",
            "content": {
                "application/json": {
                    "example": {
                        "status": "healthy",
                        "database": "ok",
                        "timestamp": "2024-01-01T12:00:00Z"
                    }
                }
            }
        },
        503: {
            "description": "Service is unhealthy",
        }
    }
)
async def health_check() -> dict[str, Any] | JSONResponse:
    """
    Health check endpoint for Docker and monitoring.

    Checks:
    - Database connectivity
    - MQTT broker connectivity
    - Disk space availability
    - Basic service availability

    Returns 200 if healthy, 503 if unhealthy.
    """
    health_status = {
        "status": "healthy",
        "api": "ok",
        "database": "unknown",
        "mqtt_broker": "unknown",
        "mqtt_bridge": "unknown",
        "mqtt_telemetry": "unknown",
        "disk_space": "unknown",
        "last_publish_time": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    unhealthy = False

    # Check database connectivity
    try:
        storage = get_storage()
        storage.get("__health_check__")
        health_status["database"] = "ok"
    except Exception as e:
        logger.warning("Database health check failed: %s", e)
        health_status["database"] = "error"
        health_status["database_error"] = str(e)
        unhealthy = True

    # Check MQTT connectivity via cached client state (no expensive test connection)
    states = mqtt_state.all_states()
    if states:
        # We have registered MQTT clients — use their cached state
        bridge_connected = states.get("bridge")
        telemetry_connected = states.get("telemetry")

        if bridge_connected is not None:
            health_status["mqtt_bridge"] = "ok" if bridge_connected else "error"
        if telemetry_connected is not None:
            health_status["mqtt_telemetry"] = "ok" if telemetry_connected else "error"

        # If any client is connected, broker is reachable
        any_connected = any(v for v in states.values() if v)
        if any_connected:
            health_status["mqtt_broker"] = "ok"
        elif all(v is False for v in states.values()):
            health_status["mqtt_broker"] = "error"
            health_status["mqtt_broker_error"] = "All MQTT clients disconnected"
    else:
        # No clients registered yet — broker state unknown
        health_status["mqtt_broker"] = "unknown"

    # Check disk space
    try:
        # Check if we have at least minimum required space (for certificate uploads)
        if check_disk_space(HEALTH_CHECK_MIN_DISK_SPACE):
            health_status["disk_space"] = "ok"
        else:
            health_status["disk_space"] = "warning"
            health_status["disk_space_message"] = "Low disk space"
            # Disk space warning is not critical, but should be monitored
    except Exception as e:
        logger.warning("Disk space health check failed: %s", e)
        health_status["disk_space"] = "error"
        health_status["disk_space_error"] = str(e)
        # Disk space error is not critical for API health

    # Determine overall health status
    # API is unhealthy if database is down
    # MQTT broker connectivity issues are warnings but not critical for API health
    # However, if both bridge and telemetry MQTT are down, that's a problem
    mqtt_services_down = (
        health_status.get("mqtt_bridge") == "error" and
        health_status.get("mqtt_telemetry") == "error"
    )

    if unhealthy or mqtt_services_down:
        health_status["status"] = "unhealthy"
        return JSONResponse(
            content=health_status,
            status_code=503
        )

    # Check if MQTT broker is reachable but services aren't connected (warning)
    if (health_status["mqtt_broker"] == "ok" and
        (health_status.get("mqtt_bridge") == "error" or
         health_status.get("mqtt_telemetry") == "error")):
        health_status["status"] = "degraded"
        health_status["warning"] = "MQTT broker is reachable but some services are not connected"

    return health_status


@router.get("/", tags=["info"])
async def root() -> dict[str, str | dict[str, str]]:
    """Root endpoint with API information."""
    return {
        "message": "MQTT Bridge Configuration API",
        "version": settings.API_VERSION,
        "api_version": "v1",
        "endpoints": {
            "GET /health": "Health check endpoint",
            "GET /api/v1/config/broker": "Get current broker settings",
            "POST /api/v1/config/broker": "Update broker settings",
            "GET /api/v1/auth/api-keys": "List API keys (admin only)",
            "POST /api/v1/auth/api-keys": "Create API key (admin only)",
        },
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json"
        }
    }

