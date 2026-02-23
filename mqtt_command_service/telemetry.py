import json
import logging
import signal
import threading
import time
from typing import Any

import requests

from app.services.config_service import get_config_service
from app.services.mqtt_client_service import UnifiedMQTTClient
from env_settings import EnvSettings, get_env_settings
from telemetry_payload import (
    build_connection_status_payload,
    build_navigation_status_payload,
    build_status_payload,
    build_system_status_payload,
    build_telemetry_payload,
)

# Thread-local HTTP session — created lazily in the telemetry thread
_thread_local = threading.local()

def _get_http_session() -> requests.Session:
    """Return a thread-local requests.Session (created on first call)."""
    if not hasattr(_thread_local, 'session'):
        _thread_local.session = requests.Session()
    session: requests.Session = _thread_local.session
    return session

logger = logging.getLogger("mqtt_telemetry_service")

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False

try:
    import psutil  # noqa: F401
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# -------------------------------------------------------------------
# Service config (HTTP /status), ROBOT level (AE.01)
# Only poll the aggregated /robot status to reduce network load.
# -------------------------------------------------------------------
def _build_services() -> dict[str, str]:
    """Build service URLs from centralized EnvSettings."""
    env = get_env_settings()
    if env.is_remote:
        return {"robot": f"http://{env.remote_address}/robot/status"}
    return {"robot": f"http://{env.local_ip}:8110/status"}


def _get_janus_endpoints() -> tuple[str, str]:
    """Return (JANUS_WS_DEPTH, JANUS_WS_COLOR) from centralized settings."""
    env = get_env_settings()
    return env.effective_janus_ws_depth, env.effective_janus_ws_color

# -------------------------------------------------------------------
# Read shared configuration from config.py (DB -> ENV -> default)
# -------------------------------------------------------------------
try:
    from config import (
        is_mqtt_auth_configured,
        load_bridge_config,
    )
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False
    logger.warning("config module not available, using ENV only")

    def is_mqtt_auth_configured(username: str | None) -> bool:
        normalized = str(username or "").strip()
        return bool(normalized) and normalized.lower() != "anonymous"

# Configuration is loaded dynamically by UnifiedMQTTClient
# These constants are kept for backward compatibility but should be removed
MQTT_QOS = 1  # Default QoS

def get_robot_id() -> str:
    """Get current robot ID from configuration (cached via ConfigService)."""
    try:
        return get_config_service().get_config().robot_id
    except Exception:
        logger.debug("ConfigService unavailable for robot_id, falling back")
    if CONFIG_AVAILABLE:
        try:
            config = load_bridge_config()
            return config.robot_id
        except Exception:
            logger.debug("load_bridge_config failed for robot_id, using env")
    return get_env_settings().robot_id


def _telemetry_env() -> EnvSettings:
    """Shortcut so module-level constants survive in existing call sites."""
    return get_env_settings()


# These were module-level constants; now thin wrappers so tests can
# override via reset_env_settings().
def _poll_interval() -> int:
    result: int = _telemetry_env().poll_interval_seconds
    return result

def _http_timeout() -> int:
    result: int = _telemetry_env().telemetry_http_timeout
    return result

def _ws_check_timeout() -> float:
    result: float = _telemetry_env().websocket_check_timeout
    return result

def _ws_check_interval() -> float:
    result: float = _telemetry_env().websocket_check_interval
    return result

# -------------------------------------------------------------------
# MQTT client (uses UnifiedMQTTClient)
# -------------------------------------------------------------------
mqtt_client: UnifiedMQTTClient | None = None
_config_service: Any | None = None  # ConfigService instance

shutdown_flag = threading.Event()  # Thread-safe shutdown flag

# WebRTC status cache
webrtc_connected = threading.Event()  # Thread-safe WebRTC flag
webrtc_last_check: float = 0.0
_webrtc_lock = threading.Lock()  # Guards webrtc_last_check


