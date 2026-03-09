import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from paho.mqtt import client as mqtt_client
from shared_config.network import PORTS

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path, override=False)

logger = logging.getLogger(__name__)

try:
    from config_storage import (
        get_config_from_db,
        save_config_to_db,
    )
    DB_AVAILABLE = True
except ImportError:
    logger.warning("config_storage not available, using env variables only")
    DB_AVAILABLE = False


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    base_url: str
    watch_tasks: bool = False


@dataclass(frozen=True)
class HubAuthSettings:
    base_url: str
    robot_id: str | None
    api_key: str | None
    refresh_margin: float = 60.0


@dataclass(frozen=True)
class BridgeConfig:
    broker: str
    broker_port: int
    mqtt_user: str
    mqtt_password: str
    robot_id: str
    client_id: str
    http_timeout: float
    task_poll_interval: float
    task_poll_timeout: float
    services: dict[str, ServiceConfig] = field(default_factory=dict)
    hub_auth: HubAuthSettings | None = None
    status_heartbeat_interval: float = 15.0
    mqtt_publish_qos: int = 0
    mqtt_use_tls: bool = False
    mqtt_ca_certs: str | None = None  # path to CA certificate
    mqtt_certfile: str | None = None   # path to client cert (for mutual TLS)
    mqtt_keyfile: str | None = None    # path to client key (for mutual TLS)
    mqtt_tls_insecure: bool = False       # disable certificate verification (dev/testing only)

    @property
    def cmd_topic_pattern(self) -> str:
        return f"aroc/robot/{self.robot_id}/cmd/+"

    @property
    def resp_base_topic(self) -> str:
        return f"aroc/robot/{self.robot_id}/resp"

    @property
    def command_topic_pattern(self) -> str:
        return f"aroc/robot/{self.robot_id}/commands/+"

    @property
    def status_base_topic(self) -> str:
        return f"aroc/robot/{self.robot_id}/status"

    @property
    def config_topic_pattern(self) -> str:
        return f"aroc/robot/{self.robot_id}/config/+"


def is_mqtt_auth_configured(username: str | None) -> bool:
    """
    Returns True when MQTT credentials should be set on the client.
    Empty usernames or the sentinel value 'anonymous' mean no auth.
    """
    if username is None:
        return False
    normalized = str(username).strip()
    return bool(normalized) and normalized.lower() != "anonymous"


def load_service_configs(local_ip: str, depth_camera_ip: str) -> dict[str, ServiceConfig]:
    from env_settings import get_env_settings
    env = get_env_settings()

    api_port = env.api_port

    remote_defaults: dict[str, dict[str, Any]] = {
        "robot": {
            "base_url": env.robot_service_url,
            "watch_tasks": True,
        },
        "igus": {"base_url": env.igus_service_url},
        "xarm": {"base_url": env.xarm_service_url},
        "symovo": {"base_url": env.symovo_service_url},
        "mqtt": {"base_url": f"http://localhost:{api_port}"},
    }
    default_services = dict(remote_defaults)

    if env.service_use_local:
        default_services.update(
            {
                "robot": {"base_url": f"http://{local_ip}:{PORTS.ROBOT}", "watch_tasks": True},
                "igus": {"base_url": f"http://{local_ip}:{PORTS.IGUS}"},
                "xarm": {"base_url": f"http://{local_ip}:{PORTS.XARM}"},
                "symovo": {"base_url": f"http://{local_ip}:{PORTS.SYMOVO}"},
                "color_camera": {"base_url": f"http://{local_ip}:{PORTS.COLOR_CAMERA}"},
                "depth_camera": {"base_url": f"http://{depth_camera_ip}:{PORTS.COLOR_CAMERA}"},
                "mqtt": {"base_url": f"http://localhost:{api_port}"},
            }
        )

    override_raw = env.service_map_json
    if override_raw:
        try:
            override_map = json.loads(override_raw)
            if isinstance(override_map, dict):
                for name, cfg in override_map.items():
                    if isinstance(cfg, dict) and "base_url" in cfg:
                        default_services[name] = {
                            "base_url": cfg["base_url"],
                            "watch_tasks": bool(cfg.get("watch_tasks", False)),
                        }
                    elif isinstance(cfg, str):
                        default_services[name] = {"base_url": cfg}
                    else:
                        logger.warning(
                            "Skip invalid SERVICE_MAP_JSON entry for '%s'", name
                        )
            else:
                logger.warning("SERVICE_MAP_JSON must describe an object, ignoring.")
        except json.JSONDecodeError:
            logger.warning("Failed to decode SERVICE_MAP_JSON override, ignoring.")

    services: dict[str, ServiceConfig] = {}
    for name, cfg in default_services.items():
        base_url = str(cfg["base_url"]).rstrip("/")
        services[name] = ServiceConfig(
            name=name,
            base_url=base_url,
            watch_tasks=bool(cfg.get("watch_tasks", False)),
        )
    return services


