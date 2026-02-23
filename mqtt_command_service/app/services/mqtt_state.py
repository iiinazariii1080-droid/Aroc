"""Lightweight MQTT connection state registry.

Components (bridge, telemetry) register their UnifiedMQTTClient here
so that the health-check endpoint can query cached connection state
instead of opening an expensive test connection on every call.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.mqtt_client_service import UnifiedMQTTClient

_lock = threading.Lock()
_clients: dict[str, UnifiedMQTTClient] = {}


def register(name: str, client: UnifiedMQTTClient) -> None:
    """Register an MQTT client under *name* (e.g. ``"bridge"``, ``"telemetry"``)."""
    with _lock:
        _clients[name] = client


def unregister(name: str) -> None:
    """Remove a previously registered client."""
    with _lock:
        _clients.pop(name, None)


def is_connected(name: str) -> bool | None:
    """Return ``True``/``False`` for a registered client, or ``None`` if unknown."""
    with _lock:
        client = _clients.get(name)
    if client is None:
        return None
    return client.is_connected


def all_states() -> dict[str, bool | None]:
    """Return ``{name: is_connected}`` for every registered client."""
    with _lock:
        names = list(_clients.keys())
    return {n: is_connected(n) for n in names}
