"""TelemetryService: HTTP polling -> MQTT publishing.

Accepts config directly — no ConfigService dependency.
"""

import logging
import ssl
import threading
import time
import uuid
from enum import Enum
from typing import Any, NamedTuple

import requests
import websocket

from config import TelemetryServiceSettings
from shared.config_types import MQTTConnectionConfig, TopicSchema
from shared.constants import SERVICE_PORT_ROBOT
from shared.metrics import mqtt_messages_total
from shared.mqtt_client import LightMQTTClient
from shared.utils import now_iso, serialize_mqtt_payload
from telemetry_payload import (
    build_connection_status_payload,
    build_navigation_status_payload,
    build_status_payload,
    build_system_status_payload,
    build_telemetry_payload,
    collect_host_metrics,
    validate_robot_response,
    warmup_psutil,
)

# Health log interval in seconds (independent of poll_interval_seconds).
_HEALTH_LOG_INTERVAL_SECONDS = 300.0
# Log publish/drop stats every N publish+drop operations.
_STATS_LOG_INTERVAL = 50
# Maximum exponent for backoff: 2^_BACKOFF_EXPONENT_CAP * base.
_BACKOFF_EXPONENT_CAP = 4
# Log full traceback on recoverable errors every N consecutive failures.
_ERROR_LOG_FULL_INTERVAL = 12

logger = logging.getLogger(__name__)


def _build_poll_urls(settings: TelemetryServiceSettings) -> dict[str, str]:
    """Build service URLs from settings.

    Remote mode assumes a reverse proxy that maps ``/robot/`` prefix
    to the robot service.  Local mode connects directly to the service
    port.  Both produce the ``/status`` path — the difference is how
    the base URL is constructed.
    """
    if settings.is_remote:
        if not settings.remote_address:
            logger.error("[telemetry] IS_REMOTE=true but REMOTE_ADDRESS is not set; skipping remote service polling")
            return {}
        # Remote: reverse proxy at /robot/ prefix, so full path is /robot/status
        return {"robot": f"http://{settings.remote_address}/robot/status"}
    # Local: direct connection to robot service port, path is /status
    return {"robot": f"http://{settings.local_ip}:{SERVICE_PORT_ROBOT}/status"}


_MAX_RAW_BODY_LEN = 512


class ServiceStatusResult(NamedTuple):
    """Result of fetch_service_status: (status, data, error)."""

    status: str
    data: dict[str, Any] | None
    error: str | None