def _get_config_value(key: str, default: str, use_db: bool = True) -> str:
    """
    Get configuration value with priority: DB -> ENV -> default.

    Args:
        key: Configuration key
        default: Default value
        use_db: Whether to use DB (for dynamic settings)

    Returns:
        Configuration value
    """
    if use_db and DB_AVAILABLE:
        value = get_config_from_db(key)
        if value is not None:
            return value

    return os.environ.get(key, default)


def _get_config_value_bool(key: str, default: bool, use_db: bool = True) -> bool:
    """
    Get boolean configuration value with priority: DB -> ENV -> default.

    Args:
        key: Configuration key
        default: Default value
        use_db: Whether to use DB (for dynamic settings)

    Returns:
        Boolean configuration value
    """
    if use_db and DB_AVAILABLE:
        value = get_config_from_db(key)
        if value is not None:
            return value.lower() in ("true", "1", "yes", "on")

    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value.lower() in ("true", "1", "yes", "on")

    return default


_migration_done = False
_migration_lock = threading.Lock()

def migrate_env_to_db() -> None:
    """
    Migrates values from environment variables to DB on first startup.
    Runs exactly once per process lifetime.
    """
    global _migration_done
    if _migration_done or not DB_AVAILABLE:
        return
    with _migration_lock:
        if _migration_done:
            return  # type: ignore[unreachable]  # double-checked locking

        # List of dynamic settings that should be stored in DB
        dynamic_config_keys = [
            "MQTT_BROKER",
            "MQTT_PORT",
            "MQTT_USER",
            "MQTT_PASS",
            "MQTT_USE_TLS",
            "MQTT_CA_CERTS",
            "MQTT_CERTFILE",
            "MQTT_KEYFILE",
            "MQTT_TLS_INSECURE",
        ]

        migrated_count = 0
        try:
            for key in dynamic_config_keys:
                env_value = os.environ.get(key)
                if env_value:
                    # Skip migration of placeholder/test values
                    if "/path/to" in env_value or env_value.strip() == "":
                        logger.debug("Skipping migration of %s - placeholder value: %s", key, env_value)
                        continue

                    # Check if value already exists in DB
                    db_value = get_config_from_db(key)
                    if db_value is None:
                        # Migrate from env to DB
                        if save_config_to_db(
                            key,
                            env_value,
                            updated_by="migration",
                            reason="Initial migration from environment variables"
                        ):
                            migrated_count += 1
                            logger.info("Migrated %s from env to DB", key)
        except Exception:
            logger.exception("Error during env-to-DB migration")
            return  # Don't set _migration_done so it retries

        _migration_done = True

        if migrated_count > 0:
            logger.info("Migration completed: %d values migrated to DB", migrated_count)


