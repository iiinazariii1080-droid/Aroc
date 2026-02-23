"""Throttled logging mixin for MQTT components."""
import logging
import time

logger = logging.getLogger(__name__)


class ThrottledLoggerMixin:
    """Mixin that provides throttled logging to prevent log spam.

    Attributes expected on ``self``:
        component_name (str): Used as a prefix in throttle-count messages.
        _log_throttle_interval (float): Minimum seconds between identical log keys.
    """

    # ---- internal state (set up by subclass __init__) ----------------
    _last_log_time: dict[str, float]
    _repeated_log_count: dict[str, int]
    _log_throttle_interval: float
    component_name: str

    # ---- public helpers ---------------------------------------------

    def _log_info(self, message: str, log_key: str) -> None:
        """Log at INFO and reset throttle for *log_key*."""
        logger.info(message)
        self._reset_log_throttle(log_key)

    def _log_info_throttled(self, message: str, log_key: str) -> None:
        """Log at INFO only if *log_key* is not throttled."""
        if self._should_throttle_log(log_key):
            return
        logger.info(message)

    def _log_warning(self, message: str, log_key: str) -> None:
        """Log at WARNING and reset throttle for *log_key*."""
        logger.warning(message)
        self._reset_log_throttle(log_key)

    def _log_warning_throttled(self, message: str, log_key: str) -> None:
        """Log at WARNING only if *log_key* is not throttled."""
        if self._should_throttle_log(log_key):
            return
        logger.warning(message)

    def _log_error(self, message: str, log_key: str, exc_info: bool = False) -> None:
        """Log at ERROR and reset throttle for *log_key*."""
        logger.error(message, exc_info=exc_info)
        self._reset_log_throttle(log_key)

    def _log_error_throttled(self, message: str, log_key: str, exc_info: bool = False) -> None:
        """Log at ERROR only if *log_key* is not throttled."""
        if self._should_throttle_log(log_key):
            return
        logger.error(message, exc_info=exc_info)

    # ---- private helpers --------------------------------------------

    def _should_throttle_log(self, log_key: str) -> bool:
        """Return ``True`` if the message for *log_key* should be suppressed."""
        now = time.time()
        last_time = self._last_log_time.get(log_key, 0)

        if now - last_time < self._log_throttle_interval:
            self._repeated_log_count[log_key] = self._repeated_log_count.get(log_key, 0) + 1
            return True

        repeat_count = self._repeated_log_count.get(log_key, 0)
        if repeat_count > 0:
            logger.info(
                "[%s] Previous message repeated %d times",
                self.component_name,
                repeat_count,
            )
            self._repeated_log_count[log_key] = 0

        self._last_log_time[log_key] = now
        return False

    def _reset_log_throttle(self, log_key: str) -> None:
        """Reset throttle state for *log_key*."""
        self._last_log_time[log_key] = time.time()
        if log_key in self._repeated_log_count:
            del self._repeated_log_count[log_key]
