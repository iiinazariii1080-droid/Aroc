"""TrajectoryValidator: discretize trajectory and check each point with SafetyEnvelope."""
from typing import List, Tuple, Callable, Any
from .envelope import SafetyEnvelope
from .models import CheckResult


class TrajectoryValidator:
    """
    Validate trajectory by discretizing into N points and checking each with SafetyEnvelope.
    get_fk(angles) -> pose [x,y,z,...] in base frame (mm).
    """

    def __init__(
        self,
        envelope: SafetyEnvelope = None,
        num_points: int = 20,
    ):
        self._envelope = envelope or SafetyEnvelope()
        self._num_points = max(2, num_points)

    def validate_trajectory(
        self,
        start_joints: List[float],
        end_joints: List[float],
        get_fk: Callable[[List[float]], Tuple[int, List[float]]],
    ) -> CheckResult:
        """
        Discretize linear interpolation from start_joints to end_joints;
        for each point call get_fk and check TCP in workspace.
        get_fk(angles) -> (code, pose). pose is [x,y,z,...].
        """
        if len(start_joints) != len(end_joints) or len(start_joints) < 6:
            return CheckResult.fail([])
        for i in range(self._num_points + 1):
            t = i / self._num_points
            joints = [
                start_joints[j] + t * (end_joints[j] - start_joints[j])
                for j in range(len(start_joints))
            ]
            code, pose = get_fk(joints)
            if code != 0 or not pose or len(pose) < 3:
                continue
            check = self._envelope.check_tcp_in_ws(pose)
            if not check.ok:
                return check
        return CheckResult.success()
