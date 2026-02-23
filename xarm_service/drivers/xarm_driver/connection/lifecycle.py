"""ConnectionLifecycle: register/release SDK callbacks once; callbacks write StateStore only."""
import logging
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)


class ConnectionLifecycle:
    """Register callbacks on connect, release on disconnect. Callbacks only update StateStore."""

    def __init__(self, state_store_getter: Callable[[], Any]) -> None:
        self._get_store = state_store_getter
        self._arm: Optional[Any] = None
        self._callbacks_registered = False

    def register(self, arm: Any) -> None:
        """Register callbacks on the arm. Idempotent per arm instance."""
        if self._arm is arm and self._callbacks_registered:
            return
        self.release()
        self._arm = arm
        store = self._get_store()
        try:
            if hasattr(arm, "register_error_warn_changed_callback"):
                arm.register_error_warn_changed_callback(
                    lambda data: self._on_error_warn(store, data)
                )
            if hasattr(arm, "register_state_changed_callback"):
                arm.register_state_changed_callback(
                    lambda data: self._on_state(store, data)
                )
            if hasattr(arm, "register_count_changed_callback"):
                arm.register_count_changed_callback(
                    lambda data: self._on_count(store, data)
                )
            self._callbacks_registered = True
        except Exception as e:
            logger.warning("Failed to register callbacks: %s", e)

    def release(self) -> None:
        """Release all callbacks from the arm."""
        if not self._arm or not self._callbacks_registered:
            self._arm = None
            self._callbacks_registered = False
            return
        try:
            if hasattr(self._arm, "release_error_warn_changed_callback"):
                self._arm.release_error_warn_changed_callback(None)
            if hasattr(self._arm, "release_state_changed_callback"):
                self._arm.release_state_changed_callback(None)
            if hasattr(self._arm, "release_count_changed_callback"):
                self._arm.release_count_changed_callback(None)
        except Exception as e:
            logger.warning("Release callbacks: %s", e)
        self._arm = None
        self._callbacks_registered = False

    @staticmethod
    def _on_error_warn(store: Any, data: Any) -> None:
        """Callback from SDK: always update error/warn, including clearing to 0."""
        if not data:
            return
        try:
            store.set_robot(
                error_code=int(data.get("error_code", 0) or 0),
                warn_code=int(data.get("warn_code", 0) or 0),
            )
        except Exception:
            # Keep callbacks non-throwing
            pass

    @staticmethod
    def _on_state(store: Any, data: Any) -> None:
        if data:
            store.set_robot(mode=data.get("state", 0))

    @staticmethod
    def _on_count(store: Any, data: Any) -> None:
        pass  # optional: update execution state if needed
