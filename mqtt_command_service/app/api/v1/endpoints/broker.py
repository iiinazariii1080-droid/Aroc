"""Broker configuration endpoints."""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Security, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.exceptions import ConfigurationError
from app.core.security import API_KEY_HEADER, Role, optional_auth
from app.models.schemas import BrokerConfigResponse, BrokerConfigUpdate
from app.services.config_service import get_config_service
from app.utils.config_mapping import prepare_config_updates
from config import (
    test_mqtt_connection,
)
from constants import DEFAULT_CONNECTION_TEST_TIMEOUT, MQTT_DEFAULT_PORT, MQTT_TLS_PORT

logger = logging.getLogger(__name__)

router = APIRouter()
limiter = Limiter(key_func=get_remote_address, enabled=False)


@router.get(
    "/config/broker",
    response_model=BrokerConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current broker settings",
    description="Returns current MQTT broker settings and credentials from database.",
    responses={
        200: {
            "description": "Current broker settings",
            "content": {
                "application/json": {
                    "example": {
                        "MQTT_BROKER": "123.123.123.123",
                        "MQTT_PORT": MQTT_DEFAULT_PORT,
                        "mqtt_user": "admin",
                        "mqtt_password": "secret"
                    }
                }
            }
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        429: {
            "description": "Too many requests - rate limit exceeded",
        },
        500: {
            "description": "Configuration retrieval error",
        }
    }
)
@limiter.limit("200/minute")
async def get_broker_settings(
    request: Request,
    include_password: bool = Query(False, description="Include password (admin only)"),
    role: Role = Security(optional_auth())
) -> BrokerConfigResponse:
    """
    Get current MQTT broker configuration.

    Returns the current broker settings including:
    - Broker address (hostname or IP)
    - Port number
    - Username and password (password masked by default)
    - TLS configuration (if enabled)
    - Certificate paths (if TLS is enabled)

    **Authentication:**
    - Requires API key with READ role or higher

    **Password visibility:**
    - By default, password is masked as `****`
    - Set `include_password=true` to see actual password (ADMIN role required)

    **Example request:**
    ```bash
    curl -X GET "http://localhost:7900/api/v1/config/broker?include_password=false" \
      -H "X-API-Key: your-api-key"
    ```

    **Example response:**
    ```json
    {
      "broker": "192.168.1.100",
      "broker_port": 1883,
      "mqtt_user": "admin",
      "mqtt_password": "****",
      "mqtt_use_tls": false
    }
    ```

    Values are loaded from database with fallback to environment variables.
    """
    try:
        # Use config_service to get current config
        config_service = get_config_service()
        bridge_config = await asyncio.to_thread(config_service.get_config)

        # Convert BridgeConfig to response format
        response_data = {
            "broker": bridge_config.broker,
            "broker_port": bridge_config.broker_port,
            "mqtt_user": bridge_config.mqtt_user,
            "mqtt_password": bridge_config.mqtt_password,
            "mqtt_use_tls": bridge_config.mqtt_use_tls,
            "mqtt_tls_insecure": bridge_config.mqtt_tls_insecure,
        }
        response = BrokerConfigResponse.model_validate(response_data)

        # If admin requested password, bypass serializer by using model_dump and manual override
        if include_password and role == Role.ADMIN:
            # Return dict directly to bypass serializer
            result = response.model_dump()
            result["mqtt_password"] = bridge_config.mqtt_password
            return JSONResponse(content=result)  # type: ignore[return-value]

        return response
    except Exception as e:
        logger.error("Failed to get broker config: %s", e, exc_info=True)
        raise ConfigurationError(f"Failed to retrieve broker configuration: {e!s}")


@router.post(
    "/config/broker",
    response_model=BrokerConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Update MQTT broker settings",
    description="""
    Updates MQTT broker settings and credentials.

    Can update all fields simultaneously or only selected ones.
    All changes are saved to database with change history.

    **Validation:**
    - `broker`: must be a valid IP address (IPv4/IPv6) or hostname
    - `broker_port`: must be in range 1-65535
    - `mqtt_user`: cannot be empty, maximum 128 bytes
    - `mqtt_password`: maximum 256 bytes (can be empty)
    """,
    responses={
        200: {
            "description": "Settings successfully updated",
            "content": {
                "application/json": {
                    "example": {
                        "broker": "192.168.1.100",
                        "broker_port": MQTT_DEFAULT_PORT,
                        "mqtt_user": "admin",
                        "mqtt_password": "secret"
                    }
                }
            }
        },
        400: {
            "description": "Input validation error",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        403: {
            "description": "Forbidden - insufficient permissions (requires WRITE role)",
        },
        429: {
            "description": "Too many requests - rate limit exceeded",
        },
        500: {
            "description": "Database save error",
        }
    }
)
@limiter.limit("100/minute")
async def update_broker_settings(
    request: Request,
    update: BrokerConfigUpdate,
    role: Role = Security(optional_auth())
) -> BrokerConfigResponse:
    """
    Update MQTT broker settings and credentials.

    Accepts partial update - can specify only fields that need to be changed.
    All changes are saved atomically to database with change history.

    **Authentication:**
    - Requires API key with WRITE role or higher

    **Validation:**
    - `broker`: must be a valid IP address (IPv4/IPv6) or hostname (RFC 1123)
    - `broker_port`: must be in range 1-65535
    - `mqtt_user`: cannot be empty, maximum 128 bytes
    - `mqtt_password`: maximum 256 bytes (can be empty)

    **Auto-port switching:**
    - If `mqtt_use_tls` is enabled and port is default (1883), port switches to TLS port (8883)
    - If `mqtt_use_tls` is disabled and port is TLS port (8883), port switches to default (1883)

    **Example request:**
    ```bash
    curl -X POST "http://localhost:7900/api/v1/config/broker" \
      -H "X-API-Key: your-write-key" \
      -H "Content-Type: application/json" \
      -d '{
        "broker": "test.mosquitto.org",
        "broker_port": 1883,
        "mqtt_user": "admin",
        "mqtt_password": "secret"
      }'
    ```

    **Example response:**
    ```json
    {
      "broker": "test.mosquitto.org",
      "broker_port": 1883,
      "mqtt_user": "admin",
      "mqtt_password": "****",
      "mqtt_use_tls": false
    }
    ```
    """
    try:
        if API_KEY_HEADER in request.headers and role == Role.READ:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Write access required"
            )

        # Validation happens automatically via Pydantic when creating the model
        # If validation fails, FastAPI will return 422 before calling this function
        config_service = get_config_service()
        bridge_config = await asyncio.to_thread(config_service.get_config)

        # Prepare all updates
        updates = prepare_config_updates(update)

        # Handle auto-port switching for TLS
        if update.mqtt_use_tls is not None and update.broker_port is None:
            if update.mqtt_use_tls and bridge_config.broker_port == MQTT_DEFAULT_PORT:
                # Switch to TLS port
                updates["MQTT_PORT"] = str(MQTT_TLS_PORT)
            elif not update.mqtt_use_tls and bridge_config.broker_port == MQTT_TLS_PORT:
                # Switch to non-TLS port
                updates["MQTT_PORT"] = str(MQTT_DEFAULT_PORT)

        # Build target config for connection test (merge current + updates)
        target_broker = updates.get("MQTT_BROKER", bridge_config.broker)
        target_port = int(updates.get("MQTT_PORT", bridge_config.broker_port))
        target_user = updates.get("MQTT_USER", bridge_config.mqtt_user)
        target_pass = updates.get("MQTT_PASS", bridge_config.mqtt_password)
        target_use_tls = (
            updates.get("MQTT_USE_TLS", str(bridge_config.mqtt_use_tls)).lower() == "true"
            if isinstance(updates.get("MQTT_USE_TLS", bridge_config.mqtt_use_tls), str)
            else bool(updates.get("MQTT_USE_TLS", bridge_config.mqtt_use_tls))
        )
        # Certificate paths are fixed - always use certs/ directory
        from constants import CERT_CA_FILE, CERT_CLIENT_CERT_FILE, CERT_CLIENT_KEY_FILE
        target_ca = str(CERT_CA_FILE) if CERT_CA_FILE.exists() else None
        target_cert = str(CERT_CLIENT_CERT_FILE) if CERT_CLIENT_CERT_FILE.exists() else None
        target_key = str(CERT_CLIENT_KEY_FILE) if CERT_CLIENT_KEY_FILE.exists() else None
        target_tls_insecure = (
            updates.get("MQTT_TLS_INSECURE", str(bridge_config.mqtt_tls_insecure)).lower() == "true"
            if isinstance(updates.get("MQTT_TLS_INSECURE", bridge_config.mqtt_tls_insecure), str)
            else bool(updates.get("MQTT_TLS_INSECURE", bridge_config.mqtt_tls_insecure))
        )

        # Pre-flight connection test; reject bad broker before saving
        success, error_msg = await asyncio.to_thread(
            test_mqtt_connection,
            target_broker,
            target_port,
            target_user,
            target_pass,
            DEFAULT_CONNECTION_TEST_TIMEOUT,
            use_tls=target_use_tls,
            ca_certs=target_ca,
            certfile=target_cert,
            keyfile=target_key,
            tls_insecure=target_tls_insecure,
        )
        if not success:
            logger.error(
                "[api] Broker connection test failed for %s:%s user=%s tls=%s: %s",
                target_broker, target_port, target_user or "anonymous", target_use_tls, error_msg
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Broker connection test failed: {error_msg or 'unknown error'}"
            )

        # No changes requested — just return current config
        if not updates:
            bridge_config = await asyncio.to_thread(config_service.get_config)
            response_data = {
                "broker": bridge_config.broker,
                "broker_port": bridge_config.broker_port,
                "mqtt_user": bridge_config.mqtt_user,
                "mqtt_password": bridge_config.mqtt_password,
                "mqtt_use_tls": bridge_config.mqtt_use_tls,
                "mqtt_tls_insecure": bridge_config.mqtt_tls_insecure,
            }
            return BrokerConfigResponse.model_validate(response_data)

        # Apply all updates atomically using config_service
        if not await asyncio.to_thread(
            config_service.update_config,
            updates,
            updated_by="api",
            reason="Broker configuration updated via API",
        ):
            raise ConfigurationError("Failed to save configuration to database")

        # Reload config from service for response
        bridge_config = await asyncio.to_thread(config_service.get_config, True)
        logger.info(
            "Broker config updated via API: broker=%s, port=%s, user=%s, tls=%s",
            bridge_config.broker,
            bridge_config.broker_port,
            bridge_config.mqtt_user,
            bridge_config.mqtt_use_tls,
        )

        # Convert keys for response
        response_data = {
            "broker": bridge_config.broker,
            "broker_port": bridge_config.broker_port,
            "mqtt_user": bridge_config.mqtt_user,
            "mqtt_password": bridge_config.mqtt_password,
            "mqtt_use_tls": bridge_config.mqtt_use_tls,
            "mqtt_tls_insecure": bridge_config.mqtt_tls_insecure,
        }
        return BrokerConfigResponse.model_validate(response_data)

    except HTTPException:
        raise
    except ValueError as e:
        # Validation errors from Pydantic
        logger.warning("Validation error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e)
        )
    except Exception as e:
        logger.error("Failed to update broker config: %s", e, exc_info=True)
        raise ConfigurationError(f"Internal server error: {e!s}")

