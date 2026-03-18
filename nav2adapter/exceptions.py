class RobotBaseError(Exception):
    """Base class for all robot errors.

    Supports an optional machine-readable ``error_code`` for structured error
    handling (e.g. ``"DEVICE_BUSY"``, ``"TRANSPORT_MOVE_FAILED"``).
    """

    error_code: str = "ROBOT_ERROR"

    def __init__(self, *args: object, error_code: str | None = None) -> None:
        super().__init__(*args)
        if error_code is not None:
            self.error_code = error_code

class RobotError(RobotBaseError):
    error_code = "ROBOT_ERROR"

class DeviceBusyError(RobotBaseError):
    error_code = "DEVICE_BUSY"

class DeviceConnectionError(RobotBaseError):
    """Device connection error."""
    error_code = "DEVICE_CONNECTION_ERROR"

class DeviceError(RobotBaseError):
    error_code = "DEVICE_ERROR"

class Conflict(RobotBaseError):
    error_code = "CONFLICT"

class InputError(RobotBaseError):
    error_code = "INPUT_ERROR"

class DeviceReadyError(RobotBaseError):
    error_code = "DEVICE_NOT_READY"



