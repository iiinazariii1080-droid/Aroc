"""Broker configuration endpoints."""

import asyncio
import logging
import secrets
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Security, status
from fastapi.responses import JSONResponse

from schemas import BrokerConfigResponse, BrokerConfigUpdate
from security import Role, optional_auth, require_auth
from shared.constants import DEFAULT_CONNECTION_TEST_TIMEOUT, MQTT_DEFAULT_PORT
from shared.env import parse_bool, resolve_mqtt_port
from storage import get_store

logger = logging.getLogger(__name__)

router = APIRouter()

# Serialize broker update requests so the connection-test → save sequence is atomic.
_broker_update_lock = asyncio.Lock()


def _load_broker_config() -> dict[str, Any]:
    """Load broker config from store with defaults."""
    store = get_store()
    data = store.load()
    return {
        "broker": data.get("MQTT_BROKER", ""),
        "broker_port": int(data.get("MQTT_PORT", MQTT_DEFAULT_PORT)),
        "mqtt_user": data.get("MQTT_USER", ""),
        "mqtt_password": data.get("MQTT_PASS", ""),
        "mqtt_use_tls": parse_bool(data.get("MQTT_USE_TLS", False)),
        "mqtt_tls_insecure": parse_bool(data.get("MQTT_TLS_INSECURE", False)),
        "auth_mode": data.get("AUTH_MODE", "password"),
    }


def _check_mqtt_connection(
    broker: str,
    port: int,
    user: str,
    password: str,
    timeout: float = 5.0,
    use_tls: bool = False,
    ca_certs: str | None = None,
    certfile: str | None = None,
    keyfile: str | None = None,
    tls_insecure: bool = False,
) -> tuple[bool, str | None]:
    """Test connection to MQTT broker."""
    from paho.mqtt import client as mqtt_client

    test_client = None
    result: dict[str, Any] = {"connected": False, "error": None}
    event = threading.Event()

    def on_connect(client, userdata, flags, rc, props):
        if not rc.is_failure:
            result["connected"] = True
        else:
            result["error"] = str(rc)
        event.set()

    try:
        cid = f"broker_test_{secrets.token_hex(8)}"
        test_client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=cid,
            clean_session=True,
        )
        if user and user.strip() and user.lower() != "anonymous":
            test_client.username_pw_set(user, password)

        if use_tls:
            test_client.tls_set(ca_certs=ca_certs, certfile=certfile, keyfile=keyfile)
            test_client.tls_insecure_set(tls_insecure)

        test_client.on_connect = on_connect
        test_client.connect(broker, port, keepalive=30)
        test_client.loop_start()

        if event.wait(timeout=timeout):
            if result["connected"]:
                return True, None
            return False, result["error"] or "Connection failed"
        return False, f"Connection timeout after {timeout}s"

    except Exception as e:
        return False, str(e)
    finally:
        if test_client:
            try:
                test_client.loop_stop()
                test_client.disconnect()
            except Exception:
                pass


@router.get("/config/broker", response_model=BrokerConfigResponse)
async def get_broker_settings(
    role: Role = Security(optional_auth()),
) -> BrokerConfigResponse:
    cfg = _load_broker_config()
    return BrokerConfigResponse.model_validate(cfg)


@router.get("/config/broker/password")
async def get_broker_password(
    role: Role = Security(require_auth(Role.SERVICE)),
) -> JSONResponse:
    """Return the MQTT password. Requires SERVICE role or higher."""
    cfg = _load_broker_config()
    return JSONResponse(content={"mqtt_password": cfg["mqtt_password"]})


@router.put("/config/broker", response_model=BrokerConfigResponse)
async def update_broker_settings(
    update: BrokerConfigUpdate,
    role: Role = Security(require_auth(Role.WRITE)),
) -> BrokerConfigResponse:
    async with _broker_update_lock:
        return await _do_update_broker(update)


async def _do_update_broker(update: BrokerConfigUpdate) -> BrokerConfigResponse:
    current = _load_broker_config()
    updates: dict[str, str] = {}

    if update.broker is not None:
        updates["MQTT_BROKER"] = update.broker
    if update.broker_port is not None:
        updates["MQTT_PORT"] = str(update.broker_port)
    if update.mqtt_user is not None:
        updates["MQTT_USER"] = update.mqtt_user
    if update.mqtt_password is not None:
        updates["MQTT_PASS"] = update.mqtt_password
    if update.mqtt_use_tls is not None:
        updates["MQTT_USE_TLS"] = str(update.mqtt_use_tls).lower()
    if update.mqtt_tls_insecure is not None:
        updates["MQTT_TLS_INSECURE"] = str(update.mqtt_tls_insecure).lower()
    if update.auth_mode is not None:
        updates["AUTH_MODE"] = update.auth_mode

    # Auto-port switching for TLS
    if update.mqtt_use_tls is not None and update.broker_port is None:
        resolved = resolve_mqtt_port(current["broker_port"], update.mqtt_use_tls)
        if resolved != current["broker_port"]:
            updates["MQTT_PORT"] = str(resolved)

    if not updates:
        return BrokerConfigResponse.model_validate(current)

    # Build target config for connection test
    target_broker = updates.get("MQTT_BROKER", current["broker"])
    target_port = int(updates.get("MQTT_PORT", current["broker_port"]))
    target_user = updates.get("MQTT_USER", current["mqtt_user"])
    target_pass = updates.get("MQTT_PASS", current["mqtt_password"])
    target_tls = updates.get("MQTT_USE_TLS", str(current["mqtt_use_tls"])).lower() == "true"
    target_insecure = updates.get("MQTT_TLS_INSECURE", str(current["mqtt_tls_insecure"])).lower() == "true"

    from config import get_settings

    settings = get_settings()
    ca = str(settings.cert_ca_file) if settings.cert_ca_file.exists() else None
    cert = str(settings.cert_client_cert_file) if settings.cert_client_cert_file.exists() else None
    key = str(settings.cert_client_key_file) if settings.cert_client_key_file.exists() else None

    success, error = await asyncio.to_thread(
        _check_mqtt_connection,
        target_broker,
        target_port,
        target_user,
        target_pass,
        DEFAULT_CONNECTION_TEST_TIMEOUT,
        use_tls=target_tls,
        ca_certs=ca,
        certfile=cert,
        keyfile=key,
        tls_insecure=target_insecure,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Broker connection test failed: {error}",
        )

    # Save to store (atomic read-modify-write)
    get_store().update(lambda data: {**data, **updates})

    logger.info("Broker config updated: broker=%s port=%s tls=%s", target_broker, target_port, target_tls)

    return BrokerConfigResponse.model_validate(_load_broker_config())