def load_bridge_config() -> BridgeConfig:
    # Migrate values from env to DB on first startup
    migrate_env_to_db()

    from env_settings import get_env_settings
    env = get_env_settings()

    # Load settings: priority DB -> ENV -> default
    # Dynamic settings (can be changed via MQTT/API)
    from constants import DEFAULT_MQTT_BROKER, DEFAULT_MQTT_PASSWORD, DEFAULT_MQTT_USER, DEFAULT_ROBOT_ID
    broker = _get_config_value("MQTT_BROKER", DEFAULT_MQTT_BROKER, use_db=True)
    from constants import MQTT_DEFAULT_PORT
    broker_port = int(_get_config_value("MQTT_PORT", str(MQTT_DEFAULT_PORT), use_db=True))
    mqtt_user = _get_config_value("MQTT_USER", DEFAULT_MQTT_USER, use_db=True)
    mqtt_password = _get_config_value("MQTT_PASS", DEFAULT_MQTT_PASSWORD, use_db=True)

    # Static settings from centralized EnvSettings
    robot_id = _get_config_value("ROBOT_ID", DEFAULT_ROBOT_ID, use_db=False)
    client_id = env.mqtt_client_id or robot_id
    http_timeout = env.http_timeout
    task_poll_interval = env.task_poll_interval
    task_poll_timeout = env.task_poll_timeout

    local_ip = env.local_ip
    depth_camera_ip = env.effective_depth_camera_ip
    services = load_service_configs(local_ip, depth_camera_ip)

    hub_auth: HubAuthSettings | None = None
    if env.hub_base_url:
        hub_auth = HubAuthSettings(
            base_url=env.hub_base_url.rstrip("/"),
            robot_id=env.hub_robot_id,
            api_key=env.hub_api_key,
            refresh_margin=env.hub_auth_refresh_margin,
        )

    status_heartbeat_interval = env.status_heartbeat_interval
    mqtt_publish_qos = env.mqtt_publish_qos

    # TLS settings (read from DB with fallback to ENV)
    mqtt_use_tls = _get_config_value_bool("MQTT_USE_TLS", False, use_db=True)
    # Certificate paths are fixed - always use certs/ directory in application root
    from constants import CERT_CA_FILE, CERT_CLIENT_CERT_FILE, CERT_CLIENT_KEY_FILE, MQTT_TLS_PORT
    mqtt_ca_certs = str(CERT_CA_FILE) if CERT_CA_FILE.exists() else None
    mqtt_certfile = str(CERT_CLIENT_CERT_FILE) if CERT_CLIENT_CERT_FILE.exists() else None
    mqtt_keyfile = str(CERT_CLIENT_KEY_FILE) if CERT_CLIENT_KEY_FILE.exists() else None
    mqtt_tls_insecure = _get_config_value_bool("MQTT_TLS_INSECURE", False, use_db=True)

    # Auto-adjust port based on TLS setting (read-only — no DB write here).
    # The corrected port is only used transiently; the operator should
    # explicitly save the desired port via the API.
    if mqtt_use_tls and broker_port == MQTT_DEFAULT_PORT:
        logger.info(
            "TLS enabled but port is %d (default). Using TLS port %d for this session",
            broker_port,
            MQTT_TLS_PORT
        )
        broker_port = MQTT_TLS_PORT
    elif not mqtt_use_tls and broker_port == MQTT_TLS_PORT:
        logger.info(
            "TLS disabled but port is %d (TLS port). Using default port %d for this session",
            broker_port,
            MQTT_DEFAULT_PORT
        )
        broker_port = MQTT_DEFAULT_PORT

    return BridgeConfig(
        broker=broker,
        broker_port=broker_port,
        mqtt_user=mqtt_user,
        mqtt_password=mqtt_password,
        robot_id=robot_id,
        client_id=client_id,
        http_timeout=http_timeout,
        task_poll_interval=task_poll_interval,
        task_poll_timeout=task_poll_timeout,
        services=services,
        hub_auth=hub_auth,
        status_heartbeat_interval=status_heartbeat_interval,
        mqtt_publish_qos=max(0, min(2, mqtt_publish_qos)),
        mqtt_use_tls=mqtt_use_tls,
        mqtt_ca_certs=mqtt_ca_certs,
        mqtt_certfile=mqtt_certfile,
        mqtt_keyfile=mqtt_keyfile,
        mqtt_tls_insecure=mqtt_tls_insecure,
    )


def save_config_variable(
    key: str,
    value: str,
    updated_by: str = "system",
    reason: str | None = None
) -> bool:
    """
    Save configuration variable to DB (preferred) or .env file (fallback).

    Args:
        key: Configuration key
        value: Value
        updated_by: Who made the change (e.g., "mqtt", "api", "system")
        reason: Reason for change
    """
    # List of keys that should be stored in DB
    db_keys = [
        "MQTT_BROKER", "MQTT_PORT", "MQTT_USER", "MQTT_PASS",
        "MQTT_USE_TLS", "MQTT_CA_CERTS", "MQTT_CERTFILE", "MQTT_KEYFILE", "MQTT_TLS_INSECURE"
    ]

    # Try to save to DB for dynamic settings
    if key in db_keys and DB_AVAILABLE:
        success = save_config_to_db(key, value, updated_by=updated_by, reason=reason)
        if success:
            logger.info("Saved %s to database (by %s)", key, updated_by)
            return True
        else:
            logger.error("Failed to save %s to database", key)
            return False

    # For non-DB keys, log a warning — os.environ is not mutated to
    # avoid thread-safety issues.  Callers should rely on the DB /
    # ConfigService for dynamic configuration.
    logger.warning("Key %s is not a DB-managed setting; ignoring save (by %s)", key, updated_by)
    return False


