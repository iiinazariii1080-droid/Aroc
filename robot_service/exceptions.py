class RobotBaseError(Exception):
    """Base class for all robot errors."""
    pass

class RobotError(RobotBaseError):
    pass

class DeviceBusyError(RobotBaseError):
    pass
class DeviceConnectionError(RobotBaseError):
    """Device connection error."""
    pass
class DeviceError(RobotBaseError):
    pass

class Conflict(RobotBaseError):
    pass

class InputError(RobotBaseError):
    pass

class DeviceReadyError(RobotBaseError):
    pass

class SafetyLockoutError(RobotBaseError):
    """Safety lockout active (E-Stop / relay open). No motion commands allowed."""
    pass

class TransportMoveError(RobotBaseError):
    pass



