"""Movement primitives: safe poses, pick/place template (from WORKSPACE_SAFETY_ENVELOPE_V3)."""
from typing import List, Tuple
from app.config import SAFE_HIGH_POSE_JOINTS


def get_safe_high_pose_joints() -> Tuple[float, ...]:
    """Return predefined safe high pose (joints deg) — inside workspace."""
    return SAFE_HIGH_POSE_JOINTS


def pick_place_sequence_steps() -> List[str]:
    """
    Template step names for pick/place:
    1) MOVE_JOINTS -> safe_high_pose
    2) MOVE_JOINTS/MOVE_LINEAR -> pre_pick_above
    3) MOVE_LINEAR -> pick
    4) GRIP_CLOSE/OPEN
    5) MOVE_LINEAR -> retreat
    6) MOVE_JOINTS -> safe_high_pose
    """
    return [
        "move_to_safe_high",
        "move_to_pre_pick",
        "move_to_pick",
        "grip",
        "retreat",
        "move_to_safe_high",
    ]