def get_broker_config() -> dict[str, Any]:
    """Returns current broker settings and credentials (from DB or env).

    Delegates to ConfigService so that there is exactly one read-path for
    configuration.  The returned dict keeps the legacy key names that
    existing callers expect.
    """
    from app.services.config_service import get_config_service
    cfg = get_config_service().get_config()
    return {
        "MQTT_BROKER": cfg.broker,
        "MQTT_PORT": cfg.broker_port,
        "mqtt_user": cfg.mqtt_user,
        "mqtt_password": cfg.mqtt_password,
        "mqtt_use_tls": cfg.mqtt_use_tls,
        "mqtt_ca_certs": cfg.mqtt_ca_certs,
        "mqtt_certfile": cfg.mqtt_certfile,
        "mqtt_keyfile": cfg.mqtt_keyfile,
        "mqtt_tls_insecure": cfg.mqtt_tls_insecure,
    }


def test_mqtt_connection(
    broker: str,
    broker_port: int,
    mqtt_user: str,
    mqtt_password: str,
    timeout: float = 5.0,
    use_tls: bool = False,
    ca_certs: str | None = None,
    certfile: str | None = None,
    keyfile: str | None = None,
    tls_insecure: bool = False,
) -> tuple[bool, str | None]:
    """
    Test connection to MQTT broker with given credentials.

    Returns:
        (success: bool, error_message: Optional[str]):
            (True, None) if connection successful,
            (False, error_message) otherwise
    """
    test_client = None
    connection_result: dict[str, Any] = {"connected": False, "error": None}
    connection_event = threading.Event()

    def on_connect(client, userdata, flags, rc):  # type: ignore
        if rc == 0:
            connection_result["connected"] = True
        else:
            error_msgs = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorised",
            }
            connection_result["error"] = error_msgs.get(rc, f"Connection failed with code {rc}")
        connection_event.set()

    def on_disconnect(client, userdata, rc):  # type: ignore
        pass

    try:
        test_client_id = f"broker_test_{int(time.time())}"
        test_client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION1,
            client_id=test_client_id,
            clean_session=True,
        )
        if is_mqtt_auth_configured(mqtt_user):
            test_client.username_pw_set(mqtt_user, mqtt_password)

        # TLS configuration for test
        if use_tls:
            try:
                test_client.tls_set(
                    ca_certs=ca_certs,
                    certfile=certfile,
                    keyfile=keyfile,
                )
                test_client.tls_insecure_set(tls_insecure)
            except Exception as e:
                error_msg = f"Failed to configure TLS for test connection: {e!s}"
                logger.error(error_msg)
                return False, error_msg

        test_client.on_connect = on_connect
        test_client.on_disconnect = on_disconnect

        protocol = "mqtts" if use_tls else "mqtt"
        logger.info(
            "Testing connection to broker %s://%s:%s with user %s",
            protocol,
            broker,
            broker_port,
            mqtt_user,
        )

        test_client.connect(broker, broker_port, keepalive=30)
        test_client.loop_start()

        # Wait for connection result
        if connection_event.wait(timeout=timeout):
            if connection_result["connected"]:
                logger.info("Successfully connected to test broker")
                # Keep connection for a bit to ensure it's stable
                time.sleep(0.5)
                return True, None
            else:
                error_msg = connection_result["error"] or "Connection failed"
                logger.warning("Failed to connect to test broker: %s", error_msg)
                return False, error_msg
        else:
            error_msg = f"Connection timeout after {timeout}s"
            logger.warning(error_msg)
            return False, error_msg

    except Exception as e:
        error_msg = f"Exception during connection test: {e!s}"
        logger.error(error_msg)
        return False, error_msg
    finally:
        if test_client:
            try:
                test_client.loop_stop()
                test_client.disconnect()
            except Exception:
                pass


# Prevent pytest from collecting helper as a test
test_mqtt_connection.__test__ = False  # type: ignore[attr-defined]
