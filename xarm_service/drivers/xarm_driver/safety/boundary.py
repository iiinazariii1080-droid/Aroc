"""Compute workspace boundary in base frame and build SDK boundary list."""
from typing import List, Tuple

from typing import Any
from app.config import WS_SIZE_MM, BASE_IN_WS_MM, WS_MARGIN_MM


def compute_base_boundary_mm(
    size_mm: Tuple[float, float, float] = WS_SIZE_MM,
    base_in_ws_mm: Tuple[float, float, float] = BASE_IN_WS_MM,
    margin_mm: float = WS_MARGIN_MM,
) -> Tuple[float, float, float, float, float, float]:
    """
    Compute WS bounds in base frame (axes aligned with WS).
    Returns (x_min, x_max, y_min, y_max, z_min, z_max) in mm.
    Margin is applied inward.
    """
    sx, sy, sz = size_mm
    bx, by, bz = base_in_ws_mm
    x_min = 0 - bx + margin_mm
    x_max = sx - bx - margin_mm
    y_min = 0 - by + margin_mm
    y_max = sy - by - margin_mm
    z_min = 0 - bz + margin_mm
    z_max = sz - bz - margin_mm
    return (x_min, x_max, y_min, y_max, z_min, z_max)


def build_boundary_list(
    size_mm: Tuple[float, float, float] = WS_SIZE_MM,
    base_in_ws_mm: Tuple[float, float, float] = BASE_IN_WS_MM,
    margin_mm: float = WS_MARGIN_MM,
) -> List[float]:
    """
    Build list for set_reduced_tcp_boundary: [x_max, x_min, y_max, y_min, z_max, z_min].
    """
    x_min, x_max, y_min, y_max, z_min, z_max = compute_base_boundary_mm(
        size_mm, base_in_ws_mm, margin_mm
    )
    return [x_max, x_min, y_max, y_min, z_max, z_min]


def apply_boundary_to_arm(arm: Any, margin_mm: float = None) -> int:
    """
    Set reduced TCP boundary and enable reduced mode on the controller.
    Call after connect (e.g. from RobotActor connection lifecycle).
    arm: XArmAPI instance
    margin_mm: optional override for WS_MARGIN_MM
    Returns: SDK code (0 = success)
    """
    m = margin_mm if margin_mm is not None else WS_MARGIN_MM
    boundary = build_boundary_list(WS_SIZE_MM, BASE_IN_WS_MM, m)
    code = arm.set_reduced_tcp_boundary(boundary)
    if code == 0:
        arm.set_reduced_mode(True)
    return code
