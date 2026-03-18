"""Shared Prometheus metrics for all microservices.

Import and use these counters/histograms/gauges across services.
Each service starts a metrics HTTP server on METRICS_PORT (default 9090).
"""

import logging
import os

from prometheus_client import Counter, Gauge, Histogram, Info, start_http_server

logger = logging.getLogger(__name__)

# ---- Counters ----
mqtt_messages_total = Counter(
    "mqtt_messages_total",
    "Total MQTT messages processed",
    ["service", "direction", "topic_type"],
)

auth_degradation_total = Counter(
    "auth_degradation_total",
    "Times auth fell back to empty headers due to token failure",
)

mqtt_legacy_topic_messages_total = Counter(
    "mqtt_legacy_topic_messages_total",
    "Messages received on legacy cmd/ topic pattern (vs commands/)",
)

estop_attempts_total = Counter(
    "estop_attempts_total",
    "E-stop delivery attempts",
    ["result"],  # success, retry, failure
)

# ---- Histograms ----
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["service", "method", "endpoint", "status_code"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

command_processing_duration_seconds = Histogram(
    "command_processing_duration_seconds",
    "Command processing duration in seconds",
    ["command_type", "success"],
)

# ---- Gauges ----
safety_gate_locked = Gauge(
    "safety_gate_locked",
    "Whether the safety gate is currently locked",
    ["service"],
)

active_task_watchers = Gauge(
    "active_task_watchers",
    "Number of active task watcher threads",
    ["service"],
)

# ---- Info ----
service_info = Info("service", "Service version and configuration")


def start_metrics_server(service_name: str) -> None:
    """Start a Prometheus metrics HTTP server on METRICS_PORT (default 9090).

    Safe to call multiple times — silently ignores if port is already bound.
    """
    port = int(os.environ.get("METRICS_PORT", "9090"))
    try:
        start_http_server(port)
        logger.info("[%s] Prometheus metrics server started on port %d", service_name, port)
    except OSError as e:
        logger.warning("[%s] Could not start metrics server on port %d: %s", service_name, port, e)
