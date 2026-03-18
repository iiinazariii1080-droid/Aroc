"""Payload builder functions for telemetry MQTT messages.

Public payload builders (``build_*``) accept data dicts and return
payload dicts (or ``None`` when required data is missing).

:func:`collect_host_metrics` is the exception — it performs I/O via
psutil and should be called from the service loop, not from other
payload builders.
"""

import logging
import math
import time
from typing import Any

import psutil

from shared.utils import now_iso

logger = logging.getLogger(__name__)

# xArm report-data array indices (see xArm SDK: xarm.get_report_data())
XARM_DATA_JOINTS_INDEX = 18
XARM_DATA_COORDS_INDEX = 19
_XARM_DATA_MIN_LENGTH = XARM_DATA_COORDS_INDEX + 1


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert value to float, returning *default* on None or conversion error."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _require_float(value: Any, field_name: str) -> float | None:
    """Convert value to float for **critical** fields (coordinates, angles, battery).

    Returns ``None`` when *value* is ``None`` or unconvertible — never a
    misleading default like ``0.0``.  Downstream consumers must handle
    ``None`` explicitly (e.g. skip the payload or display "unknown").
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(
            "Failed to convert field %r to float: %r — returning None",
            field_name,
            value,
        )
        return None


def _safe_int_or_none(value: Any) -> int | None:
    """Convert *value* to ``int`` if it is a numeric non-bool type, else ``None``.

    Handles the Python quirk where ``bool`` is a subclass of ``int``
    (``isinstance(True, int)`` is ``True``), which would otherwise let
    ``True``/``False`` leak through as ``1``/``0``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _parse_omega(velocity: dict[str, Any]) -> float | None:
    """Extract angular velocity (omega) in rad/s from *velocity* dict.

    Prefers ``omega_rad_s`` (already in radians).  Falls back to
    ``omega_deg_s`` (converted via ``math.radians``).  Returns ``None``
    when no valid value is available.
    """
    if "omega_rad_s" in velocity:
        return _require_float(velocity.get("omega_rad_s"), "velocity.omega_rad_s")
    deg = _require_float(velocity.get("omega_deg_s"), "velocity.omega_deg_s")
    if deg is None:
        return None
    return math.radians(deg)


def warmup_psutil() -> None:
    """Discard the first (meaningless) cpu_percent reading."""
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        logger.warning("psutil warmup failed — host metrics may be unavailable", exc_info=True)


# ------------------------------------------------------------------
# Shared component parsers
# ------------------------------------------------------------------


def _get_igus_position(igus_data: dict[str, Any]) -> Any:
    """Return raw position value from igus data, trying ``position_cm`` then ``position``."""
    value = igus_data.get("position_cm")
    if value is None:
        value = igus_data.get("position")
    return value


def _parse_igus_lift_position(igus_data: dict[str, Any]) -> dict[str, float] | None:
    """Extract lift position from igus data (used by navigation payload)."""
    position_cm = _get_igus_position(igus_data)
    if isinstance(position_cm, (int, float)):
        return {"height": float(position_cm)}
    return None


def _parse_igus_status(igus_data: dict[str, Any]) -> dict[str, Any]:
    """Extract igus status (used by telemetry payload)."""
    position_cm = _get_igus_position(igus_data)
    return {
        "connected": bool(igus_data.get("connected", False)),
        "homed": bool(igus_data.get("homed", False)),
        "is_moving": bool(igus_data.get("is_moving", False)),
        "error": bool(igus_data.get("error", False)),
        "position_cm": _safe_float(position_cm) if position_cm is not None else None,
    }


def _parse_xarm_position_and_joints(
    xarm_data: dict[str, Any],
) -> tuple[dict[str, float] | None, dict[str, float] | None]:
    """Extract xarm TCP position and joint angles from xarm data array."""
    data = xarm_data.get("data")
    if not isinstance(data, (list, tuple)) or len(data) < _XARM_DATA_MIN_LENGTH:
        if isinstance(data, (list, tuple)):
            logger.warning(
                "xArm data array too short: got %d elements, expected >= %d",
                len(data),
                _XARM_DATA_MIN_LENGTH,
            )
        return None, None

    xarm_position = None
    coords = data[XARM_DATA_COORDS_INDEX]
    if isinstance(coords, (list, tuple)) and len(coords) >= 3:
        xarm_position = {
            "x": _require_float(coords[0], "xarm.coords.x"),
            "y": _require_float(coords[1], "xarm.coords.y"),
            "z": _require_float(coords[2], "xarm.coords.z"),
        }

    xarm_joints = None
    joints = data[XARM_DATA_JOINTS_INDEX]
    if isinstance(joints, (list, tuple)) and len(joints) >= 6:
        xarm_joints = {
            "j1": _require_float(joints[0], "xarm.joints.j1"),
            "j2": _require_float(joints[1], "xarm.joints.j2"),
            "j3": _require_float(joints[2], "xarm.joints.j3"),
            "j4": _require_float(joints[3], "xarm.joints.j4"),
            "j5": _require_float(joints[4], "xarm.joints.j5"),
            "j6": _require_float(joints[5], "xarm.joints.j6"),
        }

    return xarm_position, xarm_joints