def _compact(data: Any, limit: int = 800) -> str:
    """Return a compact, truncated string representation for logging."""
    try:
        text = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        text = str(data)
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def setup_mqtt_client() -> None:
    """Set up MQTT client using UnifiedMQTTClient."""
    global mqtt_client, _config_service

    if _config_service is None:
        _config_service = get_config_service()

    if mqtt_client is None:
        mqtt_client = UnifiedMQTTClient(
            config_service=_config_service,
            component_name="telemetry",
        )
        # Register with global MQTT state for health checks
        from app.services import mqtt_state
        mqtt_state.register("telemetry", mqtt_client)


def connect_mqtt_blocking() -> bool:
    """Blocking connection to MQTT broker."""
    global mqtt_client

    if mqtt_client is None:
        setup_mqtt_client()

    assert mqtt_client is not None  # narrowed by setup_mqtt_client
    # Start client (will connect automatically)
    mqtt_client.start()

    # Wait for connection
    for _ in range(20):  # 10 seconds max
        if mqtt_client.is_connected or shutdown_flag.is_set():
            break
        time.sleep(0.5)

    if mqtt_client.is_connected:
        logger.info("[telemetry] MQTT connected successfully")
        return True
    else:
        logger.warning("[telemetry] MQTT connection timeout")
        return False


# -------------------------------------------------------------------
# HTTP → MQTT status publishing
# -------------------------------------------------------------------


def fetch_service_status(name: str, url: str) -> tuple[str, dict[str, Any] | None, str | None]:
    """Poll /status endpoint of a service, return (status, data, error)."""
    try:
        resp = _get_http_session().get(url, timeout=_http_timeout())
        http_status = resp.status_code

        if http_status != 200:
            return "error", None, f"http_status_{http_status}"

        # Try to parse JSON; fallback to text if it fails
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}

        # If 200 and response exists — consider service online
        return "online", payload, None

    except requests.exceptions.Timeout:
        return "error", None, "timeout"
    except requests.exceptions.ConnectionError:
        return "offline", None, "connection_error"
    except Exception as e:
        return "error", None, f"exception_{type(e).__name__}"


def check_webrtc_connection() -> bool:
    """
    Check WebRTC availability by pinging Janus WebSocket connections.
    Checks both endpoints (depth and color), returns True if at least one is reachable.
    """
    if not WEBSOCKET_AVAILABLE:
        return False

    # Check at most once per websocket_check_interval seconds
    now = time.time()
    global webrtc_last_check  # Explicitly declare global variable usage
    with _webrtc_lock:
        if now - webrtc_last_check < _ws_check_interval():
            return webrtc_connected.is_set()

        webrtc_last_check = now

    # Check both WebSocket endpoints
    janus_depth, janus_color = _get_janus_endpoints()
    endpoints = [janus_depth, janus_color]
    connected = False

    for ws_url in endpoints:
        try:
            # Try connecting to WebSocket with a short timeout
            sslopt = {}
            if ws_url.startswith("wss://"):
                import ssl
                if _telemetry_env().websocket_tls_insecure:
                    sslopt = {"cert_reqs": ssl.CERT_NONE}
                else:
                    sslopt = {"cert_reqs": ssl.CERT_REQUIRED}

            ws = websocket.create_connection(
                ws_url,
                timeout=_ws_check_timeout(),
                sslopt=sslopt
            )
            # Connection successful — close immediately
            ws.close()
            connected = True
            logger.debug("WebRTC WebSocket check: %s is accessible", ws_url)
            break  # One working connection is enough
        except websocket.WebSocketTimeoutException:
            logger.debug("WebRTC WebSocket check timeout for %s", ws_url)
            continue
        except Exception as e:
            logger.debug("WebRTC WebSocket check failed for %s: %s", ws_url, e)
            continue

    # Thread-safe WebRTC flag update
    if connected:
        webrtc_connected.set()
    else:
        webrtc_connected.clear()
    return connected


