"""MqttCommandBridge — composition-based MQTT command bridge.

Config changes require container restart.
Uses LightMQTTClient from shared library.
"""

import logging
import re
import threading
import time
from datetime import datetime
from typing import Any

from bridge_protocols import BridgeDependencies
from command_dedup import CommandDeduplicator
from command_dispatcher import (
    SAFETY_GATED_COMMANDS,
    TIMESTAMP_VALIDATED_COMMANDS,
    CommandDispatcher,
)
from dateutil import parser as date_parser
from heartbeat_publisher import HeartbeatPublisher
from http_executor import HttpExecutor
from mqtt_publisher import MQTTResponsePublisher
from paho.mqtt.client import MQTTMessage
from path_validator import build_http_url, compute_allowed_hosts, validate_path
from payload_models import AckPayload, ErrorDetail
from payload_validation import validate_dict_payload, validate_headers, validate_required_field
from pending_results import PendingResultQueue
from response_publisher import BridgeResponsePublisher
from safety_gate import SafetyGate
from task_watcher import TaskWatcher

from shared.config_types import AuthManagerProtocol, BridgeConfig, TopicSchema
from shared.constants import get_cert_path
from shared.env import validate_robot_id
from shared.constants import (
    BRIDGE_DRAIN_POLL_INTERVAL_SECONDS,
    BRIDGE_DRAIN_TIMEOUT_SECONDS,
    COMMAND_HISTORY_TTL_SECONDS,
    DEFAULT_COMMAND_SERVICE,
    MAX_MQTT_PAYLOAD_SIZE,
    ROBOT_ID_ESCALATE_AFTER,
    ROBOT_ID_FALLBACK_WARN_INTERVAL,
    SAFETY_HEARTBEAT_TIMEOUT_MIN,
    SAFETY_HEARTBEAT_TIMEOUT_MULTIPLIER,
    MessageType,
    NavigationState,
    StatusType,
)
from shared.metrics import mqtt_legacy_topic_messages_total, mqtt_messages_total
from shared.mqtt_client import LightMQTTClient
from shared.utils import now_iso, safe_json_loads

logger = logging.getLogger(__name__)