def _parse_xarm_status(xarm_data: dict[str, Any]) -> dict[str, Any]:
    """Extract xarm connection/error status (used by telemetry payload)."""
    return {
        "connected": bool(xarm_data.get("connected", False)),
        "has_error": bool(xarm_data.get("has_error", False)),
        "has_warn": bool(xarm_data.get("has_warn", False)),
        "state_code": xarm_data.get("state_code"),
    }


# ------------------------------------------------------------------
# Shared pose validation
# ------------------------------------------------------------------


def _parse_validated_pose(
    symovo_data: dict[str, Any],
    context: str,
) -> tuple[float, float, float] | None:
    """Extract and validate ``(x, y, theta_rad)`` from symovo pose data.

    Returns ``None`` when the pose dict is missing or critical fields
    (x, y, theta) are absent/unconvertible — the caller should skip
    the payload entirely rather than publish misleading coordinates.
    """
    pose = symovo_data.get("pose")
    if not isinstance(pose, dict):
        return None

    x = _require_float(pose.get("x_m"), "pose.x_m")
    y = _require_float(pose.get("y_m"), "pose.y_m")
    theta_deg = _require_float(pose.get("theta_deg"), "pose.theta_deg")

    if x is None or y is None or theta_deg is None:
        logger.warning(
            "Skipping %s payload: critical pose fields missing (x=%r, y=%r, theta_deg=%r)",
            context,
            pose.get("x_m"),
            pose.get("y_m"),
            pose.get("theta_deg"),
        )
        return None

    return x, y, math.radians(theta_deg)


# ------------------------------------------------------------------
# Navigation
# ------------------------------------------------------------------

# Mapping from symovo internal state to navigation status exposed via MQTT.
# "paused" and "localization" are mapped to "idle" because the SRS defines
# the robot as non-navigating in both cases — downstream consumers should
# not distinguish between "truly idle" and "paused/localizing".
# If this mapping needs to change, update SRS section 4.3 in parallel.
_SYMOVO_STATE_MAP: dict[str, str] = {
    "localization": "idle",
    "idle": "idle",
    "navigating": "navigating",
    "arrived": "arrived",
    "error": "error",
    "paused": "idle",
}


def _map_symovo_state_to_navigation_status(symovo_state: str) -> str:
    """Map symovo state to navigation status per SRS."""
    return _SYMOVO_STATE_MAP.get(symovo_state.lower(), "idle")


