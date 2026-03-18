"""Stateful test doubles for MQTT client.

HonestFakeMQTTClient matches LightMQTTClient's behavioral contract:
- 5-state state machine (DISCONNECTED/CONNECTING/CONNECTED/RECONNECTING/ERROR)
- subscribe/unsubscribe with handler registry
- on_connect callbacks fired on start() and simulate_reconnect()
- _resubscribe_all() logging
- publish() with publish_succeeds toggle (can fail while connected)
- inject_message() for simulating inbound MQTT messages
- Topic wildcard matching (+/#)

FakeLightMQTTClient preserved for backward compatibility with telemetry tests.
"""

import json
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from shared.mqtt_client import MQTTConnectionState


class HonestFakeMQTTClient:
    """Honest fake matching LightMQTTClient's behavioral contract.

    No paho, no network, no threading pools — but implements:
    - State machine matching production states
    - subscribe/unsubscribe with handler dispatch
    - on_connect callbacks fired on start() and simulate_reconnect()
    - Resubscription tracking
    - Topic wildcard matching (+ and #)
    - publish_succeeds toggle (can fail while connected)
    - inject_message for inbound simulation
    """

    def __init__(
        self,
        config: Any = None,
        component_name: str = "test-mqtt",
        *,
        start_connected: bool = True,
        publish_succeeds: bool = True,
    ) -> None:
        self._config = config
        self.component_name = component_name
        self._start_connected = start_connected
        self._publish_succeeds = publish_succeeds

        self._state = MQTTConnectionState.DISCONNECTED
        self._mqtt_connected = threading.Event()
        self._shutdown = threading.Event()

        self._message_handlers: dict[str, set[Callable]] = {}
        self._message_handlers_lock = threading.Lock()

        self._on_connect_callbacks: list[Callable[[], None]] = []
        self._on_connect_callbacks_lock = threading.Lock()

        # Assertion logs
        self.publish_log: list[tuple[str, str, int, bool]] = []
        self.subscribe_log: list[str] = []
        self.unsubscribe_log: list[str] = []
        self.resubscribe_log: list[list[str]] = []

    # -- Properties matching LightMQTTClient --

    @property
    def is_connected(self) -> bool:
        return self._mqtt_connected.is_set()

    @property
    def state(self) -> MQTTConnectionState:
        return self._state

    # -- Lifecycle --

    def start(self) -> None:
        """Start the fake client. If start_connected, transitions to CONNECTED."""
        self._shutdown.clear()
        if self._start_connected:
            self._state = MQTTConnectionState.CONNECTING
            self._transition_to_connected()

    def stop(self) -> None:
        """Stop the fake client."""
        self._shutdown.set()
        self._state = MQTTConnectionState.DISCONNECTED
        self._mqtt_connected.clear()

    # -- Subscribe / Unsubscribe --

    def subscribe(self, topic: str, handler: Callable) -> None:
        with self._message_handlers_lock:
            if topic not in self._message_handlers:
                self._message_handlers[topic] = set()
            self._message_handlers[topic].add(handler)
        self.subscribe_log.append(topic)

    def unsubscribe(self, topic: str, handler: Callable | None = None) -> None:
        with self._message_handlers_lock:
            if topic in self._message_handlers:
                if handler:
                    self._message_handlers[topic].discard(handler)
                    if not self._message_handlers[topic]:
                        del self._message_handlers[topic]
                else:
                    del self._message_handlers[topic]
        self.unsubscribe_log.append(topic)

    def add_on_connect_callback(self, callback: Callable[[], None]) -> None:
        with self._on_connect_callbacks_lock:
            self._on_connect_callbacks.append(callback)

    # -- Publish --

    def publish(self, topic: str, payload: str | dict | list, qos: int = 1, retain: bool = False) -> bool:
        """Publish with same semantics as LightMQTTClient.publish."""
        if not self.is_connected:
            return False

        if not self._publish_succeeds:
            return False

        # Serialize dicts/lists to JSON (matching LightMQTTClient behavior)
        if isinstance(payload, (dict, list)):
            payload_str = json.dumps(payload, ensure_ascii=False)
        else:
            payload_str = str(payload)

        self.publish_log.append((topic, payload_str, qos, retain))
        return True

    # -- Simulation methods (test control) --

    def simulate_disconnect(self) -> None:
        """Simulate unexpected MQTT disconnection (like broker drop)."""
        self._state = MQTTConnectionState.DISCONNECTED
        self._mqtt_connected.clear()

    def simulate_reconnect(self) -> None:
        """Simulate successful reconnection: state→CONNECTED, resubscribe, fire callbacks."""
        self._state = MQTTConnectionState.RECONNECTING
        self._transition_to_connected()

    def inject_message(self, topic: str, payload_bytes: bytes) -> None:
        """Simulate an inbound MQTT message dispatched to matching handlers."""
        msg = SimpleNamespace(topic=topic, payload=payload_bytes)
        handlers_to_call: list[Callable] = []
        with self._message_handlers_lock:
            for handler_topic, handlers in self._message_handlers.items():
                if topic == handler_topic or self._topic_matches(topic, handler_topic):
                    handlers_to_call.extend(handlers)
        for handler in handlers_to_call:
            handler(msg)

    def set_publish_succeeds(self, value: bool) -> None:
        """Toggle publish success/failure while connected."""
        self._publish_succeeds = value

    # -- Internal --

    def _transition_to_connected(self) -> None:
        """Common transition to CONNECTED state with resubscription and callbacks."""
        self._state = MQTTConnectionState.CONNECTED
        self._mqtt_connected.set()

        # Resubscribe all topics (matching LightMQTTClient._resubscribe_all)
        with self._message_handlers_lock:
            topics = list(self._message_handlers.keys())
        if topics:
            self.resubscribe_log.append(topics)

        # Fire on_connect callbacks (matching LightMQTTClient._on_connect)
        with self._on_connect_callbacks_lock:
            callbacks = list(self._on_connect_callbacks)
        for cb in callbacks:
            cb()

    @staticmethod
    def _topic_matches(topic: str, pattern: str) -> bool:
        """MQTT topic matching with + and # wildcards.

        Matches LightMQTTClient._topic_matches logic exactly.
        """
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


# ---------------------------------------------------------------------------
# Legacy fake — preserved for backward compatibility with telemetry tests
# ---------------------------------------------------------------------------


class FakeLightMQTTClient:
    """Stateful fake replacing LightMQTTClient in tests.

    Tracks connect/disconnect state and publish calls.
    No threading, no paho, no network — pure state tracking.

    DEPRECATED: Use HonestFakeMQTTClient for new tests.
    """

    def __init__(
        self,
        *,
        start_connected: bool = True,
        publish_succeeds: bool = True,
        raise_on_init: Exception | None = None,
    ) -> None:
        if raise_on_init is not None:
            raise raise_on_init
        self._connected = False
        self._start_connected = start_connected
        self._publish_succeeds = publish_succeeds
        self._stopped = False
        self.publish_log: list[tuple[str, str, int, bool]] = []

    def start(self) -> None:
        if self._start_connected:
            self._connected = True

    def stop(self) -> None:
        self._connected = False
        self._stopped = True

    @property
    def is_connected(self) -> bool:
        return self._connected

    def publish(self, topic: str, payload: str | dict | list, qos: int = 1, retain: bool = False) -> bool:
        if not self._connected:
            return False
        if not self._publish_succeeds:
            return False
        self.publish_log.append((topic, payload, qos, retain))
        return True
