import os
import yaml

from shared_config.network import DEVICES, PORTS


def _load_config_file(path: str = os.getenv("APP_CONFIG_FILE", "/app/config.yaml")) -> dict:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CFG = _load_config_file()


def _get(key: str, default: str) -> str:
    return os.getenv(key, str(_CFG.get(key.lower(), default)))


server_ip = _get("SERVER_IP", DEVICES.HOST_LAN_IP)

# External service addresses (prefer env; fallback to config.yaml; then default)
IGUS_CONTAINER_IP = _get("IGUS_CONTAINER_IP", server_ip)
IGUS_CONTAINER_PORT = _get("IGUS_CONTAINER_PORT", str(PORTS.IGUS))

XARM_CONTAINER_IP = _get("XARM_CONTAINER_IP", server_ip)
XARM_CONTAINER_PORT = _get("XARM_CONTAINER_PORT", str(PORTS.XARM))

DEPTH_CAMERA_CONTAINER_IP = _get("DEPTH_CAMERA_CONTAINER_IP", DEVICES.DEPTH_CAMERA_IP)
DEPTH_CAMERA_CONTAINER_PORT = _get("DEPTH_CAMERA_CONTAINER_PORT", "8900")

# Joystick defaults (HTTP)
JOYSTICK_DEADZONE = float(_get("JOYSTICK_DEADZONE", "0.2"))
JOYSTICK_DEFAULT_TTL_MS = int(_get("JOYSTICK_DEFAULT_TTL_MS", "100"))
JOYSTICK_HOLD_TIMEOUT_MS = int(_get("JOYSTICK_HOLD_TIMEOUT_MS", "2000"))
JOYSTICK_MOVE_ACC = int(_get("JOYSTICK_MOVE_ACC", "1000"))
JOYSTICK_IS_MOVE_TOOL = _get("JOYSTICK_IS_MOVE_TOOL", "true")
JOYSTICK_MODE = int(_get("JOYSTICK_MODE", "0"))
JOYSTICK_LIFT_JOG_SPEED = float(_get("JOYSTICK_LIFT_JOG_SPEED", "2000"))
JOYSTICK_LIFT_JOG_TTL_MS = int(_get("JOYSTICK_LIFT_JOG_TTL_MS", "200"))
XARM_STATUS_CACHE_TTL_SEC = float(_get("XARM_STATUS_CACHE_TTL_SEC", "3.0"))

# Symovo teleop (AGV drive via joystick buttons 4/5/6/7 → W/S/A/D)
# Ranges match API (models.api_types) so startup values are always valid
_SYMOVO_DURATION_MIN, _SYMOVO_DURATION_MAX = 0.05, 2.0
_SYMOVO_LINEAR_MIN, _SYMOVO_LINEAR_MAX = 0.01, 1.0
_SYMOVO_ANGULAR_MIN, _SYMOVO_ANGULAR_MAX = 0.01, 2.0

# Teleop: main API at nav2adapter (host:7905). move/speed = /api/v1/robots/<robot_id>/move/speed; drive_mode = /drive_mode.
# SYMOVO_NAV2ADAPTER_HOST = where nav2adapter runs (e.g. 192.168.1.10). SYMOVO_ROBOT_ID = robot id in nav2adapter (e.g. fahrdummy-01).
SYMOVO_NAV2ADAPTER_HOST = _get("SYMOVO_NAV2ADAPTER_HOST", DEVICES.HOST_LAN_IP)
SYMOVO_ROBOT_ID = _get("SYMOVO_ROBOT_ID", "fahrdummy-01")
SYMOVO_TELEOP_MOVE_URL = _get(
    "SYMOVO_TELEOP_MOVE_URL",
    f"http://{SYMOVO_NAV2ADAPTER_HOST}:7905/api/v1/robots/{SYMOVO_ROBOT_ID}/move/speed",
)
# Drive mode (enable/disable motors) — required for teleop; see symovo_teleop.txt §2.2
SYMOVO_DRIVE_MODE_URL = _get("SYMOVO_DRIVE_MODE_URL", f"http://{SYMOVO_NAV2ADAPTER_HOST}:7905/drive_mode")
SAFETY_STATE_URL = _get("SAFETY_STATE_URL", f"http://{SYMOVO_NAV2ADAPTER_HOST}:7905/safety/state")
SYMOVO_TELEOP_DURATION = min(max(float(_get("SYMOVO_TELEOP_DURATION", "0.25")), _SYMOVO_DURATION_MIN), _SYMOVO_DURATION_MAX)
SYMOVO_TELEOP_LINEAR = min(max(float(_get("SYMOVO_TELEOP_LINEAR", "0.1")), _SYMOVO_LINEAR_MIN), _SYMOVO_LINEAR_MAX)
SYMOVO_TELEOP_ANGULAR = min(max(float(_get("SYMOVO_TELEOP_ANGULAR", "0.5")), _SYMOVO_ANGULAR_MIN), _SYMOVO_ANGULAR_MAX)