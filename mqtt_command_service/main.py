#!/usr/bin/env python3
import logging
import sys
import threading

from app.api.v1.endpoints.tasks import set_bridge_instance
from app.core.lifecycle import LifecycleManager
from bridge import MqttCommandBridge

logger = logging.getLogger(__name__)

# Placeholders to allow patching in tests
uvicorn = None
settings = None
app = None  # placeholder for patching in tests

# Global API thread for shutdown
_api_thread: threading.Thread | None = None
_api_shutdown_event = threading.Event()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
    )


def _run_api_thread() -> None:
    """Start API server in a separate thread."""
    configure_logging()
    logger = logging.getLogger("api")

    try:
        global uvicorn
        if uvicorn is None:
            uvicorn = __import__("uvicorn")
    except ImportError as e:
        logger.error("Failed to import uvicorn: %s. Please install it with: pip install uvicorn", e)
        return

    try:
        global app
        app_module = __import__("app.main", fromlist=["app"])
        app = app_module.app
    except ImportError as e:
        logger.error("Failed to import API application (app.main): %s", e, exc_info=True)
        return
    except Exception as e:
        logger.error("Failed to initialize API application: %s", e, exc_info=True)
        return

    try:
        global settings
        if settings is None:
            settings_module = __import__("app.core.config", fromlist=["settings"])
            settings = settings_module.settings
    except ImportError as e:
        logger.error("Failed to import API configuration module (app.core.config): %s", e, exc_info=True)
        return
    except Exception as e:
        logger.error("Failed to load API configuration: %s", e, exc_info=True)
        return

    # Validate settings before use
    try:
        api_host = settings.API_HOST
        api_port = settings.API_PORT
    except AttributeError as e:
        logger.error("API configuration missing required settings: %s", e, exc_info=True)
        return

    try:
        logger.info("[api] Starting API server on %s:%s", api_host, api_port)
        uvicorn.run(app, host=api_host, port=api_port, log_level="info")
    except KeyboardInterrupt:
        logger.info("[api] API server interrupted by user")
    except OSError as e:
        if "Address already in use" in str(e) or "address already in use" in str(e).lower():
            logger.error("[api] API server failed: Port %s is already in use. Please choose another port or stop the conflicting service.", api_port)
        else:
            logger.error("[api] API server failed: Network error - %s", e, exc_info=True)
    except Exception as e:
        logger.error("[api] API server failed with unexpected error: %s", e, exc_info=True)


def start_api() -> None:
    """Start API server in a separate thread."""
    global _api_thread
    if _api_thread and _api_thread.is_alive():
        logger.warning("[api] API thread already running")
        return

    _api_shutdown_event.clear()
    _api_thread = threading.Thread(target=_run_api_thread, name="api-server", daemon=True)
    _api_thread.start()


def shutdown_api() -> None:
    """Stop API server."""
    global _api_thread
    _api_shutdown_event.set()
    # Note: uvicorn doesn't have a clean shutdown API, so we rely on daemon thread
    # The thread will be terminated when main process exits
    if _api_thread and _api_thread.is_alive():
        logger.info("[api] Waiting for API thread to stop...")
        _api_thread.join(timeout=5.0)


def start_telemetry() -> None:
    """Start telemetry in a separate thread."""
    import threading
    import time

    from telemetry import connect_mqtt_blocking, setup_mqtt_client, telemetry_loop

    logger = logging.getLogger("telemetry")

    try:
        # Small delay to let bridge connect first
        time.sleep(2)
        setup_mqtt_client()
        connect_mqtt_blocking()

        # Start telemetry loop in a separate thread
        telemetry_thread = threading.Thread(target=telemetry_loop, name="telemetry-loop", daemon=True)
        telemetry_thread.start()
        logger.info("[telemetry] Telemetry loop started")
    except Exception as e:
        logger.error("[telemetry] Failed to start telemetry: %s", e, exc_info=True)
        raise


def stop_telemetry() -> None:
    """Stop telemetry."""
    from telemetry import mqtt_client, shutdown_flag
    logger = logging.getLogger("telemetry")

    shutdown_flag.set()
    if mqtt_client:
        mqtt_client.stop()
    logger.info("[telemetry] Telemetry stopped")


def main() -> None:
    """Main entrypoint: starts bridge, telemetry and API via LifecycleManager."""
    configure_logging()
    logger = logging.getLogger(__name__)

    logger.info("Starting MQTT Command Service (bridge + telemetry)")

    # Initialize lifecycle manager
    lifecycle = LifecycleManager(shutdown_timeout=10.0)

    # Register bridge
    try:
        bridge = MqttCommandBridge()
        set_bridge_instance(bridge)
        lifecycle.register_component(
            name="bridge",
            startup=lambda: bridge.start(),
            shutdown=lambda: bridge.stop()
        )
    except Exception as e:
        logger.error("Failed to initialize bridge: %s", e, exc_info=True)
        sys.exit(1)

    # Register telemetry
    try:
        lifecycle.register_component(
            name="telemetry",
            startup=start_telemetry,
            shutdown=stop_telemetry
        )
    except Exception as e:
        logger.error("Failed to initialize telemetry: %s", e, exc_info=True)
        sys.exit(1)

    # Register API (if enabled)
    from env_settings import get_env_settings
    if get_env_settings().api_enabled:
        try:
            lifecycle.register_component(
                name="api",
                startup=start_api,
                shutdown=shutdown_api
            )
        except Exception as e:
            logger.error("Failed to initialize API: %s", e, exc_info=True)
            sys.exit(1)

    # Wait for shutdown (handles SIGTERM/SIGINT automatically)
    try:
        lifecycle.wait_for_shutdown()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        lifecycle.shutdown()
    finally:
        logger.info("All services stopped.")


if __name__ == "__main__":
    main()
