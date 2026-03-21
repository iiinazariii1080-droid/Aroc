"""Position proximity and zone checks for skip-if-already-there logic.

All spatial reasoning about "are we already close enough?" lives here.
No motion commands — pure read-only queries against device state.
"""

import math
import logging

logger = logging.getLogger(__name__)

# ── AGV proximity ─────────────────────────────────────────────────
AGV_SAME_SHELF_TOL_M = 0.10  # метры — радиус "уже на полке"


async def agv_is_near(
    agv_client,
    target_x_m: float,
    target_y_m: float,
    tolerance_m: float = AGV_SAME_SHELF_TOL_M,
) -> bool:
    """True если AGV в пределах tolerance от целевых координат.

    Fail-safe: возвращает False при любой ошибке (робот сделает полный муув).
    """
    try:
        pose_resp = await agv_client.pose()
        pose = (pose_resp.get("pose") or {}) if isinstance(pose_resp, dict) else {}
        cur_x = float(pose.get("x_m") or 0.0)
        cur_y = float(pose.get("y_m") or 0.0)
        dist = math.hypot(target_x_m - cur_x, target_y_m - cur_y)
        near = dist < tolerance_m
        logger.debug("agv_is_near: dist=%.3fm tol=%.3fm near=%s", dist, tolerance_m, near)
        return near
    except Exception:
        return False


# ── Arm TCP zone ──────────────────────────────────────────────────
JOB_ZONE_BOX = {"x": (-307.0, 51.4), "y": (94.5, 425.3), "z": (-38.8, 332.8)}


async def arm_tcp_in_job_zone(manipulator_client) -> bool:
    """True если TCP манипулятора внутри safe-box рабочей зоны.

    Fail-safe: возвращает False при любой ошибке.
    """
    try:
        pos = await manipulator_client.tcp_position()
        x, y, z = float(pos["x"]), float(pos["y"]), float(pos["z"])
        return (
            JOB_ZONE_BOX["x"][0] <= x <= JOB_ZONE_BOX["x"][1]
            and JOB_ZONE_BOX["y"][0] <= y <= JOB_ZONE_BOX["y"][1]
            and JOB_ZONE_BOX["z"][0] <= z <= JOB_ZONE_BOX["z"][1]
        )
    except Exception:
        return False
