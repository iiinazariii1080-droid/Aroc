"""
Unified MQTT client service with automatic reconnection and state management.

This module provides a single MQTT client implementation that can be shared
by bridge and telemetry components, with automatic reconnection, backoff,
and unified state management.
"""
import concurrent.futures
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from paho.mqtt import client as mqtt_client
from paho.mqtt.client import MQTTMessage

from app.services.config_service import ConfigChangeEvent, ConfigChangeNotification, ConfigService
from app.services.mqtt_logging import ThrottledLoggerMixin
from app.services.mqtt_tls import (
    configure_tls_on_client,
    normalize_cert_paths,
    validate_tls_certificates,
)
from app.services.mqtt_types import MQTTClientConfig, MQTTConnectionState
from constants import (
    LOG_THROTTLE_INTERVAL_SECONDS,
    MQTT_CONNECTION_CHECK_INTERVAL_SECONDS,
    MQTT_CONNECTION_WAIT_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# Re-export types for backward compatibility
__all__ = ["MQTTClientConfig", "MQTTConnectionState", "UnifiedMQTTClient"]


class UnifiedMQTTClient(ThrottledLoggerMixin):
    """
    Unified MQTT client with automatic reconnection and state management.

    Features:
    - Automatic reconnection with exponential backoff
    - Unified connection state (mqtt_connected)
    - Config change handling (reconnects on broker/port/TLS changes)
    - Throttled logging to prevent spam
    - Thread-safe operations
    """

    def __init__(
        self,
        config_service: ConfigService,
        component_name: str = "mqtt_client",
        max_reconnect_delay: float = 30.0,
        initial_reconnect_delay: float = 1.0,
        reconnect_backoff_multiplier: float = 2.0,
    ):
        """
        Initialize unified MQTT client.

        Args:
            config_service: ConfigService instance for getting broker config
            component_name: Name of component using this client (for logging)
            max_reconnect_delay: Maximum delay between reconnect attempts (seconds)
            initial_reconnect_delay: Initial delay before first reconnect (seconds)
            reconnect_backoff_multiplier: Multiplier for exponential backoff
        """
        self.config_service = config_service
        self.component_name = component_name
        self.max_reconnect_delay = max_reconnect_delay
        self.initial_reconnect_delay = initial_reconnect_delay
        self.reconnect_backoff_multiplier = reconnect_backoff_multiplier

        # Thread safety
        self._lock = threading.RLock()

        # Client state
        self._client: mqtt_client.Client | None = None
        self._state = MQTTConnectionState.DISCONNECTED
        self._mqtt_connected = threading.Event()
        self._shutdown = threading.Event()

        # Reconnection state
        self._reconnect_delay = initial_reconnect_delay
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_stop = threading.Event()
        self._reconnect_required = threading.Event()

        # Flag to ignore config changes we initiated ourselves
        self._ignore_config_changes = threading.Event()

        # Message handlers
        self._message_handlers: dict[str, set[Callable[[MQTTMessage], None]]] = {}
        self._message_handlers_lock = threading.Lock()

        # On-connect callbacks (called after successful connect + resubscribe)
        self._on_connect_callbacks: list[Callable[[], None]] = []
        self._on_connect_callbacks_lock = threading.Lock()

        # Logging throttling
        self._last_log_time: dict[str, float] = {}
        self._log_throttle_interval = LOG_THROTTLE_INTERVAL_SECONDS
        self._repeated_log_count: dict[str, int] = {}

        # Last successful publish time
        self._last_publish_time: float | None = None

        # Thread pool for dispatching message handlers off the paho callback thread
        self._handler_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix=f"{component_name}-msg"
        )

        # Subscribe to config changes
        self.config_service.subscribe(self._on_config_changed)

        # Current config snapshot
        self._current_config: MQTTClientConfig | None = None

    @property
    def is_connected(self) -> bool:
        """Check if MQTT is connected (thread-safe)."""
        return self._mqtt_connected.is_set()

    @property
    def state(self) -> MQTTConnectionState:
        """Get current connection state."""
        with self._lock:
            return self._state

    @property
    def last_publish_time(self) -> float | None:
        """Get timestamp of last successful publish."""
        with self._lock:
            return self._last_publish_time

    def _is_broker_config_change(self, event: ConfigChangeEvent) -> bool:
        """
        Check if configuration change event requires broker reconnection.

        Preconditions:
            - event is a valid ConfigChangeEvent

        Postconditions:
            - Returns True if event requires reconnection
            - Returns False otherwise
        """
        broker_change_events = {
            ConfigChangeEvent.BROKER_CHANGED,
            ConfigChangeEvent.PORT_CHANGED,
            ConfigChangeEvent.TLS_CHANGED,
            ConfigChangeEvent.CERTIFICATES_CHANGED,
            ConfigChangeEvent.FULL_RELOAD,
        }
        return event in broker_change_events

    def _on_config_changed(self, notification: ConfigChangeNotification) -> None:
        """
        Handle configuration change notification.

        Preconditions:
            - notification is a valid ConfigChangeNotification

        Postconditions:
            - If change requires reconnection, reconnect_required flag is set
            - Reconnect thread is started if not already running
        """
        if self._ignore_config_changes.is_set():
            self._log_info(
                f"[{self.component_name}] Ignoring config change (initiated by self): {notification.event.value}",
                "config_change_ignored"
            )
            return

        if self._is_broker_config_change(notification.event):
            self._log_info(
                f"[{self.component_name}] Broker config changed, reconnecting...",
                "config_changed"
            )
            self._reconnect_required.set()
            # Force reconnect even if current connection is still marked as connected
            with self._lock:
                if self._client:
                    with contextlib.suppress(Exception):
                        self._client.disconnect()
                    self._mqtt_connected.clear()
                    self._state = MQTTConnectionState.DISCONNECTED
            if not (self._reconnect_thread and self._reconnect_thread.is_alive()):
                self._start_reconnect_thread()

    def _build_client(self, config: MQTTClientConfig) -> tuple[mqtt_client.Client, bool]:
        """
        Build MQTT client with given configuration.

        Preconditions:
            - config is a valid MQTTClientConfig

        Postconditions:
            - Returns (client, tls_was_disabled)
            - client is configured and ready to connect
            - tls_was_disabled is True if TLS was disabled due to missing files
            - Raises RuntimeError if TLS is required but configuration invalid
        """
        client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION1,
            client_id=config.client_id,
            clean_session=False,
        )

        # Set callbacks
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_publish = self._on_publish

        # Set credentials if provided
        if config.username:
            client.username_pw_set(config.username, config.password)

        # Configure TLS if enabled
        if config.use_tls:
            # Normalize certificate paths
            ca_certs, certfile, keyfile = normalize_cert_paths(
                config.ca_certs,
                config.certfile,
                config.keyfile
            )

            # Validate certificate files
            missing_files = validate_tls_certificates(ca_certs, certfile, keyfile)
            if missing_files:
                error_msg = (
                    f"TLS enabled but certificate files not found: {', '.join(missing_files)}. "
                    "Update certificate paths or disable TLS via API."
                )
                self._log_error(
                    f"[{self.component_name}] {error_msg}",
                    "tls_missing_files"
                )
                raise RuntimeError(f"TLS configuration invalid: {error_msg}")

            # All files exist or None (system certs)
            try:
                configure_tls_on_client(
                    client,
                    ca_certs,
                    certfile,
                    keyfile,
                    config.tls_insecure
                )
                ca_info = ca_certs or "system default"
                cert_info = f"cert={certfile}, key={keyfile}" if (certfile and keyfile) else "no client cert"
                self._log_info(
                    f"[{self.component_name}] TLS enabled for MQTT connection (CA: {ca_info}, {cert_info}, insecure: {config.tls_insecure})",
                    "tls_configured"
                )
            except FileNotFoundError as e:
                self._log_error(
                    f"[{self.component_name}] Failed to configure TLS: certificate file not found: {e}.",
                    "tls_config_error"
                )
                raise RuntimeError(f"TLS configuration invalid: {e}")
            except Exception as e:
                self._log_error(
                    f"[{self.component_name}] Failed to configure TLS for MQTT client: {e}.",
                    "tls_config_error",
                    exc_info=True
                )
                raise RuntimeError(f"TLS configuration invalid: {e}")

        return client, False

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int, properties: Any = None) -> None:
        """MQTT on_connect callback."""
        if rc == 0:
            with self._lock:
                self._state = MQTTConnectionState.CONNECTED
                self._mqtt_connected.set()
                self._reconnect_delay = self.initial_reconnect_delay  # Reset backoff

            protocol = "mqtts" if self._current_config and self._current_config.use_tls else "mqtt"
            user = self._current_config.username if self._current_config else "unknown"
            self._log_info(
                "[{}] MQTT connected to {}://{}:{} as {}".format(
                    self.component_name, protocol,
                    self._current_config.broker if self._current_config else "unknown",
                    self._current_config.port if self._current_config else "unknown",
                    user),
                "mqtt_connected"
            )

            # Resubscribe to all topics
            self._resubscribe_all()

            # Fire on-connect callbacks
            with self._on_connect_callbacks_lock:
                callbacks = list(self._on_connect_callbacks)
            for cb in callbacks:
                try:
                    cb()
                except Exception as e:
                    logger.error("[%s] on_connect callback error: %s", self.component_name, e, exc_info=True)
        else:
            with self._lock:
                self._state = MQTTConnectionState.DISCONNECTED
                self._mqtt_connected.clear()

            error_msg = mqtt_client.connack_string(rc)
            self._log_error(
                f"[{self.component_name}] MQTT connection failed: {error_msg} (rc={rc})",
                "mqtt_connect_failed"
            )
            self._reconnect_required.set()

    def _on_disconnect(self, client: Any, userdata: Any, rc: int, properties: Any = None) -> None:
        """MQTT on_disconnect callback."""
        with self._lock:
            self._state = MQTTConnectionState.DISCONNECTED
            self._mqtt_connected.clear()

        if rc != 0:
            self._log_warning(
                f"[{self.component_name}] Unexpected MQTT disconnect (rc={rc}), will retry",
                "mqtt_disconnect_unexpected"
            )
            self._reconnect_required.set()
        else:
            self._log_info(
                f"[{self.component_name}] MQTT disconnected cleanly",
                "mqtt_disconnect"
            )

    def _on_message(self, client: Any, userdata: Any, message: MQTTMessage) -> None:
        """MQTT on_message callback - routes to registered handlers off paho thread."""
        topic = message.topic

        with self._message_handlers_lock:
            # Find matching handlers (exact match or pattern)
            handlers_to_call: list[Callable[..., Any]] = []
            for handler_topic, handlers in self._message_handlers.items():
                if topic == handler_topic or self._topic_matches(topic, handler_topic):
                    handlers_to_call.extend(handlers)

        # Dispatch handlers to thread pool so paho callback thread is released immediately
        for handler in handlers_to_call:
            self._handler_executor.submit(self._invoke_handler, handler, message, topic)

    def _invoke_handler(self, handler: Callable, message: MQTTMessage, topic: str) -> None:
        """Execute a single message handler (runs in thread pool)."""
        try:
            handler(message)
        except Exception as e:
            logger.error(
                "[%s] Error in message handler for topic %s: %s",
                self.component_name, topic, e,
                exc_info=True,
            )

    def _on_publish(self, client: Any, userdata: Any, mid: int, properties: Any = None) -> None:
        """MQTT on_publish callback - track successful publishes."""
        with self._lock:
            self._last_publish_time = time.time()

    def _topic_matches(self, topic: str, pattern: str) -> bool:
        """Check if topic matches pattern (supports + and # wildcards).

        MQTT spec: ``#`` matches zero or more levels, so ``a/b/#`` matches
        ``a/b``, ``a/b/c``, ``a/b/c/d``, etc.
        """
        if pattern == topic:
            return True

        pattern_parts = pattern.split('/')
        topic_parts = topic.split('/')

        for i, p in enumerate(pattern_parts):
            if p == '#':
                return True  # matches remaining levels (including zero)
            if i >= len(topic_parts):
                return False  # topic is shorter than pattern
            if p != '+' and p != topic_parts[i]:
                return False

        # All pattern parts matched — topic must not have extra levels
        return len(topic_parts) == len(pattern_parts)

    def _resubscribe_all(self) -> None:
        """Resubscribe to all registered topics."""
        with self._message_handlers_lock:
            topics = list(self._message_handlers.keys())

        if topics and self._client and self.is_connected:
            for topic in topics:
                try:
                    self._client.subscribe(topic, qos=1)
                    logger.debug("[%s] Resubscribed to %s", self.component_name, topic)
                except Exception as e:
                    logger.error("[%s] Failed to resubscribe to %s: %s", self.component_name, topic, e)

    def add_on_connect_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked after each successful MQTT (re)connect."""
        with self._on_connect_callbacks_lock:
            self._on_connect_callbacks.append(callback)

    def subscribe(self, topic: str, handler: Callable[[MQTTMessage], None]) -> None:
        """
        Subscribe to MQTT topic with message handler.

        Args:
            topic: MQTT topic (supports + and # wildcards)
            handler: Callback function that receives MQTTMessage
        """
        with self._message_handlers_lock:
            if topic not in self._message_handlers:
                self._message_handlers[topic] = set()
            self._message_handlers[topic].add(handler)

        # Subscribe immediately if connected
        if self._client and self.is_connected:
            try:
                self._client.subscribe(topic, qos=1)
                cfg = self._current_config
                broker_info = f"{cfg.broker}:{cfg.port}" if cfg else "unknown"
                client_id = cfg.client_id if cfg else "unknown"
                user = cfg.username if cfg else "anonymous"
                self._log_info(
                    f"[{self.component_name}] MQTT subscribe -> {broker_info} (client_id={client_id}, user={user}), topic={topic}",
                    "mqtt_subscribe"
                )
            except Exception as e:
                logger.error("[%s] Failed to subscribe to %s: %s", self.component_name, topic, e)

    def unsubscribe(self, topic: str, handler: Callable[[MQTTMessage], None] | None = None) -> None:
        """
        Unsubscribe from MQTT topic.

        Args:
            topic: MQTT topic
            handler: Specific handler to remove (if None, removes all handlers for topic)
        """
        with self._message_handlers_lock:
            if topic in self._message_handlers:
                if handler:
                    self._message_handlers[topic].discard(handler)
                    if not self._message_handlers[topic]:
                        del self._message_handlers[topic]
                else:
                    del self._message_handlers[topic]

        # Unsubscribe from broker if connected
        if self._client and self.is_connected:
            try:
                self._client.unsubscribe(topic)
                logger.debug("[%s] Unsubscribed from %s", self.component_name, topic)
            except Exception as e:
                logger.error("[%s] Failed to unsubscribe from %s: %s", self.component_name, topic, e)

    def publish(
        self,
        topic: str,
        payload: Any,
        qos: int = 1,
        retain: bool = False
    ) -> bool:
        """
        Publish message to MQTT topic.

        Args:
            topic: MQTT topic
            payload: Message payload (will be JSON-encoded if dict/list)
            qos: Quality of Service (0, 1, or 2)
            retain: Whether to retain message

        Returns:
            True if published successfully, False otherwise
        """
        if not self.is_connected:
            self._log_warning_throttled(
                f"[{self.component_name}] Cannot publish to {topic}: MQTT not connected",
                "publish_not_connected"
            )
            return False

        if self._client is None:
            return False

        try:
            # Convert payload to string if needed
            if isinstance(payload, (dict, list)):
                import json
                payload_str = json.dumps(payload, ensure_ascii=False)
            else:
                payload_str = str(payload)
            cfg = self._current_config
            broker_info = (
                f"{cfg.broker}:{cfg.port}" if cfg else "unknown"
            )
            client_id = cfg.client_id if cfg else "unknown"
            user = cfg.username if cfg else "anonymous"
            self._log_info(
                f"[{self.component_name}] MQTT publish -> {broker_info} (client_id={client_id}, user={user}), topic={topic}",
                "mqtt_publish"
            )

            result = self._client.publish(topic, payload_str, qos=qos, retain=retain)

            if result.rc == mqtt_client.MQTT_ERR_SUCCESS:
                with self._lock:
                    self._last_publish_time = time.time()
                return True
            else:
                self._log_error(
                    f"[{self.component_name}] Failed to publish to {topic}: rc={result.rc}",
                    "publish_failed"
                )
                return False
        except Exception as e:
            self._log_error(
                f"[{self.component_name}] Exception publishing to {topic}: {e}",
                "publish_exception",
                exc_info=True
            )
            return False

    def start(self) -> None:
        """Start MQTT client and connect."""
        with self._lock:
            if self._client is not None:
                logger.warning("[%s] MQTT client already started", self.component_name)
                return

            self._shutdown.clear()
            self._reconnect_stop.clear()
            self._reconnect_required.clear()
            self._reconnect_delay = self.initial_reconnect_delay

        # Load config and connect
        self._connect()

        # Start reconnect monitor
        self._start_reconnect_thread()

    def stop(self) -> None:
        """Stop MQTT client and disconnect."""
        with self._lock:
            if self._shutdown.is_set():
                return

            self._shutdown.set()
            self._reconnect_stop.set()

        # Stop reconnect thread
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_stop.set()
            self._reconnect_thread.join(timeout=5.0)

        # Disconnect client
        with self._lock:
            if self._client:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
                finally:
                    self._client = None
                    self._state = MQTTConnectionState.DISCONNECTED
                    self._mqtt_connected.clear()

        # Unsubscribe from config changes
        self.config_service.unsubscribe(self._on_config_changed)

        # Shut down handler thread pool
        self._handler_executor.shutdown(wait=False)

        self._log_info(f"[{self.component_name}] MQTT client stopped", "mqtt_stopped")

    def _connect(self) -> bool:
        """Connect to MQTT broker using current config."""
        with self._lock:
            if self._shutdown.is_set():
                return False

            # Get current config
            bridge_config = self.config_service.get_config()

            # Build client config
            base_client_id = bridge_config.client_id
            # Always differentiate by component to avoid client_id collisions on broker
            client_id = f"{base_client_id}-{self.component_name}"

            # Certificate paths are fixed - always use certs/ directory
            from constants import CERT_CA_FILE, CERT_CLIENT_CERT_FILE, CERT_CLIENT_KEY_FILE
            ca_certs = str(CERT_CA_FILE) if CERT_CA_FILE.exists() else None
            certfile = str(CERT_CLIENT_CERT_FILE) if CERT_CLIENT_CERT_FILE.exists() else None
            keyfile = str(CERT_CLIENT_KEY_FILE) if CERT_CLIENT_KEY_FILE.exists() else None

            client_config = MQTTClientConfig(
                broker=bridge_config.broker,
                port=bridge_config.broker_port,
                username=bridge_config.mqtt_user if bridge_config.mqtt_user else None,
                password=bridge_config.mqtt_password if bridge_config.mqtt_password else None,
                client_id=client_id,
                use_tls=bridge_config.mqtt_use_tls,
                ca_certs=ca_certs,
                certfile=certfile,
                keyfile=keyfile,
                tls_insecure=bridge_config.mqtt_tls_insecure,
                keepalive=60,  # Increased keepalive to reduce disconnections
                qos=bridge_config.mqtt_publish_qos,
            )

            self._current_config = client_config

            # Disconnect old client if exists
            if self._client:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass

            # Build new client
            try:
                self._client, _tls_was_disabled = self._build_client(client_config)
            except Exception as e:
                self._log_error(
                    f"[{self.component_name}] Failed to build MQTT client: {e}",
                    "mqtt_build_failed",
                    exc_info=True
                )
                self._state = MQTTConnectionState.ERROR
                return False

            # TLS fallback is not allowed; any TLS configuration problem is treated as fatal

            self._state = MQTTConnectionState.CONNECTING

        # Connect (outside lock)
        protocol = "mqtts" if client_config.use_tls else "mqtt"
        user = client_config.username or "anonymous"
        broker_stripped = client_config.broker.strip() if client_config.broker else None
        broker_stripped.startswith("mqtt://") or broker_stripped.startswith("mqtts://") if broker_stripped else False

        try:
            self._client.connect(client_config.broker, client_config.port, keepalive=client_config.keepalive)
            self._client.loop_start()

            # Wait for connection (with timeout)
            max_attempts = int(MQTT_CONNECTION_WAIT_TIMEOUT_SECONDS / MQTT_CONNECTION_CHECK_INTERVAL_SECONDS)
            for _ in range(max_attempts):
                if self.is_connected or self._shutdown.is_set():
                    break
                time.sleep(MQTT_CONNECTION_CHECK_INTERVAL_SECONDS)

            if self.is_connected:
                return True
            else:
                self._log_warning(
                    f"[{self.component_name}] Connection timeout to {protocol}://{client_config.broker}:{client_config.port}",
                    "mqtt_connect_timeout"
                )
                return False

        except Exception as e:
            self._log_error(
                f"[{self.component_name}] Failed to connect to {protocol}://{client_config.broker}:{client_config.port} as {user}: {e}",
                "mqtt_connect_error",
                exc_info=True
            )
            with self._lock:
                self._state = MQTTConnectionState.ERROR
            return False

    def _start_reconnect_thread(self) -> None:
        """Start reconnect monitoring thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return

        self._reconnect_stop.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            name=f"{self.component_name}_reconnect",
            daemon=True
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """Reconnection loop with exponential backoff."""
        while not self._shutdown.is_set() and not self._reconnect_stop.is_set():
            # Wait for reconnect requirement or check periodically
            if self._reconnect_required.wait(timeout=1.0):
                self._reconnect_required.clear()

            # Check if we need to reconnect
            if not self.is_connected and not self._shutdown.is_set():
                with self._lock:
                    if self._state == MQTTConnectionState.CONNECTING:
                        # Already connecting, wait
                        time.sleep(self._reconnect_delay)
                        continue

                # Attempt reconnection
                self._log_info(
                    f"[{self.component_name}] Attempting reconnection (delay={self._reconnect_delay:.1f}s)...",
                    "mqtt_reconnect_attempt"
                )

                success = self._connect()

                if not success:
                    # Increase delay for next attempt
                    with self._lock:
                        self._reconnect_delay = min(
                            self._reconnect_delay * self.reconnect_backoff_multiplier,
                            self.max_reconnect_delay
                        )
                        self._state = MQTTConnectionState.RECONNECTING

                    # Wait before next attempt
                    time.sleep(self._reconnect_delay)
                else:
                    # Reset delay on success
                    with self._lock:
                        self._reconnect_delay = self.initial_reconnect_delay
            else:
                # Connected, wait a bit before next check
                time.sleep(5.0)

