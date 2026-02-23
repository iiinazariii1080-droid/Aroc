"""CommandService: readiness check, policy, idempotency, enqueue to RobotActor."""
import logging
from typing import Optional, Callable, Any
from collections import OrderedDict

from drivers.xarm_driver.actor.commands import (
    Command,
    CommandResult,
    CommandType,
    ResultStatus,
    ExecutionPolicy,
)

logger = logging.getLogger(__name__)


class CommandService:
    """
    Application layer: check ReadinessGate, apply policy (REJECT_IF_BUSY, QUEUE, PREEMPT),
    idempotency cache, then enqueue to RobotActor.
    """

    def __init__(
        self,
        readiness_getter: Callable[[], Any],
        actor_enqueue: Callable[[Command], Any],
        idempotency_cache_size: int = 256,
    ):
        self._get_readiness = readiness_getter
        self._enqueue = actor_enqueue
        self._cache: OrderedDict[str, CommandResult] = OrderedDict()
        self._cache_max = max(idempotency_cache_size, 1)

    def _cache_get(self, command_id: str) -> Optional[CommandResult]:
        if command_id in self._cache:
            self._cache.move_to_end(command_id)
            return self._cache[command_id]
        return None

    def _cache_set(self, command_id: str, result: CommandResult) -> None:
        if command_id in self._cache:
            self._cache.move_to_end(command_id)
        else:
            if len(self._cache) >= self._cache_max:
                self._cache.popitem(last=False)
            self._cache[command_id] = result

    async def enqueue(self, command: Command) -> CommandResult:
        """Enqueue command: check readiness, policy, idempotency, then actor.enqueue."""
        cached = self._cache_get(command.command_id)
        if cached is not None:
            return cached

        gate = self._get_readiness()
        if not gate.connected:
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.REJECTED,
                error_message="Not connected",
            )
        if gate.faulted and command.type not in (CommandType.RECOVER_FAULTS, CommandType.GET_STATUS, CommandType.GRIPPER_STATUS):
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.REJECTED,
                error_message="Robot faulted; recovery required",
            )
        if gate.busy and command.type not in (CommandType.STOP, CommandType.GET_STATUS):
            if command.policy == ExecutionPolicy.REJECT_IF_BUSY:
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.REJECTED,
                    error_message="Robot busy",
                )
            # QUEUE and PREEMPT_ACTIVE: continue to enqueue
        if command.type in (CommandType.MOVE_JOINTS, CommandType.MOVE_POSE, CommandType.MOVE_LINEAR, CommandType.MOVE_TOOL_POSITION):
            if not gate.motion_enabled:
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.REJECTED,
                    error_message="Motion not enabled",
                )

        result = await self._enqueue(command)
        if result.status in (ResultStatus.SUCCEEDED, ResultStatus.FAILED, ResultStatus.CANCELED):
            self._cache_set(command.command_id, result)
        return result
