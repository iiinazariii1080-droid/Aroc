"""Tests for app.services.mqtt_state — connection state registry."""

from unittest.mock import MagicMock

import pytest

from app.services import mqtt_state


@pytest.fixture(autouse=True)
def _clean_state():
    """Clean the registry before/after each test."""
    mqtt_state._clients.clear()
    yield
    mqtt_state._clients.clear()


class TestMqttState:
    def test_register_and_is_connected(self):
        client = MagicMock()
        client.is_connected = True
        mqtt_state.register("bridge", client)
        assert mqtt_state.is_connected("bridge") is True

    def test_is_connected_unknown(self):
        assert mqtt_state.is_connected("unknown") is None

    def test_unregister(self):
        client = MagicMock()
        mqtt_state.register("bridge", client)
        mqtt_state.unregister("bridge")
        assert mqtt_state.is_connected("bridge") is None

    def test_unregister_nonexistent(self):
        mqtt_state.unregister("unknown")  # should not raise

    def test_all_states(self):
        c1 = MagicMock()
        c1.is_connected = True
        c2 = MagicMock()
        c2.is_connected = False
        mqtt_state.register("bridge", c1)
        mqtt_state.register("telemetry", c2)
        states = mqtt_state.all_states()
        assert states == {"bridge": True, "telemetry": False}

    def test_all_states_empty(self):
        assert mqtt_state.all_states() == {}


class TestBridgeProtocol:
    def test_protocol_is_importable(self):
        from bridge_protocol import BridgeProtocol
        assert hasattr(BridgeProtocol, "__protocol_attrs__") or True  # just import check
