"""Lightweight MQTT client with automatic reconnection.

Accepts MQTTConnectionConfig directly — no ConfigService dependency.
Config changes require container restart.

Design decision — clean_session=True:
    This client uses clean_session=True intentionally.  When the client
    reconnects after a network outage, the broker does NOT replay queued
    QoS 1 messages.  This prevents stale commands (e.g. a navigateTo sent
    minutes ago) from being executed after reconnection.

    Trade-off: any QoS 1 message that the broker accepted but could not
    deliver during a disconnection is lost permanently.  For safety-critical
    commands (e-stop), at-least-once delivery is guaranteed at the HTTP
    level via the retry loop in CommandDispatcher, not via MQTT QoS.

    For non-critical responses, the PendingResultQueue caches outbound
    messages during MQTT downtime and flushes them on reconnect.
"""

import concurrent.futures
import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from paho.mqtt import client as mqtt_client
from paho.mqtt.client import MQTTMessage

from shared.config_types import MQTTConnectionConfig
from shared.constants import (
    LOG_THROTTLE_INTERVAL_SECONDS,
    MQTT_CONNECTION_CHECK_INTERVAL_SECONDS,
    MQTT_CONNECTION_WAIT_TIMEOUT_SECONDS,
    get_cert_path,
)

logger = logging.getLogger(__name__)


class MQTTConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


_KEEPALIVE = 60