def fetch_service_status(
    url: str,
    timeout: int,
    session: requests.Session,
) -> ServiceStatusResult:
    """Poll /status endpoint of a service, return ServiceStatusResult(status, data, error).

    *session* is required to avoid leaking unmanaged Session objects.
    """
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code != 200:
            return ServiceStatusResult("error", None, f"http_status_{resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            truncated = resp.text[:_MAX_RAW_BODY_LEN]
            if len(resp.text) > _MAX_RAW_BODY_LEN:
                truncated += "...[truncated]"
            logger.warning(
                "[telemetry] non-JSON response from %s: %s",
                url,
                truncated,
            )
            return ServiceStatusResult("degraded", None, "invalid_json")
        return ServiceStatusResult("online", payload, None)
    except requests.exceptions.Timeout:
        return ServiceStatusResult("error", None, "timeout")
    except requests.exceptions.ConnectionError:
        return ServiceStatusResult("offline", None, "connection_error")
    except Exception as e:
        logger.exception("[telemetry] unexpected error polling %s", url)
        return ServiceStatusResult("error", None, f"exception_{type(e).__name__}")


def _build_telemetry_messages(
    robot_id: str,
    topics: TopicSchema,
    service_data: dict[str, tuple[str, Any, str | None]],
    janus_status: dict[str, bool],
    mqtt_ok: bool,
    poll_seq: int = 0,
    session_id: str = "",
    host_metrics: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Build all MQTT messages from polled service data.

    Deterministic: all payloads share the same *timestamp* (passed from caller)
    to guarantee consistent ordering within a single poll cycle.
    *poll_seq* is a monotonic sequence number for correlation.
    *session_id* distinguishes payloads from different service instances.
    *host_metrics* are pre-collected CPU/RAM/disk metrics (from :func:`collect_host_metrics`).
    """
    messages: list[tuple[str, dict[str, Any]]] = []

    _, robot_data, _ = service_data.get("robot", ("offline", None, None))

    symovo_data: dict[str, Any] | None = None
    if isinstance(robot_data, dict):
        nested = robot_data.get("symovo")
        if isinstance(nested, dict):
            symovo_data = nested

    if symovo_data is not None:
        try:
            nav_payload = build_navigation_status_payload(robot_id, robot_data, symovo_data, timestamp=timestamp)
        except Exception:
            logger.exception("[telemetry] failed to build navigation payload")
            nav_payload = None
        if nav_payload:
            messages.append((topics.navigation_status, nav_payload))

        try:
            telem_payload = build_telemetry_payload(robot_id, robot_data, symovo_data, timestamp=timestamp)
        except Exception:
            logger.exception("[telemetry] failed to build telemetry payload")
            telem_payload = None
        if telem_payload:
            messages.append((topics.telemetry, telem_payload))

        try:
            sys_payload = build_system_status_payload(
                robot_id,
                symovo_data,
                host_metrics=host_metrics,
                timestamp=timestamp,
            )
        except Exception:
            logger.exception("[telemetry] failed to build system status payload")
            sys_payload = None
        if sys_payload:
            messages.append((topics.system_status, sys_payload))

    conn_payload = build_connection_status_payload(robot_id, janus_status, mqtt_ok, timestamp=timestamp)
    messages.append((topics.connection_status, conn_payload))

    for name, (status, data, error) in service_data.items():
        legacy_payload = build_status_payload(robot_id, name, status, data, error, timestamp=timestamp)
        messages.append((topics.status(name), legacy_payload))

    # Inject request-level metadata into every payload.
    # Copy each dict to avoid mutating objects returned by payload builders.
    # message_id enables downstream deduplication when QoS=1 causes redelivery.
    meta_base: dict[str, Any] = {"poll_seq": poll_seq}
    if session_id:
        meta_base["session_id"] = session_id
    return [
        (topic, {**payload, **meta_base, "message_id": f"{session_id}:{poll_seq}:{i}"})
        for i, (topic, payload) in enumerate(messages)
    ]


class _ServiceState(Enum):
    """Explicit lifecycle states for TelemetryService.

    One-way state machine — no recovery path by design.
    Container orchestrator (Docker/K8s) handles restarts.

    CREATED → READY → CONNECTED  → RUNNING → STOPPED
                   ↘ CONNECTING ↗

    STOPPED is reachable from *any* state via ``stop()`` (graceful shutdown).
    """

    CREATED = "created"
    READY = "ready"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RUNNING = "running"
    STOPPED = "stopped"


# Health-check thresholds (multiples of poll_interval_seconds).
# Startup grace: allow N poll intervals before requiring a successful publish.
_HEALTH_STARTUP_GRACE_MULTIPLIER = 6
# Staleness: no publish within N intervals + buffer → unhealthy.
_HEALTH_STALENESS_MULTIPLIER = 3
_HEALTH_STALENESS_BUFFER_SECONDS = 5

# Transient/network errors safe to retry.
# ConnectionError and TimeoutError cover their subclasses
# (ConnectionRefusedError, ConnectionResetError, etc.).
_RECOVERABLE_ERRORS = (
    requests.exceptions.RequestException,
    ConnectionError,
    TimeoutError,
)


class TelemetryService:
    """Encapsulates all telemetry subsystem state and behaviour."""

    _RESTART_DELAY_BASE = 5
    _RESTART_DELAY_MAX = 60

    def __init__(
        self,
        mqtt_config: MQTTConnectionConfig,
        settings: TelemetryServiceSettings,
    ) -> None:
        self._mqtt_config = mqtt_config
        self._settings = settings
        self._mqtt_client: LightMQTTClient | None = None
        self._state = _ServiceState.CREATED
        self._state_lock = threading.Lock()
        self.shutdown_flag = threading.Event()

        self._session: requests.Session | None = None
        self._publish_count: int = 0
        self._drop_count: int = 0
        self._last_successful_publish: float = 0.0
        self._stats_lock = threading.Lock()
        self._session_consecutive_errors: int = 0
        self._session_error_threshold = 5

        # Pre-compute immutable values
        self._service_urls = _build_poll_urls(self._settings)
        self._topics = TopicSchema(mqtt_config.robot_id)

        self._startup_time: float = time.time()
        # Unique per-instance ID for log/payload correlation.
        # Distinguishes payloads if two instances overlap during deploys.
        self._session_id: str = uuid.uuid4().hex[:8]

        self._janus_status: dict[str, bool] = {"depth": False, "color": False}
        self._janus_last_check: float = 0.0
        self._janus_lock = threading.Lock()
        self._janus_thread: threading.Thread | None = None
        self._janus_consecutive_failures: int = 0
        # Backoff cap for Janus WS checks: 5 minutes max between checks
        # when both endpoints are consistently down.
        self._JANUS_BACKOFF_MAX: float = 300.0

        if self._settings.websocket_tls_insecure:
            if self._settings.is_remote:
                # Allowed only when ALLOW_TLS_INSECURE_REMOTE=true (validated in config).
                logger.critical(
                    "[telemetry] WEBSOCKET_TLS_INSECURE=true in remote mode — "
                    "certificate verification disabled (explicit override active)"
                )
            else:
                logger.warning("[telemetry] TLS certificate verification DISABLED for WebSocket connections")

        warmup_psutil()

    def _require_state(self, *allowed: _ServiceState) -> None:
        """Raise RuntimeError if current state is not one of *allowed*."""
        with self._state_lock:
            if self._state not in allowed:
                raise RuntimeError(
                    f"TelemetryService is in state {self._state.value}, expected one of {[s.value for s in allowed]}"
                )

    def _transition_state(
        self,
        target: _ServiceState,
        *allowed: _ServiceState,
    ) -> None:
        """Atomically check current state and transition to *target*.

        Eliminates the TOCTOU gap between a separate _require_state()
        call and a later ``with self._state_lock: self._state = ...``.
        """
        with self._state_lock:
            if self._state not in allowed:
                raise RuntimeError(
                    f"TelemetryService is in state {self._state.value}, expected one of {[s.value for s in allowed]}"
                )
            self._state = target

    def setup(self) -> None:
        """Create MQTT client. Must be called exactly once after __init__."""
        self._transition_state(_ServiceState.READY, _ServiceState.CREATED)
        try:
            self._mqtt_client = LightMQTTClient(
                config=self._mqtt_config,
                component_name="telemetry",
            )
        except Exception:
            # Roll back state so the service can be retried or stopped cleanly.
            with self._state_lock:
                self._state = _ServiceState.CREATED
            raise

    def connect(self) -> bool:
        """Blocking connection to MQTT broker.

        LightMQTTClient.start() already waits for connection internally,
        so we only check the result here.  Transitions to CONNECTED on
        success, or CONNECTING on failure (reconnect thread keeps trying
        in the background).  The caller must check the return value.
        """
        if self._mqtt_client is None:
            raise RuntimeError("setup() must be called before connect()")
        self._mqtt_client.start()

        if self._mqtt_client.is_connected:
            self._transition_state(_ServiceState.CONNECTED, _ServiceState.READY)
            logger.info("[telemetry] MQTT connected successfully")
            return True

        # LightMQTTClient has its own reconnect thread — it will keep
        # trying in the background.  Transition to CONNECTING (not
        # CONNECTED) so the state honestly reflects "not yet connected".
        self._transition_state(_ServiceState.CONNECTING, _ServiceState.READY)
        logger.error(
            "[telemetry] MQTT initial connection failed — service will "
            "start but messages will be dropped until broker is reachable"
        )
        return False

    def stop(self) -> None:
        """Stop the telemetry service (idempotent).

        Must be called from the main thread (not a signal handler) so
        that mqtt_client.stop() and thread joins are safe.
        """
        with self._state_lock:
            if self._state == _ServiceState.STOPPED:
                return
            self._state = _ServiceState.STOPPED
            self.shutdown_flag.set()
        # Wait for Janus probe thread to finish (bounded by WS timeout).
        if self._janus_thread is not None and self._janus_thread.is_alive():
            self._janus_thread.join(timeout=5.0)
        if self._mqtt_client:
            self._mqtt_client.stop()
        # HTTP session is cleaned up in loop() finally block.

    def _reset_session(self) -> None:
        """Close the current HTTP session and clear it for lazy recreation."""
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                logger.debug("[telemetry] Error closing HTTP session", exc_info=True)
            self._session = None

    def loop(self) -> None:
        """Main polling and publishing loop (blocks until shutdown_flag is set)."""
        self._transition_state(
            _ServiceState.RUNNING,
            _ServiceState.CONNECTED,
            _ServiceState.CONNECTING,
        )

        poll_interval = self._settings.poll_interval_seconds
        http_timeout = self._settings.telemetry_http_timeout
        logger.info("[telemetry] starting telemetry loop, poll_interval=%ss", poll_interval)

        consecutive_errors = 0
        poll_seq = 0
        last_health_log_time = 0.0

        try:
            while not self.shutdown_flag.is_set():
                try:
                    start_ts = time.time()
                    poll_seq += 1
                    robot_id = self._mqtt_config.robot_id
                    topics = self._topics

                    service_data: dict[str, tuple[str, Any, str | None]] = {}
                    if self._session is None:
                        self._session = requests.Session()
                    for name, url in self._service_urls.items():
                        status, data, error = fetch_service_status(url, http_timeout, self._session)
                        service_data[name] = (status, data, error)

                    # Session recovery: if all services errored, the session may be broken.
                    all_errored = service_data and all(s in ("error", "offline") for s, _, _ in service_data.values())
                    if all_errored:
                        self._session_consecutive_errors += 1
                        if self._session_consecutive_errors >= self._session_error_threshold:
                            logger.warning(
                                "[telemetry] %d consecutive polling errors — recreating HTTP session",
                                self._session_consecutive_errors,
                            )
                            self._reset_session()
                            self._session_consecutive_errors = 0
                    else:
                        self._session_consecutive_errors = 0

                    # Boundary validation: check robot response structure.
                    _, robot_data_raw, _ = service_data.get("robot", ("offline", None, None))
                    if isinstance(robot_data_raw, dict):
                        schema_warnings = validate_robot_response(robot_data_raw)
                        if schema_warnings:
                            logger.warning(
                                "[telemetry] robot response schema issues: %s",
                                "; ".join(schema_warnings),
                            )

                    janus_status = self._check_janus_ws()
                    mqtt_ok = self._mqtt_client.is_connected if self._mqtt_client else False
                    host_metrics = collect_host_metrics()

                    poll_timestamp = now_iso()
                    messages = _build_telemetry_messages(
                        robot_id,
                        topics,
                        service_data,
                        janus_status,
                        mqtt_ok,
                        poll_seq=poll_seq,
                        session_id=self._session_id,
                        host_metrics=host_metrics,
                        timestamp=poll_timestamp,
                    )
                    for topic, payload in messages:
                        self._publish(topic, payload)

                    # Periodic health log + drop stats
                    with self._stats_lock:
                        if self._drop_count > 0 and (self._publish_count + self._drop_count) % _STATS_LOG_INTERVAL == 0:
                            logger.warning(
                                "[telemetry] publish stats: sent=%d, dropped=%d",
                                self._publish_count,
                                self._drop_count,
                            )
                        now_ts = time.time()
                        if now_ts - last_health_log_time >= _HEALTH_LOG_INTERVAL_SECONDS:
                            last_health_log_time = now_ts
                            logger.info(
                                "[telemetry] heartbeat: poll_seq=%d, sent=%d, dropped=%d, mqtt=%s",
                                poll_seq,
                                self._publish_count,
                                self._drop_count,
                                "connected" if mqtt_ok else "disconnected",
                            )

                    elapsed = time.time() - start_ts
                    sleep_time = max(0, poll_interval - elapsed)
                    if sleep_time > 0:
                        self.shutdown_flag.wait(timeout=sleep_time)
                    consecutive_errors = 0

                except _RECOVERABLE_ERRORS:
                    consecutive_errors += 1
                    # Discard potentially broken session on network errors
                    self._reset_session()
                    delay = min(
                        self._RESTART_DELAY_BASE * (2 ** min(consecutive_errors - 1, _BACKOFF_EXPONENT_CAP)),
                        self._RESTART_DELAY_MAX,
                    )
                    if consecutive_errors == 1 or consecutive_errors % _ERROR_LOG_FULL_INTERVAL == 0:
                        logger.exception(
                            "[telemetry] recoverable error (consecutive=%d), retrying in %ds",
                            consecutive_errors,
                            delay,
                        )
                    else:
                        logger.debug(
                            "[telemetry] loop still failing (consecutive=%d)",
                            consecutive_errors,
                        )
                    if not self.shutdown_flag.is_set():
                        self.shutdown_flag.wait(timeout=delay)

                except Exception:
                    logger.exception("[telemetry] non-recoverable error in telemetry loop — stopping service")
                    self.stop()
                    raise
        finally:
            self._reset_session()

    def _publish(self, topic: str, payload: dict[str, Any]) -> bool:
        if self._mqtt_client is None or not self._mqtt_client.is_connected:
            with self._stats_lock:
                self._drop_count += 1
            return False

        # Serialize here (compact JSON) so we can validate size before
        # handing to LightMQTTClient.  The client receives a str and
        # passes it through without re-serializing.
        result = serialize_mqtt_payload(payload)
        if result is None:
            with self._stats_lock:
                self._drop_count += 1
            logger.warning(
                "[telemetry] dropping oversized payload on %s",
                topic,
            )
            return False
        message, _ = result

        ok = self._mqtt_client.publish(
            topic,
            message,
            qos=self._mqtt_config.mqtt_publish_qos,
            retain=False,
        )
        with self._stats_lock:
            if ok:
                self._publish_count += 1
                self._last_successful_publish = time.time()
                mqtt_messages_total.labels(service="telemetry", direction="outbound", topic_type="telemetry").inc()
            else:
                self._drop_count += 1
        return ok

    def is_healthy(self) -> bool:
        """Return True if the service is functioning usefully.

        Used by the healthcheck heartbeat to reflect real service state.
        Unhealthy when: MQTT disconnected for too long, or no successful
        publish within 3 poll intervals.
        Use :attr:`health_reason` for a human-readable explanation.
        """
        _, reason = self._health_check()
        return reason is None

    def health_reason(self) -> str:
        """Return a human-readable explanation of the current health state.

        Returns ``"ok"`` when healthy, otherwise a short diagnostic string.
        """
        _, reason = self._health_check()
        return reason or "ok"

    def _health_check(self) -> tuple[bool, str | None]:
        """Internal health check returning ``(is_healthy, reason_or_none)``."""
        if self._mqtt_client is None:
            return False, "mqtt_client_not_initialized"
        if not self._mqtt_client.is_connected:
            return False, "mqtt_disconnected"
        with self._stats_lock:
            if self._last_successful_publish == 0.0:
                startup_age = time.time() - self._startup_time
                grace = self._settings.poll_interval_seconds * _HEALTH_STARTUP_GRACE_MULTIPLIER
                if startup_age < grace:
                    return True, None
                return False, f"no_publish_after_{grace:.0f}s_startup_grace"
            age = time.time() - self._last_successful_publish
        max_age = self._settings.poll_interval_seconds * _HEALTH_STALENESS_MULTIPLIER + _HEALTH_STALENESS_BUFFER_SECONDS
        if age < max_age:
            return True, None
        return False, f"last_publish_{age:.0f}s_ago_exceeds_{max_age:.0f}s_threshold"

    def _check_janus_ws(self) -> dict[str, bool]:
        """Return cached per-endpoint Janus WS status; trigger a background probe if interval elapsed.

        Uses exponential backoff when both endpoints are consistently down:
        base interval → 2x → 4x → ... up to _JANUS_BACKOFF_MAX (5 min).
        Resets to base interval as soon as any endpoint recovers.
        """
        now = time.time()
        with self._janus_lock:
            # Compute effective interval with backoff on consecutive failures
            base_interval = self._settings.websocket_check_interval
            if self._janus_consecutive_failures > 0:
                effective_interval = min(
                    base_interval * (2 ** min(self._janus_consecutive_failures, _BACKOFF_EXPONENT_CAP)),
                    self._JANUS_BACKOFF_MAX,
                )
            else:
                effective_interval = base_interval

            if now - self._janus_last_check < effective_interval:
                return dict(self._janus_status)
            if self._janus_thread is not None and self._janus_thread.is_alive():
                # Previous probe still running — don't spawn another.
                return dict(self._janus_status)
            # Update _janus_last_check at spawn time to prevent busy-loop
            # if probe hangs: we won't re-enter this branch until the
            # next interval elapses regardless of probe outcome.
            self._janus_last_check = now
            # Snapshot status before spawning probe — the return must be
            # inside the lock to avoid a torn read from the background thread.
            snapshot = dict(self._janus_status)
            self._janus_thread = threading.Thread(
                target=self._probe_janus_ws,
                daemon=True,
            )
            self._janus_thread.start()
        return snapshot

    def _probe_janus_ws(self) -> None:
        """Blocking WebSocket connectivity check to Janus gateway (runs in background thread).

        Checks ALL endpoints independently, reporting per-endpoint status.
        Respects shutdown_flag to avoid blocking process exit.
        Logs on state transitions (up→down, down→up) to avoid log noise.
        Tracks consecutive all-failed checks for backoff in _check_janus_ws.
        """
        endpoints = {
            "depth": self._settings.effective_janus_ws_depth,
            "color": self._settings.effective_janus_ws_color,
        }
        results: dict[str, bool] = {}
        for name, ws_url in endpoints.items():
            if self.shutdown_flag.is_set():
                return  # Don't update status on abort
            if not ws_url:
                results[name] = False
                continue
            try:
                sslopt: dict[str, Any] = {}
                if ws_url.startswith("wss://"):
                    sslopt = {
                        "cert_reqs": ssl.CERT_NONE if self._settings.websocket_tls_insecure else ssl.CERT_REQUIRED
                    }
                ws = websocket.create_connection(ws_url, timeout=self._settings.websocket_check_timeout, sslopt=sslopt)
                try:
                    ws.close()
                except Exception:
                    logger.debug("[telemetry] Janus WS close error for %s", name, exc_info=True)
                results[name] = True
            except Exception as e:
                results[name] = False
                # Log on state transition (was up, now down) or first failure.
                # Subsequent repeated failures are suppressed to reduce log noise.
                with self._janus_lock:
                    was_up = self._janus_status.get(name, False)
                    consec = self._janus_consecutive_failures
                if was_up or consec == 0:
                    logger.warning("[telemetry] Janus WS check failed for %s (%s): %s", name, ws_url, e)
                elif consec % 10 == 0:
                    # Periodic reminder every 10 backoff cycles
                    logger.warning(
                        "[telemetry] Janus WS still down for %s (%s) — %d consecutive all-failed checks",
                        name, ws_url, consec,
                    )

        with self._janus_lock:
            prev_status = self._janus_status
            self._janus_status = results
            all_failed = not any(results.values())
            any_recovered = any(results.get(k, False) and not prev_status.get(k, False) for k in results)
            if all_failed:
                self._janus_consecutive_failures += 1
                if self._janus_consecutive_failures == 1:
                    logger.info(
                        "[telemetry] All Janus WS endpoints down — entering backoff "
                        "(next check in %.0fs)",
                        min(
                            self._settings.websocket_check_interval * 2,
                            self._JANUS_BACKOFF_MAX,
                        ),
                    )
            else:
                if self._janus_consecutive_failures > 0:
                    logger.info(
                        "[telemetry] Janus WS recovered after %d consecutive failures",
                        self._janus_consecutive_failures,
                    )
                self._janus_consecutive_failures = 0
            if any_recovered:
                for name, ok in results.items():
                    if ok and not prev_status.get(name, False):
                        logger.info("[telemetry] Janus WS %s is back up", name)
