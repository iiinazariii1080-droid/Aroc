import concurrent.futures
import contextlib
import json
import logging
import threading
import time
from datetime import UTC, datetime
from types import FrameType
from typing import Any

import requests
from dateutil import parser as date_parser
from paho.mqtt.client import MQTTMessage

from app.services.config_service import ConfigChangeNotification, get_config_service
from app.services.mqtt_client_service import UnifiedMQTTClient
from app.utils.payload_validation import (
    has_any_config_parameter,
    validate_dict_payload,
    validate_headers,
    validate_required_field,
)
from auth import HubAuthManager
from command_dedup import CommandDeduplicator
from command_handlers import CommandHandlerMixin
from config import (
    BridgeConfig,
)
from constants import (
    COMMAND_HISTORY_TTL_SECONDS,
    MAX_TASK_WATCHERS,
    ErrorType,
    MessageType,
    NavigationState,
    StatusType,
)
from task_manager import TaskInfo, TaskManagerMixin
from utils import safe_json_loads

logger = logging.getLogger(__name__)


class MqttCommandBridge(CommandHandlerMixin, TaskManagerMixin):
    def __init__(self, config: BridgeConfig | None = None) -> None:
        # Get config service
        self.config_service = get_config_service()

        # Load initial config
        if config is None:
            config = self.config_service.get_config()
        self.config = config

        # Initialize unified MQTT client
        self.mqtt_client = UnifiedMQTTClient(
            config_service=self.config_service,
            component_name="bridge",
        )

        # Subscribe to config changes
        self.config_service.subscribe(self._on_config_changed)

        # Internal state
        self._shutdown = threading.Event()
        self._tasks_lock = threading.Lock()
        self._active_tasks: dict[str, TaskInfo] = {}
        self._task_watcher_sem = threading.Semaphore(MAX_TASK_WATCHERS)
        self._auth_manager = HubAuthManager(config.hub_auth) if config.hub_auth else None
        self._last_disconnect_at: float | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._started_at = time.time()
        self._dedup = CommandDeduplicator(
            history_ttl=COMMAND_HISTORY_TTL_SECONDS,
            max_history=1000,
        )

        # Thread-local HTTP sessions for connection pooling (requests.Session is not thread-safe)
        self._thread_local = threading.local()
        self._http_sessions: list[requests.Session] = []
        self._http_sessions_lock = threading.Lock()
        # Executor for offloading HTTP calls from the MQTT callback thread
        self._http_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="bridge-http"
        )

        # Task result queue for reconnection
        self._task_result_queue: dict[str, dict[str, Any]] = {}
        self._task_result_queue_lock = threading.Lock()
        self._flushing_queue = False  # guard against concurrent flushes

        # Long-running operations configuration
        self._long_operations: dict[str, dict[str, float]] = {
            "igus": {
                "/move": 30.0,
                "/reference": 30.0,
                "/fault_reset": 30.0,
            }
        }
        # Allow override from environment
        from env_settings import get_env_settings
        parsed = get_env_settings().parse_long_operations()
        if parsed is not None:
            self._long_operations = parsed

    # ---- Lifecycle -------------------------------------------------
    def _on_config_changed(self, notification: ConfigChangeNotification) -> None:
        """Handle configuration change notification."""
        # Update local config
        self.config = notification.new_config

        # Log the change
        logger.info(
            "[bridge] Config changed: %s, revision %d",
            notification.event.value,
            notification.revision
        )

        # MQTT client will automatically reconnect if broker/port/TLS changed
        # UnifiedMQTTClient handles reconnection automatically

    def start(self) -> None:
        """Start the bridge."""
        if self._shutdown.is_set():
            logger.warning("[bridge] Bridge is shutting down, cannot start.")
            return

        self._shutdown.clear()

        # Subscribe to MQTT topics
        self.mqtt_client.subscribe(
            self.config.cmd_topic_pattern,
            self._handle_mqtt_message
        )
        self.mqtt_client.subscribe(
            self.config.command_topic_pattern,
            self._handle_mqtt_message
        )
        self.mqtt_client.subscribe(
            self.config.config_topic_pattern,
            self._handle_mqtt_message
        )

        # Start MQTT client
        self.mqtt_client.start()

        # Register on-connect callback to flush queued task results
        self.mqtt_client.add_on_connect_callback(self._flush_task_result_queue)

        # Register with global MQTT state for health checks
        from app.services import mqtt_state
        mqtt_state.register("bridge", self.mqtt_client)

        # Start heartbeat
        self._start_heartbeat()

        logger.info("[bridge] Bridge started")

    def stop(self) -> None:
        """Stop the bridge."""
        if self._shutdown.is_set():
            return

        self._shutdown.set()

        # Graceful drain: wait for in-flight commands to complete (max 5 s)
        drain_deadline = time.time() + 5.0
        while time.time() < drain_deadline:
            remaining = self._dedup.in_flight_count
            if remaining == 0:
                break
            logger.info("[bridge] Draining %d in-flight command(s)…", remaining)
            time.sleep(0.25)

        self._stop_all_watchers()
        self._stop_heartbeat()

        # Stop MQTT client
        self.mqtt_client.stop()

        # Unregister from global MQTT state
        from app.services import mqtt_state
        mqtt_state.unregister("bridge")

        # Unsubscribe from config changes
        self.config_service.unsubscribe(self._on_config_changed)

        # Close auth session
        if self._auth_manager:
            self._auth_manager.close()

        # Shutdown HTTP executor
        self._http_executor.shutdown(wait=False)

        # Close all thread-local HTTP sessions
        with self._http_sessions_lock:
            for session in self._http_sessions:
                with contextlib.suppress(Exception):
                    session.close()
            self._http_sessions.clear()

        logger.info("[bridge] Bridge stopped")

    def run_forever(self) -> None:
        """Run bridge forever until shutdown."""
        self.start()
        try:
            while not self._shutdown.is_set():
                # Config changes are handled automatically via subscription
                # No need to periodically reload config
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down.")
        finally:
            self.stop()

    def handle_signal(self, signum: int, frame: FrameType | None) -> None:
        """Handle shutdown signals."""
        if self._shutdown.is_set():
            return
        logger.info("Received signal %s, shutting down...", signum)
        self.stop()

    # ---- MQTT message handling -------------------------------------
    def _handle_mqtt_message(self, message: MQTTMessage) -> None:
        """Handle incoming MQTT message."""
        try:
            self._handle_incoming_message(
                topic=message.topic,
                payload_bytes=message.payload,
            )
        except Exception as e:
            logger.exception("[bridge] Failed to process MQTT message on topic %s", message.topic)
            # Publish error to MQTT for better diagnostics
            self._publish_processing_error(message.topic, str(e))

    # ---- Command handling -----------------------------------------------
    _MAX_PAYLOAD_BYTES = 1_048_576  # 1 MiB

    def _handle_incoming_message(self, topic: str, payload_bytes: bytes) -> None:
        if len(payload_bytes) > self._MAX_PAYLOAD_BYTES:
            logger.warning(
                "Dropping oversized MQTT payload on %s (%d bytes, limit %d)",
                topic, len(payload_bytes), self._MAX_PAYLOAD_BYTES,
            )
            return
        payload = payload_bytes.decode("utf-8", errors="replace")
        logger.debug("MQTT message on %s: %.512s", topic, payload)

        # Check configuration topic
        config_key = self._extract_config_key(topic)
        if config_key:
            data, error = safe_json_loads(payload)
            if error:
                logger.warning("Failed to parse config message JSON: %s", error)
                self._publish_json_parse_error(topic, error)
                return
            self._handle_config_message(config_key, data)
            return

        command_name = self._extract_command(topic)
        if command_name:
            data, error = safe_json_loads(payload)
            if error:
                logger.warning("Failed to parse command message JSON: %s", error)
                self._publish_json_parse_error(topic, error)
                return
            self._handle_command_message(command_name, data)
            return

        service = self._extract_service(topic)
        if not service:
            logger.warning("Cannot extract service from topic: %s", topic)
            return

        data, error = safe_json_loads(payload)
        if error:
            logger.warning("Failed to parse service message JSON: %s", error)
            self._publish_invalid_json_response(service)
            return

        data_dict, validation_error = validate_dict_payload(data, error_context="Service message")
        if validation_error:
            logger.warning("Invalid JSON payload for topic %s: %s", topic, validation_error)
            self._publish_invalid_json_response(service)
            return
        assert data_dict is not None  # narrowed by validation_error check

        if service not in self.config.services:
            logger.warning("Unknown service '%s' in topic '%s'", service, topic)
            request_id = data_dict.get("request_id") if isinstance(data_dict, dict) else None
            self._publish_unknown_service_response(service, request_id)
            return

        request_id = data_dict.get("request_id")
        method = str(data_dict.get("method", "GET")).upper()
        raw_path = str(data_dict.get("path", "/"))
        body = data_dict.get("body")

        # Validate path against traversal / SSRF attacks
        path = self._validate_path(raw_path)
        if path is None:
            logger.warning("Rejected unsafe path '%s' for service '%s'", raw_path, service)
            self._publish_error_ack(
                service=service,
                request_id=data_dict.get("request_id"),
                status_code=400,
                body={"detail": "Invalid path"},
                error={"type": ErrorType.COMMAND_ERROR.value, "message": "Path contains forbidden characters"},
            )
            return

        headers, headers_error = validate_headers(data_dict.get("headers"), error_context="Service request")
        if headers_error:
            self._publish_error_ack(
                service=service,
                request_id=request_id,
                status_code=400,
                body={"detail": headers_error},
                error={"type": ErrorType.INVALID_JSON.value, "message": headers_error},
            )
            return

        timeout = data_dict.get("timeout")  # Optional timeout override

        # Auto-increase timeout for long-running operations
        if timeout is None and service in self._long_operations:
            service_ops = self._long_operations.get(service, {})
            if path in service_ops:
                timeout = max(self.config.http_timeout, service_ops[path])


        url = self._build_http_url(service, path)
        if not url:
            logger.error("No base_url for service '%s'", service)
            self._publish_service_config_missing(service, request_id)
            return

        self._submit_http(
            service=service,
            request_id=request_id,
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
            context=None,
        )

    def _handle_command_message(self, command: str, data: Any) -> None:
        data_dict, validation_error = validate_dict_payload(data, error_context="Command message")
        if validation_error:
            self._publish_command_error(command, None, validation_error)
            return
        assert data_dict is not None  # narrowed by validation_error check

        command_name = command.strip()
        command_key = command_name.lower()

        command_id_raw = data_dict.get("command_id") or data_dict.get("request_id")
        command_id, id_error = validate_required_field(
            {"command_id": command_id_raw} if command_id_raw is not None else {},
            "command_id",
            error_context="Command"
        )
        if id_error:
            self._publish_command_error(command_name, None, id_error)
            return
        command_id = str(command_id)

        # Replay cached result if available
        cached_payload = self._dedup.get(command_id)
        if cached_payload:
            logger.info(
                "Replaying cached response for command_id=%s (command=%s)",
                command_id,
                command_name,
            )
            service_name = cached_payload.get("service", "robot")
            self._send_response(service_name, cached_payload)
            self._publish_navigation_status(
                state=NavigationState.DUPLICATE.value,
                success=cached_payload.get("success"),
                detail=cached_payload,
                context={
                    "command_name": command_key,
                    "command_id": command_id,
                    "status_type": StatusType.NAVIGATION.value,
                    "target_id": data_dict.get("target_id"),
                },
            )
            return

        if not self._dedup.try_start(command_id):
            logger.info(
                "Command %s (id=%s) already in progress, ignoring duplicate.",
                command_name,
                command_id,
            )
            return

        try:
            # Validate timestamp for navigateTo and cancel commands (per SRS)
            if command_key in ("navigateto", "cancel"):
                timestamp = self._validate_timestamp(
                    data_dict.get("timestamp"),
                    command_name,
                    command_id,
                )
                # Store validated timestamp for forwarding in HTTP request
                if timestamp:
                    data_dict["_validated_timestamp"] = timestamp

            if command_key == "navigateto":
                self._handle_navigate_command(command_id, data_dict)
            elif command_key == "cancel":
                self._handle_cancel_command(command_id, data_dict)
            elif command_key == "estop":
                self._handle_estop_command(command_id, data_dict)
            else:
                self._publish_command_error(
                    command_name,
                    command_id,
                    f"Unsupported command '{command_name}'.",
                )
                self._finish_command(command_id)
        except Exception:
            # On unexpected error, release the command lock immediately
            self._finish_command(command_id)
            raise

    def _handle_config_message(self, config_key: str, data: Any) -> None:
        """Handle configuration messages."""
        data_dict, validation_error = validate_dict_payload(data, error_context="Config message")
        if validation_error:
            logger.warning("Config message payload must be a JSON object: %s", validation_error)
            return
        assert data_dict is not None  # narrowed by validation_error check

        config_key_upper = config_key.upper()
        if config_key_upper == "MQTT_BROKER":
            self._handle_broker_config_change(data_dict)
        else:
            logger.warning("Unknown config key: %s", config_key)

    def _handle_broker_config_change(self, data: dict[str, Any]) -> None:
        """Handle broker configuration change."""
        request_id = data.get("request_id")

        broker = data.get("MQTT_BROKER")
        broker_port = data.get("MQTT_PORT")
        mqtt_user = data.get("mqtt_user")
        mqtt_password = data.get("mqtt_password")

        # TLS settings
        mqtt_use_tls = data.get("mqtt_use_tls")
        mqtt_ca_certs = data.get("mqtt_ca_certs")
        mqtt_certfile = data.get("mqtt_certfile")
        mqtt_keyfile = data.get("mqtt_keyfile")
        mqtt_tls_insecure = data.get("mqtt_tls_insecure")

        # Check that at least one parameter is provided
        config_params = [
            broker, broker_port, mqtt_user, mqtt_password,
            mqtt_use_tls, mqtt_ca_certs, mqtt_certfile, mqtt_keyfile, mqtt_tls_insecure
        ]
        param_names = [
            "broker", "broker_port", "mqtt_user", "mqtt_password",
            "mqtt_use_tls", "mqtt_ca_certs", "mqtt_certfile", "mqtt_keyfile", "mqtt_tls_insecure"
        ]

        if not has_any_config_parameter(dict(zip(param_names, config_params, strict=False)), param_names):
            error_msg = "At least one configuration parameter must be provided"
            logger.warning(error_msg)
            self._publish_config_response(request_id, success=False, error=error_msg)
            return

        # Validate broker_port if provided
        if broker_port is not None:
            try:
                broker_port = int(broker_port)
                if broker_port < 1 or broker_port > 65535:
                    error_msg = "broker_port must be between 1 and 65535"
                    logger.warning(error_msg)
                    self._publish_config_response(request_id, success=False, error=error_msg)
                    return
            except (ValueError, TypeError):
                error_msg = "broker_port must be an integer"
                logger.warning(error_msg)
                self._publish_config_response(request_id, success=False, error=error_msg)
                return

        logger.info("[bridge] Processing broker configuration change request")

        # Prepare updates
        updates = {}
        if broker is not None:
            updates["MQTT_BROKER"] = broker
        if broker_port is not None:
            updates["MQTT_PORT"] = str(broker_port)
        if mqtt_user is not None:
            updates["MQTT_USER"] = mqtt_user
        if mqtt_password is not None:
            updates["MQTT_PASS"] = mqtt_password
        if mqtt_use_tls is not None:
            updates["MQTT_USE_TLS"] = str(mqtt_use_tls).lower()
        if mqtt_ca_certs is not None:
            updates["MQTT_CA_CERTS"] = mqtt_ca_certs or ""
        if mqtt_certfile is not None:
            updates["MQTT_CERTFILE"] = mqtt_certfile or ""
        if mqtt_keyfile is not None:
            updates["MQTT_KEYFILE"] = mqtt_keyfile or ""
        if mqtt_tls_insecure is not None:
            updates["MQTT_TLS_INSECURE"] = str(mqtt_tls_insecure).lower()

        if not updates:
            error_msg = "No configuration parameters provided"
            logger.warning("[bridge] %s", error_msg)
            self._publish_config_response(request_id, success=False, error=error_msg)
            return

        # Update via config_service (atomic)
        success = self.config_service.update_config(
            updates,
            updated_by="mqtt",
            reason="Broker configuration changed via MQTT"
        )

        if success:
            # UnifiedMQTTClient will automatically reconnect
            logger.info("[bridge] Broker configuration updated successfully, reconnecting...")
            self._publish_config_response(request_id, success=True)
        else:
            error_msg = "Failed to update broker configuration"
            logger.error("[bridge] %s", error_msg)
            self._publish_config_response(request_id, success=False, error=error_msg)

    def _publish_config_response(self, request_id: str | None, success: bool, error: str | None = None) -> None:
        """Publish response to configuration change."""
        topic = f"{self.config.status_base_topic}/config"
        payload = {
            "type": MessageType.CONFIG_RESPONSE.value,
            "request_id": request_id,
            "success": success,
            "timestamp": self._now_iso(),
        }
        if error:
            payload["error"] = error
        else:
            payload["message"] = "Configuration updated successfully"

        self._publish_json(topic, payload)

    # _reconnect_with_new_config removed - UnifiedMQTTClient handles reconnection automatically

    # navigateTo, cancel, estop — see CommandHandlerMixin (command_handlers.py)

    def _submit_http(self, service: str, **kwargs: Any) -> None:
        """Submit an HTTP command to the executor with shutdown guard.

        Captures service_cfg snapshot before submitting to avoid
        TOCTOU issues with config reloads between submit and execution.
        """
        context = kwargs.get("context")
        command_id = context.get("command_id") if context else None

        if self._shutdown.is_set():
            logger.warning("[bridge] Ignoring HTTP command during shutdown")
            self._finish_command(command_id)
            return
        service_cfg = self.config.services.get(service)
        try:
            self._http_executor.submit(
                self._execute_http_command,
                service=service,
                service_cfg=service_cfg,
                **kwargs,
            )
        except RuntimeError:
            logger.warning("[bridge] HTTP executor shut down, command dropped")
            self._finish_command(command_id)

    def _execute_http_command(
        self,
        service: str,
        request_id: str | None,
        method: str,
        url: str,
        headers: dict[str, Any],
        body: Any,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
        service_cfg: Any | None = None,
    ) -> None:
        logger.info("HTTP %s %s", method, url)
        logger.debug("HTTP %s %s body=%s", method, url, str(body)[:200])

        request_headers = self._prepare_request_headers(headers)
        request_timeout = timeout if timeout is not None else self.config.http_timeout

        try:
            response = self._get_http_session().request(
                method=method,
                url=url,
                headers=request_headers,
                json=body if body is not None else None,
                timeout=request_timeout,
            )
        except requests.RequestException as exc:
            logger.exception("HTTP request failed for service=%s url=%s", service, url)
            self._publish_http_error_response(service, request_id, error=str(exc))
            # Release command lock if this was a command
            if context and context.get("command_id"):
                self._finish_command(context["command_id"])
            return

        try:
            response_body = response.json()
        except ValueError:
            response_body = response.text

        ack_payload: dict[str, Any] = {
            "type": MessageType.ACK.value,
            "request_id": request_id,
            "service": service,
            "success": response.ok,
            "status_code": response.status_code,
            "headers": {k: v for k, v in response.headers.items()
                        if k.lower() in {"content-type", "content-length", "x-request-id"}},
            "body": response_body,
            "error": None,
        }
        if context:
            command_id = context.get("command_id")
            if command_id:
                ack_payload.setdefault("command_id", command_id)
        if not response.ok:
            ack_payload["error"] = {
                "type": ErrorType.HTTP_ERROR.value,
                "message": f"HTTP {response.status_code}",
            }

        service_cfg = service_cfg or self.config.services.get(service)
        task_id: str | None = None
        if service_cfg and service_cfg.watch_tasks and isinstance(response_body, dict):
            task_id = response_body.get("task_id")
            if task_id:
                ack_payload["task_id"] = task_id

        self._send_response(service, ack_payload)

        if task_id:
            origin_request_id = request_id or f"task-{task_id}"
            metadata = None
            if context and context.get("metadata"):
                metadata = dict(context["metadata"])
            if metadata is not None and context is not None:
                metadata.setdefault("command_id", context.get("command_id"))
                metadata.setdefault("command_name", context.get("command_name"))
                metadata.setdefault("target_id", context.get("target_id"))
                metadata.setdefault("status_type", context.get("status_type"))
                metadata["task_id"] = task_id
            self._start_task_watcher(
                service_cfg,
                task_id,
                origin_request_id,
                metadata=metadata,
            )

        if context and context.get("command_id"):
            self._store_command_history(context["command_id"], ack_payload)

        if context and context.get("publish_navigation"):
            navigation_state = NavigationState.ACKNOWLEDGED if response.ok else NavigationState.REJECTED
            self._publish_navigation_status(
                state=navigation_state.value,
                success=response.ok,
                detail=ack_payload,
                context=context,
            )

        # Release command lock after HTTP completes
        if context and context.get("command_id"):
            self._finish_command(context["command_id"])

    # Task watchers (_start_task_watcher, _task_watcher_loop, _publish_task_timeout,
    # _remove_task, get_task_info, get_active_tasks, _stop_all_watchers)
    # — see TaskManagerMixin (task_manager.py)

    def _start_heartbeat(self) -> None:
        if self.config.status_heartbeat_interval <= 0:
            return
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            name="mqtt-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread = thread
        thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread and thread.is_alive():
            thread.join(timeout=self.config.status_heartbeat_interval * 2)
        self._heartbeat_thread = None

    # Reconnect methods removed - UnifiedMQTTClient handles reconnection automatically

    def _heartbeat_loop(self) -> None:
        interval = max(1.0, self.config.status_heartbeat_interval)
        while not self._shutdown.is_set() and not self._heartbeat_stop.is_set():
            try:
                self._publish_system_status()
                self._publish_connection_status()
            except Exception:
                logger.exception("Heartbeat publishing failed.")
            finished = self._heartbeat_stop.wait(interval)
            if finished:
                break

    def _publish_system_status(self) -> None:
        timestamp = self._now_iso()
        with self._tasks_lock:
            active_count = len(self._active_tasks)
            active_ids = list(self._active_tasks.keys())
        payload = {
            "type": MessageType.STATUS.value,
            "status_type": StatusType.SYSTEM.value,
            "robot_id": self._effective_robot_id(),
            "timestamp": timestamp,
            "uptime_seconds": max(0.0, time.time() - self._started_at),
            "bridge": {
                "active_tasks": {"count": active_count, "ids": active_ids},
            },
        }
        self._publish_status("system", payload)

    def _publish_connection_status(self) -> None:
        timestamp = self._now_iso()
        auth_enabled = bool(self._auth_manager)
        auth_token_active = (
            self._auth_manager.has_valid_token() if self._auth_manager else False
        )
        payload = {
            "type": MessageType.STATUS.value,
            "status_type": StatusType.CONNECTION.value,
            "robot_id": self._effective_robot_id(),
            "timestamp": timestamp,
            "mqtt": {
                "connected": self.mqtt_client.is_connected,
                "client_id": self.config.client_id,
                "MQTT_BROKER": {
                    "host": self.config.broker,
                    "port": self.config.broker_port,
                },
                "last_disconnect_ts": self._format_timestamp(self._last_disconnect_at),
            },
            "auth": {
                "enabled": auth_enabled,
                "token_active": auth_token_active,
            },
        }
        self._publish_status("connection", payload)

    def _auth_headers(self) -> dict[str, str]:
        if self._auth_manager:
            return self._auth_manager.auth_headers()
        return {}

    def _prepare_request_headers(self, user_headers: dict[str, Any]) -> dict[str, str]:
        merged: dict[str, str] = {}
        merged.update(self._auth_headers())
        for key, value in user_headers.items():
            if value is None:
                continue
            merged[str(key)] = str(value)
        return merged

    # ---- MQTT publishing -----------------------------------------------
    def _send_response(self, service: str, payload: dict[str, Any]) -> None:
        """Send response to service. If MQTT is disconnected, queue for later."""
        request_id = payload.get("request_id")

        # If MQTT is connected, send immediately
        if self.mqtt_client.is_connected:
            topic = f"{self.config.resp_base_topic}/{service}"
            if self._publish_json(topic, payload):
                return  # Sent successfully

        # If send failed, add to queue for delivery after reconnection
        if request_id:
            self._add_task_result_to_queue(request_id, payload)
            logger.debug("Added result to queue for request_id=%s (MQTT not connected)", request_id)

    def _publish_status(self, suffix: str, payload: dict[str, Any]) -> None:
        topic = f"{self.config.status_base_topic}/{suffix}"
        self._publish_json(topic, payload)

    def _publish_navigation_status(
        self,
        state: str,
        success: bool | None,
        detail: Any,
        context: dict[str, Any] | None,
    ) -> None:
        if not context or context.get("status_type") != StatusType.NAVIGATION.value:
            return
        command_id = context.get("command_id")
        if not command_id:
            return
        payload = {
            "type": MessageType.STATUS.value,
            "status_type": StatusType.NAVIGATION.value,
            "robot_id": self._effective_robot_id(),
            "timestamp": self._now_iso(),
            "command_id": command_id,
            "command_name": context.get("command_name"),
            "target_id": context.get("target_id"),
            "task_id": context.get("task_id"),
            "state": state,
            "success": success,
            "detail": detail,
        }
        self._publish_status("navigation", payload)

    def _publish_unknown_service_response(
        self,
        service: str,
        request_id: str | None,
    ) -> None:
        payload = {
            "type": MessageType.ACK.value,
            "request_id": request_id,
            "service": service,
            "success": False,
            "status_code": 400,
            "body": {"detail": f"Unknown service '{service}'"},
            "error": {"type": ErrorType.ROUTING_ERROR.value, "message": "Unknown service"},
        }
        self._send_response(service, payload)

    def _publish_invalid_json_response(self, service: str) -> None:
        payload = {
            "type": MessageType.ACK.value,
            "request_id": None,
            "service": service,
            "success": False,
            "status_code": 400,
            "body": {"detail": "Invalid JSON payload"},
            "error": {
                "type": ErrorType.INVALID_JSON.value,
                "message": "Payload is not a JSON object",
            },
        }
        self._send_response(service, payload)

    def _publish_service_config_missing(
        self,
        service: str,
        request_id: str | None,
    ) -> None:
        self._publish_error_ack(
            service=service,
            request_id=request_id,
            status_code=500,
            body={"detail": f"No HTTP mapping for service '{service}'"},
            error={"type": ErrorType.ROUTING_ERROR.value, "message": "No base_url configured"},
        )

    def _publish_command_error(
        self,
        command: str,
        command_id: str | None,
        message: str,
    ) -> None:
        body = {"detail": message, "command": command}
        self._publish_error_ack(
            service="robot",
            request_id=command_id,
            status_code=400,
            body=body,
            error={"type": ErrorType.COMMAND_ERROR.value, "message": message},
        )
        if command.lower() in {"navigateto", "cancel", "estop"}:
            context = {
                "command_name": command.lower(),
                "command_id": command_id,
                "status_type": "navigation",
            }
            self._publish_navigation_status(
                state=NavigationState.REJECTED.value,
                success=False,
                detail=body,
                context=context,
            )

    def _publish_http_error_response(
        self,
        service: str,
        request_id: str | None,
        error: str,
    ) -> None:
        self._publish_error_ack(
            service=service,
            request_id=request_id,
            status_code=0,
            body=None,
            error={"type": ErrorType.HTTP_ERROR.value, "message": error},
        )

    def _publish_error_ack(
        self,
        service: str,
        request_id: str | None,
        status_code: int,
        body: Any,
        error: dict[str, Any],
    ) -> None:
        payload = {
            "type": MessageType.ACK.value,
            "request_id": request_id,
            "service": service,
            "success": False,
            "status_code": status_code,
            "body": body,
            "error": error,
        }
        self._send_response(service, payload)

    def _publish_json(self, topic: str, payload: dict[str, Any]) -> bool:
        """Publish JSON payload to MQTT topic with error checking.

        Returns:
            True if publish was successful, False otherwise.
        """
        if not self.mqtt_client.is_connected:
            logger.warning("[bridge] MQTT not connected, skipping publish to %s", topic)
            return False

        try:
            message = json.dumps(payload, separators=(",", ":"))
            message_bytes = message.encode('utf-8')

            # Validate payload size
            try:
                from constants import MAX_MQTT_PAYLOAD_SIZE
                if len(message_bytes) > MAX_MQTT_PAYLOAD_SIZE:
                    logger.error(
                        "[bridge] Payload too large for topic %s: %d bytes (max %d)",
                        topic, len(message_bytes), MAX_MQTT_PAYLOAD_SIZE
                    )
                    return False
            except ImportError:
                # If constants unavailable, use conservative limit
                MAX_MQTT_PAYLOAD_SIZE = 1024 * 1024  # 1MB
                if len(message_bytes) > MAX_MQTT_PAYLOAD_SIZE:
                    logger.error("[bridge] Payload too large for topic %s", topic)
                    return False

            logger.debug("[bridge] MQTT publish %s -> %s", topic, message[:200])

            # Publish via UnifiedMQTTClient (pass pre-serialized string
            # to avoid redundant json.dumps inside publish())
            return self.mqtt_client.publish(
                topic,
                message,
                qos=self.config.mqtt_publish_qos,
                retain=False
            )
        except Exception:
            logger.exception("[bridge] Failed to publish MQTT message to %s", topic)
            return False

    # ---- Helper methods ----------------------------------------
    def _extract_service(self, topic: str) -> str | None:
        parts = topic.split("/")
        if len(parts) < 5:
            return None
        return parts[4]

    def _get_http_session(self) -> requests.Session:
        """Get a thread-local requests.Session (connection pooling per thread)."""
        if not hasattr(self._thread_local, 'session'):
            session = requests.Session()
            self._thread_local.session = session
            with self._http_sessions_lock:
                self._http_sessions.append(session)
        session_obj: requests.Session = self._thread_local.session
        return session_obj

    def _build_http_url(self, service: str, path: str) -> str | None:
        service_cfg = self.config.services.get(service)
        if not service_cfg:
            return None
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{service_cfg.base_url}{normalized_path}"

    @staticmethod
    def _validate_path(path: str) -> str | None:
        """Validate and sanitise an HTTP path from an MQTT message.

        Returns the cleaned path, or ``None`` if it contains
        forbidden characters / traversal sequences.
        """
        import posixpath

        # Block null bytes, backslashes, @ (userinfo), and whitespace
        if any(c in path for c in ('\x00', '\\', '@', '\r', '\n')):
            return None

        # Normalize the path (resolves /../ sequences)
        normalized = posixpath.normpath(path)

        # After normalization the path must still start with '/'
        if not normalized.startswith('/'):
            normalized = '/' + normalized

        # Ensure that normalization hasn't escaped the root
        if normalized.startswith('/..') or normalized == '..':
            return None

        return normalized

    def _extract_command(self, topic: str) -> str | None:
        parts = topic.split("/")
        if len(parts) < 5:
            return None
        if parts[3] != "commands":
            return None
        return parts[4]

    def _extract_config_key(self, topic: str) -> str | None:
        """Extract configuration key from topic aroc/robot/{robot_id}/config/{key}"""
        parts = topic.split("/")
        if len(parts) < 5:
            return None
        if parts[3] != "config":
            return None
        return parts[4]

    def _effective_robot_id(self) -> str:
        if self._auth_manager:
            rid = self._auth_manager.robot_id()
            if rid:
                return rid
        return self.config.robot_id

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def _format_timestamp(self, ts: float | None) -> str | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()

    def _validate_timestamp(self, timestamp: Any, command_name: str, command_id: str) -> str | None:
        """
        Validate an ISO-8601 timestamp.

        Args:
            timestamp: Timestamp value to validate
            command_name: Command name (for logging)
            command_id: Command ID (for logging)

        Returns:
            Valid timestamp (str) or None if invalid/missing
        """
        if timestamp is None:
            logger.warning(
                "Command '%s' (id=%s) missing required 'timestamp' field (ISO-8601 format expected)",
                command_name,
                command_id,
            )
            return None

        if not isinstance(timestamp, str):
            logger.warning(
                "Command '%s' (id=%s) has invalid 'timestamp' type: expected string (ISO-8601), got %s",
                command_name,
                command_id,
                type(timestamp).__name__,
            )
            return None

        try:
            # Parse ISO-8601 timestamp
            parsed = date_parser.isoparse(timestamp)
            # Verify it is actually a datetime
            if not isinstance(parsed, datetime):
                raise ValueError("Not a valid datetime")
            return timestamp
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(
                "Command '%s' (id=%s) has invalid 'timestamp' format: '%s' (ISO-8601 expected, error: %s)",
                command_name,
                command_id,
                timestamp,
                str(e),
            )
            return None

    def _store_command_history(self, command_id: str, payload: dict[str, Any]) -> None:
        """Delegate to CommandDeduplicator (also called by mixins via BridgeProtocol)."""
        self._dedup.store(command_id, payload)

    def _finish_command(self, command_id: str | None) -> None:
        """Delegate to CommandDeduplicator (also called by mixins via BridgeProtocol)."""
        self._dedup.finish(command_id)

    # _check_and_reload_config removed - ConfigService handles config changes via subscription

    def _add_task_result_to_queue(self, request_id: str, result: dict[str, Any]) -> None:
        """Queue task result for sending after reconnection."""
        if not request_id:
            return
        with self._task_result_queue_lock:
            self._task_result_queue[request_id] = result

    def _flush_task_result_queue(self) -> None:
        """Sends all queued task results after reconnection.

        Uses a ``_flushing_queue`` flag so that a rapid reconnect
        cannot trigger a concurrent flush (which would duplicate results).
        """
        with self._task_result_queue_lock:
            if self._flushing_queue or not self._task_result_queue:
                return
            self._flushing_queue = True
            results_to_send = dict(self._task_result_queue)

        try:
            logger.info("Flushing %d queued task results after reconnection", len(results_to_send))
            for request_id, result in results_to_send.items():
                try:
                    service = result.get("service", "unknown")
                    self._send_response(service, result)
                    logger.debug("Sent queued result for request_id=%s", request_id)
                    # Remove only after successful send
                    with self._task_result_queue_lock:
                        self._task_result_queue.pop(request_id, None)
                except Exception as e:
                    logger.error("Failed to send queued result for request_id=%s: %s", request_id, e)
        finally:
            with self._task_result_queue_lock:
                self._flushing_queue = False

    def _publish_processing_error(self, topic: str, error_message: str) -> None:
        """Publish message processing error to MQTT topic."""
        try:
            # Extract service from topic for error publishing
            service = self._extract_service(topic)
            if not service:
                # Try to extract from other topic patterns
                if "/cmd/" in topic or "/commands/" in topic:
                    # This is a command, publish to general errors topic
                    error_topic = f"{self.config.status_base_topic}/errors"
                    err_payload: dict[str, Any] = {
                        "type": MessageType.ERROR.value,
                        "topic": topic,
                        "error": {
                            "type": ErrorType.PROCESSING_ERROR.value,
                            "message": error_message,
                            "timestamp": self._now_iso(),
                        }
                    }
                    self._publish_json(error_topic, err_payload)
                return

            # Publish error to service response topic
            payload: dict[str, Any] = {
                "type": MessageType.ACK.value,
                "request_id": None,
                "service": service,
                "success": False,
                "status_code": 500,
                "body": {"detail": f"Failed to process message: {error_message}"},
                "error": {
                    "type": ErrorType.PROCESSING_ERROR.value,
                    "message": error_message,
                },
            }
            self._send_response(service, payload)
        except Exception as e:
            logger.error("Failed to publish processing error: %s", e, exc_info=True)

    def _publish_json_parse_error(self, topic: str, error_message: str) -> None:
        """Publish JSON parse error to MQTT topic."""
        try:
            service = self._extract_service(topic)
            if service:
                self._publish_invalid_json_response(service)
            else:
                # For commands, publish to errors topic
                error_topic = f"{self.config.status_base_topic}/errors"
                payload = {
                    "type": MessageType.ERROR.value,
                    "topic": topic,
                    "error": {
                        "type": ErrorType.JSON_PARSE_ERROR.value,
                        "message": error_message,
                        "timestamp": self._now_iso(),
                    }
                }
                self._publish_json(error_topic, payload)
        except Exception as e:
            logger.error("Failed to publish JSON parse error: %s", e, exc_info=True)

