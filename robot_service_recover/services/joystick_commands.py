from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CommandType(str, Enum):
    MOVE_STEP = "move_step"
    MOVE_STOP = "move_stop"
    GRIPPER_TOGGLE = "gripper_toggle"
    AUTOTAKE = "autotake"
    SYMOVO_MOVE = "symovo_move"
    SYMOVO_STOP = "symovo_stop"


@dataclass
class JoystickCommand:
    cmd_type: CommandType
    direction: Optional[str] = None
    is_loop: bool = False
    metadata: Optional[dict] = None

