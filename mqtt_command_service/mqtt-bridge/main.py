#!/usr/bin/env python3
"""mqtt-bridge entrypoint: fetch broker config, create bridge, run until shutdown."""

import logging
import signal
import sys
from types import FrameType

from hub_auth_adapter import HubAuthHttpAdapter

from bridge import MqttCommandBridge
from config import fetch_broker_config, get_settings
from shared.healthcheck import start_heartbeat
from shared.logging_config import configure_logging
from shared.metrics import start_metrics_server

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    settings.warn_unconfigured_services()

    start_metrics_server("mqtt-bridge")

    logger.info("mqtt-bridge starting — fetching broker config from %s", settings.config_api_url)

    try:
        broker_config = fetch_broker_config(settings)
    except RuntimeError as e:
        logger.error("Failed to obtain broker config: %s", e)
        sys.exit(1)

    logger.info(
        "Broker config loaded: broker=%s:%d, robot_id=%s, tls=%s",
        broker_config.mqtt.broker,
        broker_config.mqtt.broker_port,
        broker_config.robot_id,
        broker_config.mqtt.mqtt_use_tls,
    )

    # CRIT-2: Wire up hub-auth integration.
    # The hub-auth service manages JWT tokens; bridge calls it via HTTP.
    auth_manager = None
    if settings.hub_auth_url:
        auth_manager = HubAuthHttpAdapter(
            hub_auth_url=settings.hub_auth_url,
            timeout=settings.hub_auth_timeout,
            internal_service_key=settings.internal_service_key,
        )
        logger.info("Hub-auth integration enabled via %s", settings.hub_auth_url)

    bridge = MqttCommandBridge(config=broker_config, auth_manager=auth_manager)

    # Graceful shutdown on SIGTERM/SIGINT
    # CRIT-1: Only set the flag here — cleanup (bridge.stop())
    # happens in the finally block below, after shutdown_event.wait() exits.
    # Calling bridge.stop() from a signal handler risks deadlock on _stop_lock
    # if a second signal arrives while stop() is already running.
    def _handle_signal(signum: int, frame: FrameType | None) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", sig_name)
        bridge.shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    start_heartbeat(
        bridge.shutdown_event,
        is_healthy=lambda: bridge.mqtt_client.is_connected,
    )

    try:
        bridge.start()
        # Block until shutdown event is set (by signal handler or bridge.stop()).
        # Periodic wakeup to log MQTT disconnection state for diagnostics.
        while not bridge.shutdown_event.wait(timeout=30):
            if not bridge.mqtt_client.is_connected:
                logger.warning("[bridge] MQTT disconnected — waiting for reconnection")
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, shutting down...")
    finally:
        bridge.stop()
        logger.info("mqtt-bridge stopped.")


if __name__ == "__main__":
    main()
