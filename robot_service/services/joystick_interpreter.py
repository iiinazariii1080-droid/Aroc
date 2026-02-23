import time
from typing import Any, Dict, Optional

from services.joystick_commands import CommandType, JoystickCommand


class JoystickInterpreter:
    def __init__(self, pipeline, *, deadzone: float = 0.2):
        self._pipeline = pipeline
        self._deadzone = deadzone
        self.commands_total = 0
        self.last_command_type: Optional[str] = None
        self.frame_to_command_delay_ms = 0.0

    async def process_frame(self, frame: Dict[str, Any]) -> None:
        start = time.time()
        command = self._build_command(frame)
        if command is None:
            return
        if command.metadata is None:
            command.metadata = {}
        command.metadata.setdefault("ttl_ms", frame.get("ttl_ms", 150))
        command.metadata.setdefault("age_ms", frame.get("age_ms"))
        await self._dispatch(command, frame)
        self.commands_total += 1
        self.last_command_type = command.cmd_type.value
        self.frame_to_command_delay_ms = (time.time() - start) * 1000.0

    async def _dispatch(self, command: JoystickCommand, frame: Optional[Dict[str, Any]] = None) -> None:
        frame = frame or {}
        age_ms = command.metadata.get("age_ms") if command.metadata else frame.get("age_ms")
        if command.cmd_type == CommandType.MOVE_STEP:
            ttl_ms = int(command.metadata.get("ttl_ms", self._pipeline._default_ttl_ms))
            await self._pipeline._send_move(
                command.direction,
                is_loop=command.is_loop,
                ttl_ms=ttl_ms,
                last_input_age_ms=age_ms,
            )
            # For tap-style moves only: schedule a stop in ttl_ms.
            # For loop moves we rely on explicit release + inactivity watchdog.
            if not command.is_loop:
                self._pipeline._schedule_step_over(ttl_ms)
        elif command.cmd_type == CommandType.MOVE_STOP:
            await self._pipeline._move_step_over(last_input_age_ms=age_ms)
        elif command.cmd_type == CommandType.LIFT_JOG:
            md = command.metadata or {}
            await self._pipeline._send_lift_jog(
                str(md.get("direction", "up")),
                ttl_ms=int(md.get("ttl_ms", self._pipeline._default_ttl_ms)),
            )
        elif command.cmd_type == CommandType.LIFT_STOP:
            await self._pipeline._stop_lift_jog()
        elif command.cmd_type == CommandType.GRIPPER_TOGGLE:
            await self._pipeline._toggle_gripper()
        elif command.cmd_type == CommandType.AUTOTAKE:
            await self._pipeline._call_autotake()
        elif command.cmd_type == CommandType.SYMOVO_MOVE:
            md = command.metadata or {}
            await self._pipeline._send_symovo_motion(
                int(md.get("linear_dir", 0)),
                int(md.get("angular_dir", 0)),
                float(md.get("duration", 0.25)),
            )
        elif command.cmd_type == CommandType.SYMOVO_STOP:
            dur = float((command.metadata or {}).get("duration", 0.25))
            await self._pipeline._send_symovo_motion(0, 0, dur)

    def _build_command(self, frame: Dict[str, Any]) -> Optional[JoystickCommand]:
        buttons = list(frame.get("buttons", []))
        axes = list(frame.get("axes", []))
        prev_buttons = list(frame.get("prev_buttons", [0] * len(buttons)))
        ttl_ms = int(frame.get("ttl_ms", 150))

        if len(prev_buttons) < len(buttons):
            prev_buttons += [0] * (len(buttons) - len(prev_buttons))

        lift_up_idx = 10
        lift_down_idx = 8
        lift_up_prev = int(prev_buttons[lift_up_idx]) if lift_up_idx < len(prev_buttons) else 0
        lift_down_prev = int(prev_buttons[lift_down_idx]) if lift_down_idx < len(prev_buttons) else 0
        lift_up_cur = int(buttons[lift_up_idx]) if lift_up_idx < len(buttons) else 0
        lift_down_cur = int(buttons[lift_down_idx]) if lift_down_idx < len(buttons) else 0

        if lift_up_cur or lift_down_cur:
            if lift_up_cur and lift_down_cur:
                return JoystickCommand(CommandType.LIFT_STOP)
            return JoystickCommand(
                CommandType.LIFT_JOG,
                metadata={
                    "direction": "up" if lift_up_cur else "down",
                    "ttl_ms": ttl_ms,
                },
            )

        if lift_up_prev or lift_down_prev:
            return JoystickCommand(CommandType.LIFT_STOP)

        if len(buttons) > 0:
            prev0 = int(prev_buttons[0])
            cur0 = int(buttons[0])
            if prev0 == 0 and cur0 == 1:
                return JoystickCommand(CommandType.GRIPPER_TOGGLE)

        movement_map = {
            9: ("z", -1),
            11: ("z", 1),
            14: ("x", -1),
            12: ("x", 1),
            15: ("y", -1),
            13: ("y", 1),
        }

        for idx, (coord, sign) in movement_map.items():
            prev = int(prev_buttons[idx]) if idx < len(prev_buttons) else 0
            cur = int(buttons[idx]) if idx < len(buttons) else 0
            if prev == 0 and cur == 1:
                direction = self._build_direction(coord, sign)
                return JoystickCommand(
                    CommandType.MOVE_STEP,
                    direction=direction,
                    is_loop=True,
                    metadata={"ttl_ms": ttl_ms},
                )

        for idx, (coord, sign) in movement_map.items():
            if idx < len(buttons) and int(buttons[idx]) == 1:
                direction = self._build_direction(coord, sign)
                return JoystickCommand(
                    CommandType.MOVE_STEP,
                    direction=direction,
                    is_loop=True,
                    metadata={"ttl_ms": ttl_ms},
                )

        if len(buttons) > 3:
            prev3 = int(prev_buttons[3]) if len(prev_buttons) > 3 else 0
            cur3 = int(buttons[3])
            if prev3 == 0 and cur3 == 1:
                return JoystickCommand(CommandType.AUTOTAKE)

        axis2 = self._apply_deadzone(float(axes[2]) if len(axes) > 2 else 0.0)
        axis3 = self._apply_deadzone(float(axes[3]) if len(axes) > 3 else 0.0)
        if len(buttons) > 2 and int(buttons[2]) == 1 and axis2 != 0.0:
            direction = "attitude-yaw-increase" if axis2 > 0 else "attitude-yaw-decrease"
            return JoystickCommand(
                CommandType.MOVE_STEP,
                direction=direction,
                is_loop=True,
                metadata={"ttl_ms": ttl_ms},
            )
        else:
            choice: Optional[str] = None
            if axis2 != 0.0:
                choice = "attitude-roll-increase" if axis2 > 0 else "attitude-roll-decrease"
            if axis3 != 0.0 and (choice is None or abs(axis3) > abs(axis2)):
                choice = "attitude-pitch-increase" if axis3 > 0 else "attitude-pitch-decrease"
            if choice is not None:
                return JoystickCommand(
                    CommandType.MOVE_STEP,
                    direction=choice,
                    is_loop=True,
                    metadata={"ttl_ms": ttl_ms},
                )

        # Symovo teleop: 4=W, 6=S, 7=A, 5=D -> direction-only.
        # Speed magnitudes are owned by nav2adapter/symovo container defaults.
        duration = getattr(self._pipeline, "_symovo_teleop_duration", 0.25)
        linear_dir = 0
        angular_dir = 0
        if len(buttons) > 7:
            if int(buttons[4] or 0):
                linear_dir += 1
            if int(buttons[6] or 0):
                linear_dir -= 1
            if int(buttons[7] or 0):
                angular_dir += 1
            if int(buttons[5] or 0):
                angular_dir -= 1
        if linear_dir != 0 or angular_dir != 0:
            return JoystickCommand(
                CommandType.SYMOVO_MOVE,
                metadata={"linear_dir": linear_dir, "angular_dir": angular_dir, "duration": duration},
            )
        if len(prev_buttons) > 7 and any(int(prev_buttons[i] or 0) for i in (4, 5, 6, 7)):
            return JoystickCommand(CommandType.SYMOVO_STOP, metadata={"duration": duration})

        if not any(buttons):
            return JoystickCommand(CommandType.MOVE_STOP)

        return JoystickCommand(CommandType.MOVE_STOP)

    def _build_direction(self, coord: str, sign: int) -> str:
        if coord == "x":
            return "position-x-increase" if sign > 0 else "position-x-decrease"
        if coord == "y":
            return "position-y-increase" if sign > 0 else "position-y-decrease"
        return "position-z-increase" if sign > 0 else "position-z-decrease"

    def _apply_deadzone(self, value: float) -> float:
        return value if abs(value) >= self._deadzone else 0.0

