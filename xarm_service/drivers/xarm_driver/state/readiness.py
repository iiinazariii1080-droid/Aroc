"""ReadinessGate: computed ready = connected && !faulted && motion_enabled && !busy."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import StateStore


class ReadinessGate:
    """Compute whether the robot is ready to accept motion commands."""

    def __init__(self, store: "StateStore") -> None:
        self._store = store

    @property
    def ready(self) -> bool:
        return (
            self._store.connected
            and not self._store.faulted
            and self._store.motion_enabled
            and not self._store.busy
        )

    @property
    def connected(self) -> bool:
        return self._store.connected

    @property
    def faulted(self) -> bool:
        return self._store.faulted

    @property
    def motion_enabled(self) -> bool:
        return self._store.motion_enabled

    @property
    def busy(self) -> bool:
        return self._store.busy