class MqttCommandBridge:
    """Composition-based MQTT command bridge.

    Owns CommandDispatcher, TaskWatcher, HttpExecutor, and all infrastructure.
    No mixin inheritance — dependencies are wired explicitly.
    """

    def __init__(
        self,
        config: BridgeConfig,
        auth_manager: AuthManagerProtocol | None = None,
    ) -> None:
        self.config = config

        self.mqtt_client = LightMQTTClient(
            config=config.mqtt,
            component_name="bridge",
        )

        self._shutdown = threading.Event()
        self._auth_manager = auth_manager
        self._started_at = time.time()

        self._safety_gate = SafetyGate(
            heartbeat_timeout=max(
                SAFETY_HEARTBEAT_TIMEOUT_MIN,
                self.config.status_heartbeat_interval * SAFETY_HEARTBEAT_TIMEOUT_MULTIPLIER,
            ),
            startup_grace_seconds=config.safety_gate_startup_grace,
        )
        self._dedup = CommandDeduplicator(
            history_ttl=COMMAND_HISTORY_TTL_SECONDS,
            max_history=1000,
        )
        self._service_dedup = CommandDeduplicator(
            history_ttl=COMMAND_HISTORY_TTL_SECONDS,
            max_history=1000,
        )

        self._stop_lock = threading.Lock()
        self._stopped = threading.Event()
        self._robot_id_lock = threading.Lock()

        self._last_robot_id_warn_ts: float = 0.0
        self._robot_id_fallback_count: int = 0

        # mTLS identity: extract robot_id from client certificate CN
        self._cert_cn: str | None = None
        if config.mqtt.auth_mode in ("mtls", "mtls_password"):
            try:
                from shared.cert_identity import extract_cn_from_cert

                cert_path = config.mqtt.mqtt_certfile or str(get_cert_path("client.crt"))
                self._cert_cn = extract_cn_from_cert(cert_path)
                logger.info("[bridge] robot_id from cert CN: %s", self._cert_cn)
            except (ValueError, FileNotFoundError) as e:
                logger.error("[bridge] Cannot extract robot_id from client cert: %s — falling back to config", e)

        self._current_robot_id: str = self._effective_robot_id()
        self._topics = TopicSchema(self._current_robot_id)
        self._allowed_hosts = compute_allowed_hosts(config.services)
        if self._allowed_hosts:
            logger.info(
                "[bridge] SSRF allowlist: %s (from %d configured services)",
                ", ".join(sorted(self._allowed_hosts)),
                len(config.services),
            )
        else:
            logger.warning(
                "[bridge] SSRF allowlist is EMPTY — no services configured. "
                "All HTTP requests to private IPs will be blocked."
            )

        self._publisher = MQTTResponsePublisher(
            mqtt_client=self.mqtt_client,
            config=self.config,
        )

        # Response publisher first (pending queue wired below via set_pending_queue)
        self._resp_publisher = BridgeResponsePublisher(
            publisher=self._publisher,
            mqtt_client=self.mqtt_client,
            get_topics_fn=self._get_topics,
            get_robot_id_fn=self._effective_robot_id,
            extract_service_fn=self._extract_service,
        )

        # Pending results queue (delivers responses when MQTT reconnects)
        self._pending_queue = PendingResultQueue(
            shutdown_event=self._shutdown,
            publish_json_fn=self._resp_publisher.publish_json,
            get_resp_topic_fn=lambda service: f"{self._get_topics().resp_base}/{service}",
            mqtt_is_connected_fn=lambda: self.mqtt_client.is_connected,
        )
        # Wire the pending queue — must happen before mqtt_client.start()
        self._resp_publisher.set_pending_queue(self._pending_queue.queue)

        # Typed dependency adapter — satisfies BridgeServices protocol.
        # Components are passed directly to avoid reaching through private attributes.
        _deps = BridgeDependencies(
            resp_publisher=self._resp_publisher,
            dedup=self._dedup,
            http_executor=None,  # Set below after HttpExecutor is created
            auth_headers_fn=self._auth_headers,
            services=config.services,
            allowed_hosts=self._allowed_hosts,
        )

        self._task_watcher = TaskWatcher(
            task_poll_interval=config.task_poll_interval,
            task_poll_timeout=config.task_poll_timeout,
            http_timeout=config.http_timeout,
            shutdown_event=self._shutdown,
            deps=_deps,
        )

        # HTTP executor (Protocol-based DI, no lambdas)
        self._http_executor = HttpExecutor(
            config=config,
            shutdown_event=self._shutdown,
            deps=_deps,
            service_dedup=self._service_dedup,
            task_watcher=self._task_watcher,
        )
        # Wire http_executor into deps (circular: deps needs executor for
        # get_http_session, executor needs deps for response publishing)
        _deps.set_http_executor(self._http_executor)

        self._command_dispatcher = CommandDispatcher(
            deps=_deps,
            http_timeout=config.http_timeout,
            estop_alert_callback=self._on_estop_failure,
        )
        self._command_dispatcher.set_shutdown_event(self._shutdown)

        self._heartbeat = HeartbeatPublisher(
            interval=self.config.status_heartbeat_interval,
            shutdown_event=self._shutdown,
            publish_system_status=self._publish_system_status,
            publish_connection_status=self._publish_connection_status,
        )

    # ---- Lifecycle -------------------------------------------------
    def start(self) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.clear()

        # DEPRECATED: legacy cmd/ topic — monitor mqtt_legacy_topic_messages_total
        # counter; remove when it reads 0 for 30+ days across all deployments.
        _legacy_cmd_topic = f"aroc/robot/{self._current_robot_id}/cmd/+"
        self.mqtt_client.subscribe(_legacy_cmd_topic, self._handle_mqtt_message)
        self.mqtt_client.subscribe(self._topics.command_pattern, self._handle_mqtt_message)
        self.mqtt_client.subscribe(self._topics.safety_status, self._handle_safety_message)

        self.mqtt_client.start()
        self.mqtt_client.add_on_connect_callback(self._pending_queue.flush)

        self._heartbeat.start()
        self._pending_queue.start_flush_thread()
        logger.info("[bridge] Bridge started", extra={"service": "mqtt-bridge"})

    def stop(self) -> None:
        """Graceful shutdown with enforced dependency ordering.

        Shutdown dependency graph (each step depends on all previous):

        1. Set _shutdown event — signals all threads to stop accepting work
        2. Drain in-flight commands — wait for CommandDeduplicator.in_flight_count == 0
        3. Join e-stop threads — safety-critical, must complete delivery attempts
        4. Stop task watchers — polling threads observe _shutdown via wait()
        5. Stop heartbeat publisher — no more status publishes
        6. Shutdown HTTP executor — drain thread pool, close HTTP sessions
        7. Stop MQTT client — disconnect broker, shutdown handler executor
           (must be AFTER http_executor so in-flight HTTP responses can
           still be published via MQTT)
        8. Close auth manager — HTTP session to hub-auth

        WARNING: Reordering steps 6/7 will cause lost MQTT responses.
        """
        acquired = self._stop_lock.acquire(blocking=False)
        if not acquired:
            self._stopped.wait(timeout=BRIDGE_DRAIN_TIMEOUT_SECONDS * 2)
            return
        try:
            if self._stopped.is_set():
                return
            self._shutdown.set()                                  # (1)

            drain_deadline = time.time() + BRIDGE_DRAIN_TIMEOUT_SECONDS
            while time.time() < drain_deadline:                   # (2)
                if self._dedup.in_flight_count == 0:
                    break
                time.sleep(BRIDGE_DRAIN_POLL_INTERVAL_SECONDS)

            self._command_dispatcher.join_estop_threads()          # (3)
            self._task_watcher.stop_all()                          # (4)
            self._heartbeat.stop()                                 # (5)
            self._http_executor.shutdown()                         # (6)
            self.mqtt_client.stop()                                # (7)

            if self._auth_manager:
                self._auth_manager.close()                         # (8)
            logger.info("[bridge] Bridge stopped")
        finally:
            self._stopped.set()
            self._stop_lock.release()

    @property
    def shutdown_event(self) -> threading.Event:
        return self._shutdown

    # ---- Robot ID change detection ──────────────────────────────
    def _get_topics(self) -> TopicSchema:
        with self._robot_id_lock:
            return self._topics

    def _check_robot_id_change(self) -> None:
        new_id = self._effective_robot_id()
        with self._robot_id_lock:
            if new_id == self._current_robot_id:
                return
            old_id = self._current_robot_id
            old_topics = self._topics
            new_topics = TopicSchema(new_id)
            self._current_robot_id = new_id
            self._topics = new_topics

        logger.warning("[bridge] Robot ID changed: %s -> %s — re-subscribing MQTT topics", old_id, new_id)

        # Subscribe new topics FIRST to avoid message loss window.
        # Brief overlap (both old and new active) is safe — CommandDeduplicator
        # handles any duplicates during the transition.
        try:
            self.mqtt_client.subscribe(f"aroc/robot/{new_id}/cmd/+", self._handle_mqtt_message)
            self.mqtt_client.subscribe(new_topics.command_pattern, self._handle_mqtt_message)
            self.mqtt_client.subscribe(new_topics.safety_status, self._handle_safety_message)
        except Exception:
            logger.exception(
                "[bridge] Error subscribing new topics for robot_id=%s — keeping old topics",
                new_id,
            )
            with self._robot_id_lock:
                self._current_robot_id = old_id
                self._topics = old_topics
            return  # Old subscriptions still active, no harm done

        # Unsubscribe old topics (best-effort, new topics already active)
        try:
            self.mqtt_client.unsubscribe(f"aroc/robot/{old_id}/cmd/+")
            self.mqtt_client.unsubscribe(old_topics.command_pattern)
            self.mqtt_client.unsubscribe(old_topics.safety_status)
        except Exception:
            logger.exception("[bridge] Error unsubscribing old topics for robot_id=%s", old_id)

    # ---- Safety ───────────────────────────────────────────────────
    def _handle_safety_message(self, message: MQTTMessage) -> None:
        return self._safety_gate.handle_safety_message(message)

    def _is_safety_locked(self) -> bool:
        return self._safety_gate.is_locked()

    # ---- MQTT message handling ────────────────────────────────────
    def _handle_mqtt_message(self, message: MQTTMessage) -> None:
        try:
            mqtt_messages_total.labels(service="bridge", direction="inbound", topic_type="command").inc()
            if "/cmd/" in message.topic and "/commands/" not in message.topic:
                mqtt_legacy_topic_messages_total.inc()
            self._handle_incoming_message(topic=message.topic, payload_bytes=message.payload)
        except Exception as e:
            logger.exception("[bridge] Failed to process MQTT message on topic %s", message.topic)
            self._resp_publisher.publish_processing_error(message.topic, str(e))

    def _handle_incoming_message(self, topic: str, payload_bytes: bytes) -> None:
        if len(payload_bytes) > MAX_MQTT_PAYLOAD_SIZE:
            self._resp_publisher.reject_oversized_payload(topic, len(payload_bytes))
            return

        payload = payload_bytes.decode("utf-8", errors="replace")

        command_name = self._extract_command(topic)
        if command_name:
            data, error = safe_json_loads(payload)
            if error:
                self._resp_publisher.publish_json_parse_error(topic, error)
                return
            self._handle_command_message(command_name, data)
            return

        service = self._extract_service(topic)
        if not service:
            return

        data, error = safe_json_loads(payload)
        if error:
            self._resp_publisher.publish_error_response(
                service, 400, "Invalid JSON payload",
                ErrorDetail.invalid_json("Payload is not a JSON object"),
            )
            return

        data_dict, validation_error = validate_dict_payload(data, error_context="Service message")
        if validation_error:
            self._resp_publisher.publish_error_response(
                service, 400, "Invalid JSON payload",
                ErrorDetail.invalid_json("Payload is not a JSON object"),
            )
            return

        self._handle_service_message(service, data_dict)

    # ---- Service handling ─────────────────────────────────────────
    def _handle_service_message(self, service: str, data_dict: dict[str, Any]) -> None:
        method = str(data_dict.get("method", "GET")).upper()
        if method != "GET" and self._is_safety_locked():
            reason = (self._safety_gate.state or {}).get("reason", "unknown")
            self._resp_publisher.publish_error_response(
                service, 403,
                f"Service request rejected: robot is in safety lockout ({reason}).",
                ErrorDetail.command_error("Safety lockout active"),
                request_id=data_dict.get("request_id"),
            )
            return

        if service not in self.config.services:
            self._resp_publisher.publish_error_response(
                service, 400, f"Unknown service '{service}'",
                ErrorDetail.routing_error("Unknown service"),
                request_id=data_dict.get("request_id"),
            )
            return

        request_id = data_dict.get("request_id")

        if request_id:
            cached = self._service_dedup.get(request_id)
            if cached is not None:
                logger.info("[bridge] Replaying cached service response for request_id=%s", request_id)
                self._resp_publisher.send_response(service, cached)
                return
            if self._service_dedup.was_processed(request_id):
                logger.debug("[bridge] Service request %s already processed, skipping", request_id)
                return
            if not self._service_dedup.try_start(request_id):
                logger.debug("[bridge] Service request %s already in flight, skipping", request_id)
                return

        raw_path = str(data_dict.get("path", "/"))
        body = data_dict.get("body")

        service_cfg = self.config.services.get(service)
        allowed_prefixes = service_cfg.allowed_path_prefixes if service_cfg else ()
        path = validate_path(raw_path, allowed_prefixes=allowed_prefixes)
        if path is None:
            ack = AckPayload(
                request_id=data_dict.get("request_id"), service=service,
                success=False, status_code=400, body={"detail": "Invalid path"},
                error=ErrorDetail.command_error("Path contains forbidden characters"),
            )
            self._resp_publisher.send_response(service, ack.to_dict())
            return

        headers, headers_error = validate_headers(data_dict.get("headers"), error_context="Service request")
        if headers_error:
            ack = AckPayload(
                request_id=request_id, service=service,
                success=False, status_code=400, body={"detail": headers_error},
                error=ErrorDetail.invalid_json(headers_error),
            )
            self._resp_publisher.send_response(service, ack.to_dict())
            return

        try:
            timeout = self._http_executor.resolve_timeout(data_dict, service, path)
        except ValueError:
            ack = AckPayload(
                request_id=data_dict.get("request_id"), service=service,
                success=False, status_code=400,
                body={"detail": "timeout must be a number"},
                error=ErrorDetail.command_error("Invalid timeout type"),
            )
            self._resp_publisher.send_response(service, ack.to_dict())
            return

        url = build_http_url(service, path, self.config.services, self._allowed_hosts)
        if not url:
            self._resp_publisher.publish_error_response(
                service, 500, f"No HTTP mapping for service '{service}'",
                ErrorDetail.routing_error("No base_url configured"),
                request_id=request_id,
            )
            return

        self._http_executor.submit(
            service=service, request_id=request_id, method=method,
            url=url, headers=headers, body=body, timeout=timeout, context=None,
        )

    # ---- Command handling ─────────────────────────────────────────
    def _handle_command_message(self, command: str, data: Any) -> None:
        data_dict, validation_error = validate_dict_payload(data, error_context="Command message")
        if validation_error:
            self._resp_publisher.publish_command_error(command, None, validation_error)
            return

        command_name = command.strip()
        command_key = command_name.lower()

        # Accept both "command_id" and "request_id" for backwards compatibility:
        # older clients send "request_id", newer clients send "command_id".
        command_id_raw = data_dict.get("command_id") or data_dict.get("request_id")
        command_id, id_error = validate_required_field(
            {"command_id": command_id_raw} if command_id_raw is not None else {},
            "command_id", error_context="Command",
        )
        if id_error:
            self._resp_publisher.publish_command_error(command_name, None, id_error)
            return
        command_id = str(command_id)

        cached_payload = self._dedup.get(command_id)
        if cached_payload:
            service_name = cached_payload.get("service", DEFAULT_COMMAND_SERVICE)
            self._resp_publisher.send_response(service_name, cached_payload)
            self._resp_publisher.publish_navigation_status(
                state=NavigationState.DUPLICATE.value,
                success=cached_payload.get("success"),
                detail=cached_payload,
                context={
                    "command_name": command_key, "command_id": command_id,
                    "status_type": StatusType.NAVIGATION.value,
                    "target_id": data_dict.get("target_id"),
                },
            )
            return

        if not self._dedup.try_start(command_id):
            return

        try:
            validated_timestamp: str | None = None
            if command_key in TIMESTAMP_VALIDATED_COMMANDS:
                try:
                    validated_timestamp = self._validate_timestamp(
                        data_dict.get("timestamp"), command_name, command_id,
                    )
                except ValueError as ts_err:
                    self._resp_publisher.publish_command_error(command_name, command_id, str(ts_err))
                    self._dedup.finish(command_id)
                    return

            if command_key in SAFETY_GATED_COMMANDS and self._is_safety_locked():
                reason = (self._safety_gate.state or {}).get("reason", "unknown")
                self._resp_publisher.publish_command_error(
                    command_name, command_id,
                    f"Command rejected: robot is in safety lockout ({reason}).",
                )
                self._dedup.finish(command_id)
                return

            if command_key == "estop":
                self._command_dispatcher.handle_estop(command_id, data_dict)
            else:
                self._command_dispatcher.dispatch(command_key, command_id, data_dict, validated_timestamp)
        except Exception:
            self._dedup.finish(command_id)
            raise

    # ---- Heartbeat ────────────────────────────────────────────────
    def _publish_system_status(self) -> None:
        self._check_robot_id_change()
        payload = {
            "type": MessageType.STATUS.value,
            "status_type": StatusType.SYSTEM.value,
            "robot_id": self._effective_robot_id(),
            "timestamp": now_iso(),
            "uptime_seconds": max(0.0, time.time() - self._started_at),
            "bridge": {"active_tasks": {
                "count": self._task_watcher.active_task_count,
                "ids": self._task_watcher.active_task_ids,
            }},
        }
        self._resp_publisher.publish_status("system", payload)

    def _publish_connection_status(self) -> None:
        auth_enabled = bool(self._auth_manager)
        auth_token_active = self._auth_manager.has_valid_token() if self._auth_manager else False
        payload = {
            "type": MessageType.STATUS.value,
            "status_type": StatusType.CONNECTION.value,
            "robot_id": self._effective_robot_id(),
            "timestamp": now_iso(),
            "mqtt": {
                "connected": self.mqtt_client.is_connected,
                "client_id": self.config.mqtt.client_id,
                "MQTT_BROKER": {"host": self.config.mqtt.broker, "port": self.config.mqtt.broker_port},
            },
            "auth": {"enabled": auth_enabled, "token_active": auth_token_active},
        }
        self._resp_publisher.publish_status("connection", payload)

    # ---- Bridge-level logic (not pure delegation) ─────────────────
    def _auth_headers(self) -> dict[str, str]:
        if self._auth_manager:
            return self._auth_manager.auth_headers()
        return {}

    def _on_estop_failure(self, command_id: str, error: str) -> None:
        alert_payload = {
            "type": "safety_alert", "alert": "estop_delivery_failed",
            "command_id": command_id, "error": error,
            "timestamp": now_iso(), "robot_id": self._effective_robot_id(),
        }
        alert_topic = f"{self._get_topics().status_base}/safety_alert"
        self._resp_publisher.publish_json(alert_topic, alert_payload)
        logger.critical("[bridge] E-stop safety alert published to %s (command_id=%s)", alert_topic, command_id)

    # ---- Helpers ──────────────────────────────────────────────────
    _VALID_SERVICE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")

    def _extract_service(self, topic: str) -> str | None:
        service = self._get_topics().parse_service(topic)
        if service is None:
            return None
        if not self._VALID_SERVICE_NAME.match(service):
            logger.warning("[bridge] Invalid service name in topic: %r", service)
            return None
        return service

    def _extract_command(self, topic: str) -> str | None:
        return self._get_topics().parse_command(topic)

    def _effective_robot_id(self) -> str:
        # Priority 1: cert CN (cryptographic identity — cannot be spoofed)
        if self._cert_cn:
            return self._cert_cn
        # Priority 2: hub-auth
        if self._auth_manager:
            rid = self._auth_manager.robot_id()
            if rid:
                try:
                    validate_robot_id(rid)
                except ValueError:
                    logger.warning("[bridge] Hub auth returned invalid robot_id %r, falling back to config", rid)
                else:
                    with self._robot_id_lock:
                        self._robot_id_fallback_count = 0
                    return rid
            now = time.time()
            with self._robot_id_lock:
                if now - self._last_robot_id_warn_ts >= ROBOT_ID_FALLBACK_WARN_INTERVAL:
                    self._last_robot_id_warn_ts = now
                    self._robot_id_fallback_count += 1
                    should_warn = True
                    count = self._robot_id_fallback_count
                else:
                    should_warn = False
                    count = self._robot_id_fallback_count
            if should_warn:
                if count >= ROBOT_ID_ESCALATE_AFTER:
                    logger.error(
                        "[bridge] Hub auth robot_id unavailable for %d consecutive checks "
                        "(%.0f+ min) — all MQTT messages use fallback '%s'. "
                        "Check HUB_BASE_URL / HUB_ROBOT_ID / HUB_API_KEY env vars in hub-auth service.",
                        count,
                        count * ROBOT_ID_FALLBACK_WARN_INTERVAL / 60,
                        self.config.robot_id,
                    )
                else:
                    logger.warning("[bridge] Hub auth robot_id unavailable, falling back to %s", self.config.robot_id)
        return self.config.robot_id

    def _validate_timestamp(self, timestamp: Any, command_name: str, command_id: str) -> str | None:
        if timestamp is None:
            return None
        if not isinstance(timestamp, str):
            raise ValueError(f"'timestamp' must be a string (ISO-8601), got {type(timestamp).__name__}")
        try:
            parsed = date_parser.isoparse(timestamp)
            if not isinstance(parsed, datetime):
                raise ValueError("Not a valid datetime")
            return timestamp
        except (ValueError, TypeError, AttributeError) as e:
            raise ValueError(f"Invalid 'timestamp' format: '{timestamp}' (error: {e})") from e
