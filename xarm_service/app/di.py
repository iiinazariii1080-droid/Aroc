"""Dependency injection: singletons for StateStore, ReadinessGate, RobotActor, CommandService."""
from typing import Optional

from drivers.xarm_driver.state import StateStore, ReadinessGate
from drivers.xarm_driver.actor import RobotActor
from drivers.xarm_driver.usecases import CommandService

_state_store: Optional[StateStore] = None
_readiness_gate: Optional[ReadinessGate] = None
_actor: Optional[RobotActor] = None
_command_service: Optional[CommandService] = None


def get_state_store() -> StateStore:
    global _state_store
    if _state_store is None:
        _state_store = StateStore()
    return _state_store


def get_readiness_gate() -> ReadinessGate:
    global _readiness_gate
    if _readiness_gate is None:
        _readiness_gate = ReadinessGate(get_state_store())
    return _readiness_gate


def get_actor() -> RobotActor:
    global _actor
    if _actor is None:
        _actor = RobotActor(state_store_getter=get_state_store)
    return _actor


def get_command_service() -> CommandService:
    global _command_service
    if _command_service is None:
        actor = get_actor()
        _command_service = CommandService(
            readiness_getter=get_readiness_gate,
            actor_enqueue=actor.enqueue,
            idempotency_cache_size=256,
        )
    return _command_service


def init_di() -> None:
    """Initialize singletons (called from lifespan)."""
    get_state_store()
    get_readiness_gate()
    get_actor()
    get_command_service()


def shutdown_di() -> None:
    """Clear singletons on shutdown."""
    global _state_store, _readiness_gate, _actor, _command_service
    _state_store = None
    _readiness_gate = None
    _actor = None
    _command_service = None
