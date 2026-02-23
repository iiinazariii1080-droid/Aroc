"""Payload builder functions for telemetry MQTT messages.

All functions are pure: they accept data dicts and return payload dicts
(or ``None`` when required data is missing).  No I/O or side effects.
"""
import math
from datetime import UTC, datetime
from typing import Any

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

import logging
import time

logger = logging.getLogger(__name__)


def iso_timestamp() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------
# Navigation
# ------------------------------------------------------------------

def _map_symovo_state_to_navigation_status(symovo_state: str) -> str:
    """Map symovo state to navigation status per SRS."""
    state_map = {
        "localization": "idle",
        "idle": "idle",
        "navigating": "navigating",
        "arrived": "arrived",
        "error": "error",
        "paused": "idle",
    }
    return state_map.get(symovo_state.lower(), "idle")


def _extract_navigation_data(
    robot_data: dict[str, Any],
    symovo_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract navigation data from robot and symovo responses.

    Returns data for ``status/navigation`` per SRS 4.1.
    """
    if not symovo_data or not isinstance(symovo_data, dict):
        return None

    pose = symovo_data.get("pose")
    if not pose or not isinstance(pose, dict):
        return None

    # Convert theta from degrees to radians
    theta_deg = pose.get("theta_deg", 0.0)
    theta_rad = math.radians(theta_deg)

    # Map navigation state
    symovo_state = symovo_data.get("state", "idle")
    nav_status = _map_symovo_state_to_navigation_status(symovo_state)

    # Extract target_id if available
    target_id = None
    if isinstance(robot_data, dict):
        target_id = robot_data.get("target_id")
    if not target_id and isinstance(symovo_data, dict):
        target_id = symovo_data.get("target_id")

    return {
        "status": nav_status,
        "target_id": target_id,
        "progress_percent": None,
        "current_position": {
            "x": float(pose.get("x_m", 0.0)),
            "y": float(pose.get("y_m", 0.0)),
            "theta": float(theta_rad),
        },
        "eta_seconds": None,
        "error_code": None,
        "error_message": None,
    }


# ------------------------------------------------------------------
# System metrics
# ------------------------------------------------------------------

def _extract_system_data(symovo_data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract system data for ``status/system`` per SRS.

    Includes standard metrics typically published by robots.
    """
    if not symovo_data or not isinstance(symovo_data, dict):
        return None

    battery = symovo_data.get("battery_level_percent")
    if battery is None:
        return None

    cpu_percent: float | None = None
    ram_percent: float | None = None
    disk_percent: float | None = None
    uptime_seconds: float | None = None

    if PSUTIL_AVAILABLE:
        try:
            cpu_percent = round(float(psutil.cpu_percent(interval=None)), 1)
            ram_percent = round(float(psutil.virtual_memory().percent), 1)
            try:
                disk = psutil.disk_usage("/")
                disk_percent = round(float(disk.percent), 1)
            except Exception:
                logger.debug("Failed to get disk usage", exc_info=True)
            try:
                uptime_seconds = time.time() - float(psutil.boot_time())
            except Exception:
                logger.debug("Failed to get uptime", exc_info=True)
        except Exception:
            logger.debug("Failed to get CPU/RAM metrics", exc_info=True)

    return {
        "cpu": cpu_percent,
        "ram": ram_percent,
        "battery": round(float(battery)),
        "disk": disk_percent,
        "uptime_seconds": uptime_seconds,
    }


# ------------------------------------------------------------------
# Public payload builders
# ------------------------------------------------------------------

def build_navigation_status_payload(
    robot_id: str,
    robot_data: dict[str, Any],
    symovo_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build payload for ``status/navigation`` per SRS 4.1."""
    nav_data = _extract_navigation_data(robot_data, symovo_data)
    if not nav_data:
        return None

    igus_data = robot_data.get("igus") if isinstance(robot_data, dict) else None
    xarm_data = robot_data.get("xarm") if isinstance(robot_data, dict) else None

    # Lift height (igus)
    lift_position = None
    if isinstance(igus_data, dict):
        position_cm = igus_data.get("position_cm") or igus_data.get("position")
        if isinstance(position_cm, (int, float)):
            lift_position = {"height": float(position_cm)}

    # xarm position and joints
    xarm_position = None
    xarm_joints = None
    if isinstance(xarm_data, dict):
        data = xarm_data.get("data")
        if isinstance(data, (list, tuple)) and len(data) > 19:
            chrods = data[19] if len(data) > 19 else None
            if isinstance(chrods, (list, tuple)) and len(chrods) >= 3:
                xarm_position = {
                    "x": float(chrods[0]),
                    "y": float(chrods[1]),
                    "z": float(chrods[2]),
                }
            joints = data[18] if len(data) > 18 else None
            if isinstance(joints, (list, tuple)) and len(joints) >= 6:
                xarm_joints = {
                    "j1": float(joints[0]),
                    "j2": float(joints[1]),
                    "j3": float(joints[2]),
                    "j4": float(joints[3]),
                    "j5": float(joints[4]),
                    "j6": float(joints[5]),
                }

    payload: dict[str, Any] = {
        "robot_id": robot_id,
        "timestamp": iso_timestamp(),
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
) -> dict[str, Any] | None:
    """Build payload for ``status/system`` per SRS 4.1."""
    sys_data = _extract_system_data(symovo_data)
    if not sys_data:
        return None

    payload: dict[str, Any] = {
        "robot_id": robot_id,
        "timestamp": iso_timestamp(),
        "cpu": sys_data["cpu"],
        "ram": sys_data["ram"],
        "battery": sys_data["battery"],
    }

    if sys_data.get("disk") is not None:
        payload["disk"] = sys_data["disk"]
    if sys_data.get("uptime_seconds") is not None:
        payload["uptime_seconds"] = round(sys_data["uptime_seconds"], 1)

    return payload


def build_telemetry_payload(
    robot_id: str,
    robot_data: dict[str, Any],
    symovo_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build payload for telemetry topic per SRS 4.1."""
    if not symovo_data or not isinstance(symovo_data, dict):
        return None

    pose = symovo_data.get("pose", {})
    velocity = symovo_data.get("velocity", {})
    state_flags = symovo_data.get("state_flags", {})

    igus_data = robot_data.get("igus") if isinstance(robot_data, dict) else None
    xarm_data = robot_data.get("xarm") if isinstance(robot_data, dict) else None

    igus_status = None
    if isinstance(igus_data, dict):
        igus_status = {
            "connected": bool(igus_data.get("connected", False)),
            "homed": bool(igus_data.get("homed", False)),
            "is_moving": bool(igus_data.get("is_moving", False)),
            "error": bool(igus_data.get("error", False)),
            "position_cm": (
                float(igus_data.get("position_cm", 0.0))
                if igus_data.get("position_cm") is not None
                else None
            ),
        }

    xarm_status = None
    if isinstance(xarm_data, dict):
        xarm_status = {
            "connected": bool(xarm_data.get("connected", False)),
            "has_error": bool(xarm_data.get("has_error", False)),
            "has_warn": bool(xarm_data.get("has_warn", False)),
            "state_code": xarm_data.get("state_code"),
        }

    payload: dict[str, Any] = {
        "robot_id": robot_id,
        "timestamp": iso_timestamp(),
        "data": {
            "pose": {
                "x": float(pose.get("x_m", 0.0)),
                "y": float(pose.get("y_m", 0.0)),
                "theta": float(math.radians(pose.get("theta_deg", 0.0))),
                "map_id": int(pose.get("map_id", 0)),
            },
            "velocity": {
                "vx": float(velocity.get("vx_m_s", 0.0)),
                "vy": float(velocity.get("vy_m_s", 0.0)),
                "omega": (
                    float(velocity.get("omega_rad_s", 0.0))
                    if "omega_rad_s" in velocity
                    else math.radians(float(velocity.get("omega_deg_s", 0.0)))
                ),
            },
            "battery_percent": float(symovo_data.get("battery_level_percent", 0.0)),
            "state": symovo_data.get("state", "unknown"),
            "state_flags": state_flags if isinstance(state_flags, dict) else {},
        },
    }

    if igus_status is not None:
        payload.setdefault("components", {})["igus"] = igus_status
    if xarm_status is not None:
        payload.setdefault("components", {})["xarm"] = xarm_status

    return payload


def build_status_payload(
    robot_id: str,
    service_name: str,
    status: str,
    data: Any,
    error: str | None,
) -> dict[str, Any]:
    """Generic service status payload (legacy, backward compatibility)."""
    return {
        "robot_id": robot_id,
        "service": service_name,
        "timestamp": iso_timestamp(),
        "status": status,
        "data": data,
        "error": error,
    }


def build_connection_status_payload(
    robot_id: str,
    webrtc_status: bool,
    mqtt_status: bool,
) -> dict[str, Any]:
    """Build payload for ``status/connection`` per SRS 4.1."""
    return {
        "robot_id": robot_id,
        "timestamp": iso_timestamp(),
        "webrtc": webrtc_status,
        "mqtt": mqtt_status,
    }
