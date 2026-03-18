#!/usr/bin/env python3
"""mqtt-telemetry entrypoint: fetch MQTT config, create service, run until shutdown."""

import logging
import signal
import sys
from types import FrameType

from config import fetch_mqtt_connection_config, get_settings
from shared.healthcheck import start_heartbeat
from shared.logging_config import configure_logging
from shared.metrics import start_metrics_server
from telemetry import TelemetryService

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    start_metrics_server("mqtt-telemetry")

    logger.info("mqtt-telemetry starting — fetching MQTT config from %s", settings.config_api_url)

    try:
        mqtt_config = fetch_mqtt_connection_config(settings)
    except RuntimeError as e:
        logger.error("Failed to obtain MQTT config: %s", e)
        sys.exit(1)

    logger.info(
        "MQTT config loaded: broker=%s:%d, robot_id=%s, tls=%s",
        mqtt_config.broker,
        mqtt_config.broker_port,
        mqtt_config.robot_id,
        mqtt_config.mqtt_use_tls,
    )

    service = TelemetryService(mqtt_config=mqtt_config, settings=settings)

    def _handle_signal(signum: int, frame: FrameType | None) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", sig_name)
        # Only set the flag here — cleanup happens in the finally block
        # after loop() has exited. Calling stop() from a signal handler
        # can race with loop() mid-publish.
        service.shutdown_flag.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    start_heartbeat(service.shutdown_flag, is_healthy=service.is_healthy)

    try:
        service.setup()
        connected = service.connect()

        if not connected:
            logger.warning("MQTT broker not reachable at startup — will retry in background")

        if not service.shutdown_flag.is_set():
            service.loop()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, shutting down...")
    finally:
        # Always call stop() for cleanup — it is idempotent.
        service.stop()
        logger.info("mqtt-telemetry stopped.")


if __name__ == "__main__":
    main()
