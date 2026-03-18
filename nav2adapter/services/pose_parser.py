"""Unified pose parsing for all Symovo controller response formats.

Single source of truth for extracting (x, y, theta) from the various
response shapes returned by different Symovo firmware versions:

1. ``{"pose": {"x": ..., "y": ..., "theta": ...}}``         -- standard
2. ``{"x": ..., "y": ..., "theta": ...}``                    -- flat / direct
3. ``{"result": {"pose": {...}}}``                         -- wrapped
4. ``{"pose": {"x_m": ..., "y_m": ..., "theta_deg": ...}}``  -- normalized SI

Previously this logic was copy-pasted across 4 locations (status_publisher,
symovo_service, command_handler x2) with subtle variations. Consolidating
here eliminates divergence and provides a single place to add new formats.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from domain.models import Pose2D, PositionStatus

_LOGGER = logging.getLogger(__name__)


def _unwrap_list(raw: Any) -> Any:
    """Unwrap single-element list (Symovo sometimes returns [{}])."""
    if isinstance(raw, list):
        return raw[0] if raw else {}
    return raw


def find_pose_dict(raw: dict) -> Optional[dict]:
    """Locate the inner pose dict from any supported wrapper."""
    # Direct "pose" key
    if isinstance(raw.get("pose"), dict) and raw["pose"]:
        return raw["pose"]
    # Wrapped: {"result": {"pose": {...}}}
    result = raw.get("result")
    if isinstance(result, dict) and isinstance(result.get("pose"), dict):
        return result["pose"]
    # Flat: keys directly on raw
    if any(k in raw for k in ("x", "y", "x_m", "y_m")):
        return raw
    return None


def extract_x(pose: dict) -> Optional[float]:
    """Extract X coordinate, preferring x_m over x."""
    for key in ("x_m", "x"):
        val = pose.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def extract_y(pose: dict) -> Optional[float]:
    """Extract Y coordinate, preferring y_m over y."""
    for key in ("y_m", "y"):
        val = pose.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def extract_theta_rad(pose: dict) -> Optional[float]:
    """Extract heading in radians, converting from degrees if needed."""
    # theta_rad (canonical)
    val = pose.get("theta_rad")
    if isinstance(val, (int, float)):
        return float(val)
    # theta (assumed radians)
    val = pose.get("theta")
    if isinstance(val, (int, float)):
        return float(val)
    # theta_deg -> convert
    val = pose.get("theta_deg")
    if isinstance(val, (int, float)):
        return math.radians(float(val))
    return None


def parse_pose2d(raw: Any) -> Optional[Pose2D]:
    """Parse a Symovo response into a Pose2D (x, y, map_id).

    Returns None if the payload cannot be parsed.
    """
    raw = _unwrap_list(raw)
    if not isinstance(raw, dict):
        return None

    pose = find_pose_dict(raw)
    if pose is None:
        return None

    x = extract_x(pose)
    y = extract_y(pose)
    if x is None or y is None:
        return None

    map_id = pose.get("map_id")
    map_id_int = int(map_id) if isinstance(map_id, (int, float)) else None
    return Pose2D(x=x, y=y, map_id=map_id_int)


def parse_position_status(raw: Any) -> Optional[PositionStatus]:
    """Parse a Symovo response into a PositionStatus (x, y, theta, frame_id).

    Returns None if the payload cannot be parsed into a valid position.
    """
    raw = _unwrap_list(raw)
    if not isinstance(raw, dict):
        _LOGGER.warning("Pose data is not a dict: %s", type(raw).__name__)
        return None

    pose = find_pose_dict(raw)
    if pose is None:
        _LOGGER.warning("Could not extract pose from data: %s", list(raw.keys()))
        return None

    x = extract_x(pose)
    y = extract_y(pose)
    theta = extract_theta_rad(pose)

    if x is None or y is None or theta is None:
        _LOGGER.warning("Pose data incomplete: x=%s, y=%s, theta=%s", x, y, theta)
        return None

    return PositionStatus(x=x, y=y, theta=theta, frame_id="map")
