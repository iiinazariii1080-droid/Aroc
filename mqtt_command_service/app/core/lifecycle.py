"""
Lifecycle manager for service components.

Manages startup, shutdown, and graceful termination of all service components
(bridge, telemetry, API) with proper signal handling.
"""
import logging
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class LifecycleManager:
    """
    Manages lifecycle of service components with graceful shutdown.

    Handles:
    - Component registration and startup
    - Graceful shutdown on SIGTERM/SIGINT
    - Proper cleanup order
    - Timeout handling for shutdown
    """

    def __init__(self, shutdown_timeout: float = 10.0):
        """
        Initialize lifecycle manager.

        Args:
            shutdown_timeout: Maximum time to wait for components to shutdown (seconds)
        """
        self._shutdown_timeout = shutdown_timeout
        self._components: list[Callable[[], None]] = []  # Shutdown callbacks
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._shutdown_started = False

        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self._handle_signal)

    def register_component(
        self,
        name: str,
        startup: Callable[[], None],
        shutdown: Callable[[], None]
    ) -> None:
        """
        Register a component with startup and shutdown callbacks.

        Args:
            name: Component name (for logging)
            startup: Function to call on startup
            shutdown: Function to call on shutdown
        """
        with self._lock:
            def shutdown_wrapper() -> None:
                try:
                    logger.info("Shutting down component: %s", name)
                    shutdown()
                    logger.info("Component %s shut down successfully", name)
                except Exception as e:
                    logger.error("Error shutting down component %s: %s", name, e, exc_info=True)

            self._components.append(shutdown_wrapper)

        # Call startup outside the lock to avoid blocking other
        # register_component / shutdown calls during slow starts
        try:
            logger.info("Starting component: %s", name)
            startup()
            logger.info("Component %s started successfully", name)
        except Exception as e:
            logger.error("Error starting component %s: %s", name, e, exc_info=True)
            raise

    def shutdown(self) -> None:
        """Initiate graceful shutdown of all components."""
        with self._lock:
            if self._shutdown_started:
                return

            self._shutdown_started = True
            self._shutdown_event.set()

        logger.info("Initiating graceful shutdown...")

        # Shutdown all components in reverse order
        shutdown_start = time.time()
        for component_shutdown in reversed(self._components):
            if time.time() - shutdown_start > self._shutdown_timeout:
                logger.warning("Shutdown timeout exceeded, forcing termination")
                break

            try:
                component_shutdown()
            except Exception as e:
                logger.error("Error during component shutdown: %s", e, exc_info=True)

        elapsed = time.time() - shutdown_start
        logger.info("Shutdown completed in %.2fs", elapsed)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals (SIGTERM, SIGINT)."""
        signal_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        logger.info("Received signal %s, initiating shutdown...", signal_name)
        self.shutdown()

    def wait_for_shutdown(self) -> None:
        """Wait for shutdown event (blocks until shutdown is requested)."""
        self._shutdown_event.wait()

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated."""
        return self._shutdown_event.is_set()

