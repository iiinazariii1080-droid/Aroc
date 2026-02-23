"""Fake MQTT client for testing pub/sub without a real broker.

Usage::

    from testing_utils.fakes.mqtt import FakeMQTTClient

    @pytest.fixture
    def mqtt():
        return FakeMQTTClient()
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class MQTTMessage:
    """Represents a published MQTT message."""

    topic: str
    payload: bytes | str | dict
    qos: int = 0
    retain: bool = False

    @property
    def payload_str(self) -> str:
        if isinstance(self.payload, bytes):
            return self.payload.decode("utf-8", errors="replace")
        if isinstance(self.payload, dict):
            return json.dumps(self.payload)
        return str(self.payload)

    @property
    def payload_dict(self) -> dict:
        if isinstance(self.payload, dict):
            return self.payload
        raw = self.payload_str
        return json.loads(raw)


class FakeMQTTClient:
    """In-memory MQTT client stub.

    Supports publish/subscribe for testing without a real broker.
    """

    def __init__(self) -> None:
        self.published: list[MQTTMessage] = []
        self._subscriptions: dict[str, list[Callable]] = {}
        self.connected: bool = False
        self._connect_called: int = 0
        self._disconnect_called: int = 0

    # ── lifecycle ───────────────────────────────────────────────────────

    def connect(self, host: str = "localhost", port: int = 1883, **kwargs: Any) -> None:
        self.connected = True
        self._connect_called += 1

    def disconnect(self) -> None:
        self.connected = False
        self._disconnect_called += 1

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    # ── pub/sub ─────────────────────────────────────────────────────────

    def publish(
        self,
        topic: str,
        payload: Any = None,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        msg = MQTTMessage(topic=topic, payload=payload, qos=qos, retain=retain)
        self.published.append(msg)
        # Deliver to matching subscribers
        for pattern, callbacks in self._subscriptions.items():
            if self._topic_matches(pattern, topic):
                for cb in callbacks:
                    cb(msg)

    def subscribe(self, topic: str, callback: Callable | None = None) -> None:
        if topic not in self._subscriptions:
            self._subscriptions[topic] = []
        if callback:
            self._subscriptions[topic].append(callback)

    # ── helpers ─────────────────────────────────────────────────────────

    def get_published(self, topic_filter: str | None = None) -> list[MQTTMessage]:
        """Return published messages, optionally filtered by topic prefix."""
        if topic_filter is None:
            return list(self.published)
        return [m for m in self.published if m.topic.startswith(topic_filter)]

    def clear(self) -> None:
        """Reset all state."""
        self.published.clear()
        self._subscriptions.clear()

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """Simple MQTT wildcard matching (+ and #)."""
        pat_parts = pattern.split("/")
        top_parts = topic.split("/")
        for i, pp in enumerate(pat_parts):
            if pp == "#":
                return True
            if i >= len(top_parts):
                return False
            if pp != "+" and pp != top_parts[i]:
                return False
        return len(pat_parts) == len(top_parts)