def _extract_navigation_data(
    robot_data: dict[str, Any],
    symovo_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract navigation data from robot and symovo responses.

    Returns ``None`` when critical pose fields (x, y, theta) are missing
    or unconvertible — never publishes a payload with fake coordinates.
    """
    validated = _parse_validated_pose(symovo_data, "navigation")
    if validated is None:
        return None
    x, y, theta_rad = validated

    nav_status = _map_symovo_state_to_navigation_status(
        symovo_data.get("state", "idle"),
    )

    target_id = robot_data.get("target_id") or symovo_data.get("target_id")

    return {
        "status": nav_status,
        "target_id": target_id,
        "progress_percent": None,
        "current_position": {
            "x": x,
            "y": y,
            "theta": theta_rad,
        },
        "eta_seconds": None,
        "error_code": None,
        "error_message": None,
    }


# ------------------------------------------------------------------
# System metrics
# ------------------------------------------------------------------


def collect_host_metrics() -> dict[str, Any] | None:
    """Collect host system metrics (CPU, RAM, disk, uptime) via psutil.

    This performs I/O — call from the service loop, not from payload builders.
    Returns ``None`` when all metrics are unavailable.
    """
    cpu_percent: float | None = None
    ram_percent: float | None = None
    disk_percent: float | None = None
    uptime_seconds: float | None = None

    try:
        cpu_percent = round(float(psutil.cpu_percent(interval=None)), 1)
    except Exception:
        logger.warning("Failed to get CPU metrics", exc_info=True)
    try:
        ram_percent = round(float(psutil.virtual_memory().percent), 1)
    except Exception:
        logger.warning("Failed to get RAM metrics", exc_info=True)
    try:
        disk = psutil.disk_usage("/")
        disk_percent = round(float(disk.percent), 1)
    except Exception:
        logger.warning("Failed to get disk usage", exc_info=True)
    try:
        uptime_seconds = round(time.time() - float(psutil.boot_time()), 1)
    except Exception:
        logger.warning("Failed to get uptime", exc_info=True)

    result = {
        "cpu": cpu_percent,
        "ram": ram_percent,
        "disk": disk_percent,
        "uptime_seconds": uptime_seconds,
    }
    # Return None when all metrics failed (e.g. psutil unavailable).
    if all(v is None for v in result.values()):
        return None
    return result


def _extract_system_data(symovo_data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract battery data from symovo for ``status/system``.

    Pure function — no I/O.  Host metrics are collected separately
    via :func:`collect_host_metrics` and passed to the payload builder.
    """
    battery = symovo_data.get("battery_level_percent")
    if battery is None:
        return None

    battery_value = _require_float(battery, "battery_level_percent")
    if battery_value is None:
        return None

    return {"battery": round(battery_value)}


# ------------------------------------------------------------------
# Shared robot component parsing
# ------------------------------------------------------------------


def _get_component_dicts(
    robot_data: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract igus and xarm sub-dicts from *robot_data* (None if not a dict)."""
    igus = robot_data.get("igus")
    xarm = robot_data.get("xarm")
    return (igus if isinstance(igus, dict) else None, xarm if isinstance(xarm, dict) else None)


def _parse_robot_components(
    robot_data: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Parse igus and xarm status from *robot_data*.

    Returns ``(igus_status, xarm_status)`` — either may be ``None``.
    """
    igus_data, xarm_data = _get_component_dicts(robot_data)
    igus_status = _parse_igus_status(igus_data) if igus_data else None
    xarm_status = _parse_xarm_status(xarm_data) if xarm_data else None
    return igus_status, xarm_status


def _parse_robot_arm_details(
    robot_data: dict[str, Any],
) -> tuple[dict[str, float] | None, dict[str, float] | None, dict[str, float] | None]:
    """Parse igus lift position, xarm position, and xarm joints from *robot_data*.

    Returns ``(lift_position, xarm_position, xarm_joints)`` — any may be ``None``.
    """
    igus_data, xarm_data = _get_component_dicts(robot_data)
    lift_position = _parse_igus_lift_position(igus_data) if igus_data else None
    xarm_position = None
    xarm_joints = None
    if xarm_data:
        xarm_position, xarm_joints = _parse_xarm_position_and_joints(xarm_data)
    return lift_position, xarm_position, xarm_joints


# ------------------------------------------------------------------
# Public payload builders
# ------------------------------------------------------------------


def build_navigation_status_payload(
    robot_id: str,
    robot_data: dict[str, Any],
    symovo_data: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Build payload for ``status/navigation``."""
    nav_data = _extract_navigation_data(robot_data, symovo_data)
    if not nav_data:
        return None

    lift_position, xarm_position, xarm_joints = _parse_robot_arm_details(robot_data)

    payload: dict[str, Any] = {
        "v": 1,
        "robot_id": robot_id,
        "timestamp": timestamp or now_iso(),
        "status": nav_data["status"],
        "target_id": nav_data["target_id"],
        "progress_percent": nav_data["progress_percent"],
        "current_position": nav_data["current_position"],
        "eta_seconds": nav_data["eta_seconds"],
        "error_code": nav_data["error_code"],
        "error_message": nav_data["error_message"],
    }

    if lift_position is not None:
        payload["lift_position"] = lift_position
    if xarm_position is not None:
        payload["xarm_position"] = xarm_position
    if xarm_joints is not None:
        payload["xarm_joints"] = xarm_joints

    return payload


def build_system_status_payload(
    robot_id: str,
    symovo_data: dict[str, Any],
    *,
    host_metrics: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Build payload for ``status/system``.

    Robot metrics (battery) come from *symovo_data*.  Host metrics
    (cpu, ram, disk, uptime) are collected externally via
    :func:`collect_host_metrics` and passed in to keep I/O out of this function.
    """
    sys_data = _extract_system_data(symovo_data)
    if not sys_data:
        return None

    payload: dict[str, Any] = {
        "v": 1,
        "robot_id": robot_id,
        "timestamp": timestamp or now_iso(),
        "battery": sys_data["battery"],
    }

    if host_metrics:
        host_out = {k: v for k, v in host_metrics.items() if v is not None}
        if host_out:
            payload["host_metrics"] = host_out

    return payload


def build_telemetry_payload(
    robot_id: str,
    robot_data: dict[str, Any],
    symovo_data: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Build payload for telemetry topic.

    Returns ``None`` when critical pose fields are missing/invalid.
    """
    validated = _parse_validated_pose(symovo_data, "telemetry")
    if validated is None:
        return None
    x, y, theta_rad = validated

    pose = symovo_data.get("pose", {})
    velocity = symovo_data.get("velocity", {})
    state_flags = symovo_data.get("state_flags", {})

    igus_status, xarm_status = _parse_robot_components(robot_data)

    payload: dict[str, Any] = {
        "v": 1,
        "robot_id": robot_id,
        "timestamp": timestamp or now_iso(),
        "data": {
            "pose": {
                "x": x,
                "y": y,
                "theta": theta_rad,
                "map_id": _safe_int_or_none(pose.get("map_id")),
            },
            "velocity": {
                "vx": _require_float(velocity.get("vx_m_s"), "velocity.vx_m_s"),
                "vy": _require_float(velocity.get("vy_m_s"), "velocity.vy_m_s"),
                "omega": _parse_omega(velocity),
            },
            "battery_percent": _require_float(symovo_data.get("battery_level_percent"), "battery_level_percent"),
            "state": symovo_data.get("state", "unknown"),
            "state_flags": state_flags if isinstance(state_flags, dict) else {},
        },
    }

    if igus_status is not None:
        payload.setdefault("components", {})["igus"] = igus_status
    if xarm_status is not None:
        payload.setdefault("components", {})["xarm"] = xarm_status

    return payload


# Keys that must never be forwarded from upstream HTTP responses into MQTT.
# Prevents accidental leakage of internal/sensitive data.
_STATUS_DATA_REDACTED_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credentials",
        "authorization",
        "private_key",
    }
)


def _sanitize_status_data(data: Any) -> Any:
    """Remove sensitive keys from upstream data before publishing to MQTT."""
    if not isinstance(data, dict):
        return data
    return {k: _sanitize_status_data(v) for k, v in data.items() if k.lower() not in _STATUS_DATA_REDACTED_KEYS}


def build_status_payload(
    robot_id: str,
    service_name: str,
    status: str,
    data: Any,
    error: str | None,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Generic service status payload.

    Upstream *data* is sanitized to remove known sensitive keys
    before publishing to MQTT.
    """
    return {
        "v": 1,
        "robot_id": robot_id,
        "service": service_name,
        "timestamp": timestamp or now_iso(),
        "status": status,
        "data": _sanitize_status_data(data),
        "error": error,
    }


def build_connection_status_payload(
    robot_id: str,
    janus_ws_status: dict[str, bool],
    mqtt_status: bool,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build payload for ``status/connection``.

    *janus_ws_status* is a dict with per-endpoint Janus WebSocket connectivity,
    e.g. ``{"depth": True, "color": False}``.
    """
    return {
        "v": 1,
        "robot_id": robot_id,
        "timestamp": timestamp or now_iso(),
        "janus_ws": janus_ws_status,
        "mqtt": mqtt_status,
    }


# ------------------------------------------------------------------
# Boundary validation
# ------------------------------------------------------------------

# Expected top-level keys in the robot /status response.  Missing keys
# are not fatal (payload builders handle None gracefully) but indicate
# an upstream contract change that should be investigated.
_EXPECTED_SYMOVO_KEYS = {"pose", "state", "velocity", "battery_level_percent"}
_EXPECTED_POSE_KEYS = {"x_m", "y_m", "theta_deg"}


def validate_robot_response(robot_data: dict[str, Any]) -> list[str]:
    """Check *robot_data* for expected structure; return list of warnings.

    This is a **boundary-level** check: call it once right after
    ``fetch_service_status`` returns, so that unexpected upstream
    changes are logged immediately rather than masked by defensive
    defaults deep inside payload builders.

    Returns an empty list when the data looks as expected.
    """
    warnings: list[str] = []

    if not isinstance(robot_data, dict):
        warnings.append(f"robot_data is {type(robot_data).__name__}, expected dict")
        return warnings

    symovo = robot_data.get("symovo")
    if symovo is None:
        warnings.append("missing 'symovo' key in robot_data")
        return warnings
    if not isinstance(symovo, dict):
        warnings.append(f"'symovo' is {type(symovo).__name__}, expected dict")
        return warnings

    missing_symovo = _EXPECTED_SYMOVO_KEYS - symovo.keys()
    if missing_symovo:
        warnings.append(f"symovo missing keys: {sorted(missing_symovo)}")

    pose = symovo.get("pose")
    if isinstance(pose, dict):
        missing_pose = _EXPECTED_POSE_KEYS - pose.keys()
        if missing_pose:
            warnings.append(f"symovo.pose missing keys: {sorted(missing_pose)}")
    elif pose is not None:
        warnings.append(f"symovo.pose is {type(pose).__name__}, expected dict")

    igus = robot_data.get("igus")
    if igus is not None and not isinstance(igus, dict):
        warnings.append(f"'igus' is {type(igus).__name__}, expected dict or absent")

    xarm = robot_data.get("xarm")
    if xarm is not None and not isinstance(xarm, dict):
        warnings.append(f"'xarm' is {type(xarm).__name__}, expected dict or absent")

    return warnings