def publish_mqtt(topic: str, payload: dict[str, Any], log_name: str = "data") -> bool:
    """Universal MQTT publish with QoS=1, no retain."""
    global mqtt_client

    if mqtt_client is None:
        logger.warning("[telemetry] MQTT client not initialized, skip publish to '%s'", topic)
        return False

    if not mqtt_client.is_connected:
        # UnifiedMQTTClient handles throttling, so we don't need to log every time
        return False

    # Validate payload size
    message = json.dumps(payload, separators=(",", ":"))
    message_bytes = message.encode('utf-8')

    try:
        from constants import MAX_MQTT_PAYLOAD_SIZE
        if len(message_bytes) > MAX_MQTT_PAYLOAD_SIZE:
            logger.error(
                "[telemetry] Payload too large for topic %s: %d bytes (max %d)",
                topic, len(message_bytes), MAX_MQTT_PAYLOAD_SIZE
            )
            return False
    except ImportError:
        MAX_MQTT_PAYLOAD_SIZE = 1024 * 1024  # 1MB
        if len(message_bytes) > MAX_MQTT_PAYLOAD_SIZE:
            logger.error("[telemetry] Payload too large for topic %s", topic)
            return False

    # Publish via UnifiedMQTTClient
    return mqtt_client.publish(topic, payload, qos=MQTT_QOS, retain=False)


def publish_service_status(service_name: str, payload: dict) -> None:
    """Publish to MQTT with QoS=1, no retain (avoid stale status)."""
    topic = f"aroc/robot/{get_robot_id()}/status/{service_name}"
    publish_mqtt(topic, payload, f"status for '{service_name}'")


