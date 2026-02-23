"""SafetyEnvelope: check TCP and link points inside workspace box (base frame)."""
from typing import List, Tuple, Sequence

from app.config import WS_SIZE_MM, BASE_IN_WS_MM, WS_MARGIN_MM, WS_CHECK_DISABLED
from .boundary import compute_base_boundary_mm
from .models import CheckResult, Violation


class SafetyEnvelope:
    """
    Check that points (TCP or link positions) in base frame are inside WS box with margin.
    All coordinates in mm; base frame aligned with WS (identity rotation).
    """

    def __init__(
        self,
        size_mm: Tuple[float, float, float] = WS_SIZE_MM,
        base_in_ws_mm: Tuple[float, float, float] = BASE_IN_WS_MM,
        margin_mm: float = WS_MARGIN_MM,
    ):
        self._size_mm = size_mm
        self._base_in_ws_mm = base_in_ws_mm
        self._margin_mm = margin_mm
        self._x_min, self._x_max, self._y_min, self._y_max, self._z_min, self._z_max = (
            compute_base_boundary_mm(size_mm, base_in_ws_mm, margin_mm)
        )

    def _point_in_box(self, x: float, y: float, z: float) -> Tuple[bool, List[Violation]]:
        if WS_CHECK_DISABLED:
            return (True, [])
        violations: List[Violation] = []
        if x < self._x_min:
            violations.append(Violation("point", x, y, z, "x_min", self._margin_mm))
        if x > self._x_max:
            violations.append(Violation("point", x, y, z, "x_max", self._margin_mm))
        if y < self._y_min:
            violations.append(Violation("point", x, y, z, "y_min", self._margin_mm))
        if y > self._y_max:
            violations.append(Violation("point", x, y, z, "y_max", self._margin_mm))
        if z < self._z_min:
            violations.append(Violation("point", x, y, z, "z_min", self._margin_mm))
        if z > self._z_max:
            violations.append(Violation("point", x, y, z, "z_max", self._margin_mm))
        return (len(violations) == 0, violations)

    def check_tcp_in_ws(self, pose: Sequence[float]) -> CheckResult:
        """
        pose: [x, y, z, ...] in base frame (mm). Extra elements (roll, pitch, yaw) ignored.
        """
        if len(pose) < 3:
            return CheckResult.fail([Violation("tcp", 0, 0, 0, None, self._margin_mm)])
        x, y, z = float(pose[0]), float(pose[1]), float(pose[2])
        ok, violations = self._point_in_box(x, y, z)
        if not ok:
            violations = [Violation("tcp", x, y, z, v.axis, v.margin_mm) for v in violations]
        return CheckResult.success() if ok else CheckResult.fail(violations)

    def check_points_in_ws(
        self,
        points: List[Tuple[str, float, float, float]],
    ) -> CheckResult:
        """
        points: list of (name, x, y, z) in base frame (mm).
        Returns failure on first violation.
        """
        all_violations: List[Violation] = []
        for name, x, y, z in points:
            ok, violations = self._point_in_box(x, y, z)
            if not ok:
                for v in violations:
                    all_violations.append(Violation(name, x, y, z, v.axis, v.margin_mm))
                return CheckResult.fail(all_violations)
        return CheckResult.success()

    def check_links_in_ws(
        self,
        link_poses: List[Tuple[str, float, float, float]],
    ) -> CheckResult:
        """
        link_poses: list of (link_name, x, y, z) in base frame (mm).
        Caller provides positions from FK (e.g. SDK get_forward_kinematics for TCP only).
        """
        return self.check_points_in_ws(link_poses)