class LightMQTTClient:
    """MQTT client with automatic reconnection and state management."""

    def __init__(
        self,
        config: MQTTConnectionConfig,
        component_name: str = "mqtt_client",
        max_reconnect_delay: float = 30.0,
        initial_reconnect_delay: float = 1.0,
    ):
        self._mqtt_config = config
        self.component_name = component_name
        self.max_reconnect_delay = max_reconnect_delay
        self.initial_reconnect_delay = initial_reconnect_delay

        self._lock = threading.RLock()
        self._client: mqtt_client.Client | None = None
        self._state = MQTTConnectionState.DISCONNECTED
        self._mqtt_connected = threading.Event()
        self._shutdown = threading.Event()

        self._reconnect_delay = initial_reconnect_delay
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_stop = threading.Event()
        # Generation-based reconnect signaling — no lost reconnect requests
        self._reconnect_cond = threading.Condition()
        self._reconnect_generation: int = 0
        self._reconnect_seen_generation: int = 0

        self._message_handlers: dict[str, set[Callable[[MQTTMessage], None]]] = {}
        self._message_handlers_lock = threading.Lock()

        self._on_connect_callbacks: list[Callable[[], None]] = []
        self._on_connect_callbacks_lock = threading.Lock()

        self._last_log_time: dict[str, float] = {}
        self._log_throttle_interval = LOG_THROTTLE_INTERVAL_SECONDS

        self._last_publish_time: float | None = None

        self._handler_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix=f"{component_name}-msg"
        )

        # Resolve component-specific client_id and cert paths once at init
        self._effective_client_id = f"{config.client_id}-{component_name}"
        self._resolved_ca = config.mqtt_ca_certs or (str(p) if (p := get_cert_path("ca.crt")).exists() else None)
        self._resolved_cert = config.mqtt_certfile or (str(p) if (p := get_cert_path("client.crt")).exists() else None)
        self._resolved_key = config.mqtt_keyfile or (str(p) if (p := get_cert_path("client.key")).exists() else None)

    @property
    def is_connected(self) -> bool:
        return self._mqtt_connected.is_set()

    @property
    def state(self) -> MQTTConnectionState:
        with self._lock:
            return self._state

    @property
    def last_publish_time(self) -> float | None:
        with self._lock:
            return self._last_publish_time

    def _build_paho_client(self) -> mqtt_client.Client:
        cfg = self._mqtt_config
        client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=self._effective_client_id,
            clean_session=True,  # Intentional: prevents stale command replay. See module docstring.
        )

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_publish = self._on_publish

        # In pure mTLS mode, identity comes from the client certificate —
        # do not send username/password.  In password or transition mode, send them.
        if cfg.auth_mode != "mtls" and cfg.mqtt_user:
            client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password or None)

        if cfg.mqtt_use_tls:
            ca = self._resolved_ca
            cert = self._resolved_cert
            key = self._resolved_key

            # Normalize: strip whitespace, empty → None
            ca = ca.strip() if (ca and ca.strip()) else None
            cert = cert.strip() if (cert and cert.strip()) else None
            key = key.strip() if (key and key.strip()) else None

            # Validate files exist
            missing = []
            if ca and not Path(ca).exists():
                missing.append(f"CA cert: {ca}")
            if cert and not Path(cert).exists():
                missing.append(f"Client cert: {cert}")
            if key and not Path(key).exists():
                missing.append(f"Client key: {key}")
            if (cert and not key) or (key and not cert):
                missing.append("Client cert and key must both be specified or both omitted")
            if missing:
                raise RuntimeError(f"TLS configuration invalid: {', '.join(missing)}")

            # Guard: mqtt_tls_insecure requires explicit env-var opt-in.
            # Without ALLOW_MQTT_TLS_INSECURE=true the flag is silently
            # downgraded to secure mode — prevents accidental MITM exposure
            # from a stale config-api setting.
            tls_insecure_requested = cfg.mqtt_tls_insecure
            allow_insecure = os.environ.get("ALLOW_MQTT_TLS_INSECURE", "").lower() in ("1", "true", "yes")
            if tls_insecure_requested and not allow_insecure:
                logger.error(
                    "[%s] MQTT_TLS_INSECURE=true in broker config but ALLOW_MQTT_TLS_INSECURE "
                    "env var is not set — enforcing certificate verification. "
                    "Set ALLOW_MQTT_TLS_INSECURE=true to explicitly allow insecure connections.",
                    self.component_name,
                )
                tls_insecure_requested = False

            if tls_insecure_requested and not ca:
                # Self-signed cert without CA: skip all verification
                import ssl

                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                if cert and key:
                    ssl_context.load_cert_chain(cert, key)
                client.tls_set_context(ssl_context)
            else:
                client.tls_set(ca_certs=ca, certfile=cert, keyfile=key)
            if tls_insecure_requested:
                client.tls_insecure_set(True)
                logger.critical(
                    "[%s] TLS certificate verification DISABLED — connections are vulnerable to MITM attacks",
                    self.component_name,
                )

            logger.info(
                "[%s] TLS enabled (CA: %s, insecure: %s)",
                self.component_name,
                ca or "system default",
                tls_insecure_requested,
            )

        return client

    def _on_connect(self, client: Any, userdata: Any, connect_flags: Any, reason_code: Any, properties: Any) -> None:
        if not reason_code.is_failure:
            with self._lock:
                self._state = MQTTConnectionState.CONNECTED
                self._mqtt_connected.set()
                self._reconnect_delay = self.initial_reconnect_delay

            cfg = self._mqtt_config
            protocol = "mqtts" if cfg.mqtt_use_tls else "mqtt"
            logger.info(
                "[%s] MQTT connected to %s://%s:%s as %s",
                self.component_name,
                protocol,
                cfg.broker,
                cfg.broker_port,
                cfg.mqtt_user or "anonymous",
            )

            self._resubscribe_all()

            with self._on_connect_callbacks_lock:
                callbacks = list(self._on_connect_callbacks)
            for cb in callbacks:
                with contextlib.suppress(RuntimeError):
                    self._handler_executor.submit(cb)
        else:
            with self._lock:
                self._state = MQTTConnectionState.DISCONNECTED
                self._mqtt_connected.clear()
            logger.error("[%s] MQTT connection failed: %s", self.component_name, reason_code)
            with self._reconnect_cond:
                self._reconnect_generation += 1
                self._reconnect_cond.notify()

    def _on_disconnect(
        self, client: Any, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any
    ) -> None:
        with self._lock:
            self._state = MQTTConnectionState.DISCONNECTED
            self._mqtt_connected.clear()

        if reason_code.is_failure:
            logger.warning("[%s] Unexpected MQTT disconnect (%s), will retry", self.component_name, reason_code)
            with self._reconnect_cond:
                self._reconnect_generation += 1
                self._reconnect_cond.notify()
        else:
            logger.info("[%s] MQTT disconnected cleanly", self.component_name)

    def _on_message(self, client: Any, userdata: Any, message: MQTTMessage) -> None:
        topic = message.topic
        with self._message_handlers_lock:
            handlers_to_call: list[Callable[..., Any]] = []
            for handler_topic, handlers in self._message_handlers.items():
                if topic == handler_topic or self._topic_matches(topic, handler_topic):
                    handlers_to_call.extend(handlers)

        for handler in handlers_to_call:
            self._handler_executor.submit(self._invoke_handler, handler, message, topic)

    def _invoke_handler(self, handler: Callable, message: MQTTMessage, topic: str) -> None:
        try:
            handler(message)
        except Exception as e:
            logger.error("[%s] Error in handler for %s: %s", self.component_name, topic, e, exc_info=True)

    def _on_publish(self, client: Any, userdata: Any, mid: int, reason_code: Any, properties: Any) -> None:
        with self._lock:
            self._last_publish_time = time.time()

    def _topic_matches(self, topic: str, pattern: str) -> bool:
        if pattern == topic:
            return True
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")
        for i, p in enumerate(pattern_parts):
            if p == "#":
                return True
            if i >= len(topic_parts):
                return False
            if p != "+" and p != topic_parts[i]:
                return False
        return len(topic_parts) == len(pattern_parts)

    def _resubscribe_all(self) -> None:
        with self._message_handlers_lock:
            topics = list(self._message_handlers.keys())
        if topics and self._client and self.is_connected:
            qos = self._mqtt_config.mqtt_publish_qos
            for topic in topics:
                try:
                    self._client.subscribe(topic, qos=qos)
                    logger.debug("[%s] Resubscribed to %s", self.component_name, topic)
                except Exception as e:
                    logger.error("[%s] Failed to resubscribe to %s: %s", self.component_name, topic, e)

    def add_on_connect_callback(self, callback: Callable[[], None]) -> None:
        with self._on_connect_callbacks_lock:
            self._on_connect_callbacks.append(callback)

    def subscribe(self, topic: str, handler: Callable[[MQTTMessage], None]) -> None:
        with self._message_handlers_lock:
            if topic not in self._message_handlers:
                self._message_handlers[topic] = set()
            self._message_handlers[topic].add(handler)

        # Snapshot client under lock to avoid race with stop()
        with self._lock:
            client = self._client
        if client and self.is_connected:
            try:
                client.subscribe(topic, qos=self._mqtt_config.mqtt_publish_qos)
                logger.info("[%s] Subscribed to %s", self.component_name, topic)
            except Exception as e:
                logger.error("[%s] Failed to subscribe to %s: %s", self.component_name, topic, e)

    def unsubscribe(self, topic: str, handler: Callable[[MQTTMessage], None] | None = None) -> None:
        with self._message_handlers_lock:
            if topic in self._message_handlers:
                if handler:
                    self._message_handlers[topic].discard(handler)
                    if not self._message_handlers[topic]:
                        del self._message_handlers[topic]
                else:
                    del self._message_handlers[topic]

        # Snapshot client under lock to avoid race with stop()
        with self._lock:
            client = self._client
        if client and self.is_connected:
            try:
                client.unsubscribe(topic)
            except Exception as e:
                logger.error("[%s] Failed to unsubscribe from %s: %s", self.component_name, topic, e)

    def publish(self, topic: str, payload: str | dict | list, qos: int = 1, retain: bool = False) -> bool:
        """Publish a message to an MQTT topic.

        Args:
            payload: Pre-serialized JSON string, or a dict/list that will be
                     JSON-encoded automatically.  Callers that need size
                     validation should pre-serialize and pass a ``str``.
        """
        # Snapshot client under lock to avoid race with stop()
        with self._lock:
            client = self._client
        if not self.is_connected or client is None:
            return False

        try:
            if isinstance(payload, (dict, list)):
                payload_str = json.dumps(payload, ensure_ascii=False)
            else:
                payload_str = str(payload)

            result = client.publish(topic, payload_str, qos=qos, retain=retain)
            return result.rc == mqtt_client.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error("[%s] Exception publishing to %s: %s", self.component_name, topic, e, exc_info=True)
            return False

    def start(self) -> None:
        with self._lock:
            if self._client is not None:
                logger.warning("[%s] Already started", self.component_name)
                return
            self._shutdown.clear()
            self._reconnect_stop.clear()
            self._reconnect_generation = 0
            self._reconnect_seen_generation = 0
            self._reconnect_delay = self.initial_reconnect_delay

        self._connect()
        self._start_reconnect_thread()

    def stop(self) -> None:
        with self._lock:
            if self._shutdown.is_set():
                return
            self._shutdown.set()
            self._reconnect_stop.set()

        # Wake the reconnect loop so it can observe shutdown
        with self._reconnect_cond:
            self._reconnect_cond.notify()

        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_stop.set()
            self._reconnect_thread.join(timeout=5.0)

        # Detach client under lock, then tear down outside lock (same
        # deadlock-avoidance pattern as _connect).
        with self._lock:
            client = self._client
            self._client = None
            self._state = MQTTConnectionState.DISCONNECTED
            self._mqtt_connected.clear()

        self._teardown_client(client)

        # Allow in-flight message handlers to finish (up to 5s) so that
        # HTTP responses already in progress are not silently dropped.
        # cancel_futures=True cancels only queued (not yet started) tasks.
        self._handler_executor.shutdown(wait=True, cancel_futures=True)
        logger.info("[%s] MQTT client stopped", self.component_name)

    def _teardown_client(self, client: mqtt_client.Client | None) -> None:
        """Stop and disconnect a paho client **without** holding ``_lock``.

        Paho's ``loop_stop()`` blocks until the network-loop thread exits.
        That thread may call ``_on_disconnect`` which acquires ``_lock``.
        If we held the lock here we would deadlock.
        """
        if client is None:
            return
        with contextlib.suppress(Exception):
            client.disconnect()
        with contextlib.suppress(Exception):
            client.loop_stop()

    def _connect(self) -> bool:
        if self._shutdown.is_set():
            return False

        cfg = self._mqtt_config

        # Detach old client under the lock, then tear it down outside the lock
        # to avoid deadlocking with _on_disconnect.
        with self._lock:
            if self._shutdown.is_set():
                return False
            old_client = self._client
            self._client = None

        self._teardown_client(old_client)

        with self._lock:
            if self._shutdown.is_set():
                return False
            try:
                self._client = self._build_paho_client()
            except Exception as e:
                logger.error("[%s] Failed to build MQTT client: %s", self.component_name, e, exc_info=True)
                self._state = MQTTConnectionState.ERROR
                return False

            self._state = MQTTConnectionState.CONNECTING
            client = self._client  # local ref: stop() may set self._client = None

        protocol = "mqtts" if cfg.mqtt_use_tls else "mqtt"
        try:
            client.connect(cfg.broker, cfg.broker_port, keepalive=_KEEPALIVE)
            client.loop_start()

            max_attempts = int(MQTT_CONNECTION_WAIT_TIMEOUT_SECONDS / MQTT_CONNECTION_CHECK_INTERVAL_SECONDS)
            for _ in range(max_attempts):
                if self.is_connected or self._shutdown.is_set():
                    break
                self._mqtt_connected.wait(timeout=MQTT_CONNECTION_CHECK_INTERVAL_SECONDS)

            if self.is_connected:
                return True

            logger.warning(
                "[%s] Connection timeout to %s://%s:%s",
                self.component_name,
                protocol,
                cfg.broker,
                cfg.broker_port,
            )
            with self._lock:
                self._state = MQTTConnectionState.RECONNECTING
            return False

        except Exception as e:
            logger.error(
                "[%s] Failed to connect to %s://%s:%s: %s",
                self.component_name,
                protocol,
                cfg.broker,
                cfg.broker_port,
                e,
                exc_info=True,
            )
            with self._lock:
                self._state = MQTTConnectionState.ERROR
            return False

    def _start_reconnect_thread(self) -> None:
        with self._lock:
            if self._reconnect_thread and self._reconnect_thread.is_alive():
                return
            self._reconnect_stop.clear()
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop,
                name=f"{self.component_name}_reconnect",
                daemon=True,
            )
            self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        while not self._shutdown.is_set() and not self._reconnect_stop.is_set():
            # Wait for a reconnect signal (generation increment) or timeout
            with self._reconnect_cond:
                while self._reconnect_seen_generation == self._reconnect_generation:
                    if self._shutdown.is_set() or self._reconnect_stop.is_set():
                        return
                    # Wait with timeout — also handles periodic connectivity checks
                    self._reconnect_cond.wait(timeout=1.0)
                self._reconnect_seen_generation = self._reconnect_generation

            if not self.is_connected and not self._shutdown.is_set():
                with self._lock:
                    delay = self._reconnect_delay

                logger.info("[%s] Reconnecting (delay=%.1fs)...", self.component_name, delay)
                success = self._connect()

                if not success:
                    with self._lock:
                        self._reconnect_delay = min(
                            self._reconnect_delay * 2.0,
                            self.max_reconnect_delay,
                        )
                        self._state = MQTTConnectionState.RECONNECTING
                        delay = self._reconnect_delay
                    self._shutdown.wait(timeout=delay)
                else:
                    with self._lock:
                        self._reconnect_delay = self.initial_reconnect_delay
            else:
                self._shutdown.wait(timeout=5.0)
