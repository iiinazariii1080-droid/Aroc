"""CiA 402 state machine layer for dryve D1.

This package implements:
- State machine transitions (Shutdown/Switch On/Enable Operation/Fault Reset)
- Preconditions ("dominance") checks required by the dryve D1 manual (e.g., DI7 Enable -> Statusword bit 9 "Remote")
- Fault decode & reset routines

The motor controller must be driven by a master system through explicit Controlword writes
while continuously evaluating the Statusword. Per manual, to maintain "Operation enabled"
the Controlword bits 0..3 must be sent with each telegram once set.

References:
- dryve D1 manual: State Machine, Statusword/Controlword bit definitions, Fault reset rules.
"""

from .dominance import (
    PreconditionFailed,
    require_not_in_fault,
    require_remote_enabled,
)
from .fault import (
    FaultInfo,
    FaultManager,
    FaultResetError,
)
from .state_machine import (
    CiA402StateMachine,
    InvalidBootStateError,
    StateMachineConfig,
    StateMachineError,
    StateMachineTimeout,
)

__all__ = [
    # state machine
    "CiA402StateMachine",
    "StateMachineConfig",
    "StateMachineError",
    "StateMachineTimeout",
    "InvalidBootStateError",
    # dominance
    "PreconditionFailed",
    "require_remote_enabled",
    "require_not_in_fault",
    # fault
    "FaultInfo",
    "FaultManager",
    "FaultResetError",
]