def telemetry_loop() -> None:
    """
    Main service polling and MQTT publishing loop.
    Publishes data per SRS 4.1:
    - status/navigation - navigation status
    - status/system - system metrics (CPU, RAM, battery)
    - status/connection - connection state (WebRTC, MQTT)
    - telemetry - sensor data
    """
    global mqtt_client, _config_service

    if _config_service is None:
        _config_service = get_config_service()

    if mqtt_client is None:
        setup_mqtt_client()
        connect_mqtt_blocking()

    logger.info("[telemetry] Starting telemetry loop, poll interval=%s s", _poll_interval())

    _RESTART_DELAY = 5  # seconds before restarting after crash

    while not shutdown_flag.is_set():
      try:
        start_ts = time.time()

        # Config changes are handled automatically by UnifiedMQTTClient
        # No need to periodically reload config

        # Collect data from all services
        service_data: dict[str, tuple[str, Any, str | None]] = {}
        for name, url in _build_services().items():
            status, data, error = fetch_service_status(name, url)
            service_data[name] = (status, data, error)
            logger.debug(
                "Fetched status for %s: status=%s error=%s",
                name,
                status,
                error,
            )

        # Extract data for telemetry
        _robot_status, robot_data, _robot_error = service_data.get("robot", ("offline", None, None))
        _symovo_status, symovo_data, _symovo_error = service_data.get("symovo", ("offline", None, None))

        # If symovo_data is empty but robot_data contains nested symovo — use it
        if (not symovo_data or not isinstance(symovo_data, dict)) and robot_data and isinstance(robot_data, dict):
            nested_symovo = robot_data.get("symovo")
            if nested_symovo and isinstance(nested_symovo, dict):
                logger.debug("Using nested symovo data from robot status")
                symovo_data = nested_symovo

        # Publish status/navigation (per SRS 4.1)
        if robot_data and symovo_data and isinstance(robot_data, dict) and isinstance(symovo_data, dict):
            nav_payload = build_navigation_status_payload(get_robot_id(), robot_data, symovo_data)
            if nav_payload:
                topic = f"aroc/robot/{get_robot_id()}/status/navigation"
                if publish_mqtt(topic, nav_payload, "navigation status"):
                    logger.debug(
                        "Published navigation status: status=%s, current_position=%s, lift_position=%s, xarm_position=%s, xarm_joints=%s",
                        nav_payload.get("status"),
                        _compact(nav_payload.get("current_position")),
                        _compact(nav_payload.get("lift_position")),
                        _compact(nav_payload.get("xarm_position")),
                        _compact(nav_payload.get("xarm_joints")),
                    )
            else:
                logger.warning("Could not build navigation status payload")

        # Publish status/system (per SRS 4.1)
        if symovo_data and isinstance(symovo_data, dict):
            sys_payload = build_system_status_payload(get_robot_id(), symovo_data)
            if sys_payload:
                topic = f"aroc/robot/{get_robot_id()}/status/system"
                if publish_mqtt(topic, sys_payload, "system status"):
                    # Build metrics string
                    metrics = []
                    if sys_payload.get("cpu") is not None:
                        metrics.append(f"cpu={sys_payload.get('cpu')}%")
                    if sys_payload.get("ram") is not None:
                        metrics.append(f"ram={sys_payload.get('ram')}%")
                    if sys_payload.get("battery") is not None:
                        metrics.append(f"battery={sys_payload.get('battery')}%")
                    if sys_payload.get("disk") is not None:
                        metrics.append(f"disk={sys_payload.get('disk')}%")
                    if sys_payload.get("uptime_seconds") is not None:
                        uptime_val: float = float(sys_payload["uptime_seconds"])
                        uptime_hours = uptime_val / 3600.0
                        metrics.append(f"uptime={uptime_hours:.1f}h")

                    logger.debug(
                        "Published system status: %s",
                        ", ".join(metrics) if metrics else "no metrics",
                    )

        # Publish status/connection (per SRS 4.1)
        webrtc_ok = check_webrtc_connection()
        mqtt_ok = mqtt_client.is_connected if mqtt_client else False
        conn_payload = build_connection_status_payload(get_robot_id(), webrtc_ok, mqtt_ok)
        topic = f"aroc/robot/{get_robot_id()}/status/connection"
        if publish_mqtt(topic, conn_payload, "connection status"):
            logger.debug("Published connection status: webrtc=%s, mqtt=%s",
                       conn_payload.get("webrtc"),
                       conn_payload.get("mqtt"))

        # Publish telemetry (per SRS 4.1)
        if robot_data and symovo_data and isinstance(robot_data, dict) and isinstance(symovo_data, dict):
            telem_payload = build_telemetry_payload(get_robot_id(), robot_data, symovo_data)
            if telem_payload:
                topic = f"aroc/robot/{get_robot_id()}/telemetry"
                if publish_mqtt(topic, telem_payload, "telemetry"):
                    data = telem_payload.get("data", {})
                    components = telem_payload.get("components", {})
                    logger.debug(
                        "Published telemetry: data=%s, components=%s",
                        _compact(data),
                        _compact(components) if components else "None",
                    )

        # Also publish legacy statuses for backward compatibility
        for name, (status, data, error) in service_data.items():
            legacy_payload = build_status_payload(get_robot_id(), name, status, data, error)
            publish_service_status(name, legacy_payload)

        elapsed = time.time() - start_ts
        sleep_time = max(0, _poll_interval() - elapsed)

        if sleep_time > 0:
            time.sleep(sleep_time)
      except Exception:
        logger.exception("[telemetry] Unhandled error in telemetry loop, restarting in %ds", _RESTART_DELAY)
        if not shutdown_flag.is_set():
            time.sleep(_RESTART_DELAY)


# -------------------------------------------------------------------
# SIGINT/SIGTERM handling
# -------------------------------------------------------------------
def handle_signal(signum: int, frame: Any) -> None:
    logger.info("[telemetry] Received signal %s, shutting down...", signum)
    shutdown_flag.set()
    if mqtt_client:
        mqtt_client.stop()


def main() -> None:
    global mqtt_client

    logger.info("[telemetry] Starting MQTT Telemetry Service for robot_id=%s", get_robot_id())

    if not WEBSOCKET_AVAILABLE:
        logger.warning(
            "[telemetry] websocket-client library not available. "
            "Install it with: pip install websocket-client. "
            "WebRTC status will always be False."
        )
    if not PSUTIL_AVAILABLE:
        logger.warning(
            "[telemetry] psutil not available. "
            "Install it with: pip install psutil. "
            "CPU/RAM metrics will be None."
        )

    setup_mqtt_client()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    connect_mqtt_blocking()

    if not shutdown_flag.is_set():
        telemetry_loop()

    # Cleanup
    if mqtt_client:
        mqtt_client.stop()

    logger.info("[telemetry] Telemetry service stopped.")


if __name__ == "__main__":
    main()
