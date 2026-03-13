"""Tests for bridge.py — safety gate: _handle_safety_message, _is_safety_locked,
and command rejection when safety lockout is active.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from config import BridgeConfig, ServiceConfig


# ── Helpers ──────────────────────────────────────────────────────────

def _make_config(**overrides) -> BridgeConfig:
    defaults = dict(
        broker="localhost",
        broker_port=1883,
        mqtt_user="user",
        mqtt_password="pass",
        robot_id="test-robot",
        client_id="test-client",
        http_timeout=5.0,
        task_poll_interval=1.0,
        task_poll_timeout=30.0,
        services={
            "robot": ServiceConfig(name="robot", base_url="http://localhost:8110", watch_tasks=True),
            "igus": ServiceConfig(name="igus", base_url="http://localhost:8101"),
        },
        status_heartbeat_interval=15.0,
        mqtt_publish_qos=0,
    )
    defaults.update(overrides)
    return BridgeConfig(**defaults)


def _make_bridge(config=None):
    """Create a MqttCommandBridge with all heavy dependencies mocked."""
    cfg = config or _make_config()
    with (
        patch("bridge.get_config_service") as mock_cs,
        patch("bridge.UnifiedMQTTClient") as mock_mqtt_cls,
        patch("bridge.HubAuthManager", return_value=None),
        patch("env_settings.get_env_settings") as mock_env,
    ):
        mock_cs.return_value.get_config.return_value = cfg
        mock_cs.return_value.subscribe = MagicMock()
        mock_env.return_value.parse_long_operations.return_value = None

        mqtt_inst = MagicMock()
        mqtt_inst.is_connected = True
        mqtt_inst.publish.return_value = True
        mock_mqtt_cls.return_value = mqtt_inst

        from bridge import MqttCommandBridge
        bridge = MqttCommandBridge(config=cfg)
        bridge.mqtt_client = mqtt_inst
    return bridge


def _mqtt_msg(payload: bytes | str) -> MagicMock:
    """Simulate a paho.mqtt MQTTMessage with the given payload."""
    msg = MagicMock()
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    msg.payload = payload
    msg.topic = "aroc/robot/test-robot/status/safety"
    return msg


# ── _handle_safety_message ──────────────────────────────────────────


class TestHandleSafetyMessage:
    def test_valid_lockout_payload_cached(self):
        """P0: valid lockout JSON is cached in _safety_state."""
        bridge = _make_bridge()
        payload = {"safety_lockout": True, "reason": "estop"}
        bridge._handle_safety_message(_mqtt_msg(json.dumps(payload)))

        assert bridge._safety_state == payload
        assert bridge._safety_state_ts > 0

    def test_valid_ok_payload_cached(self):
        """P1: valid OK payload is cached."""
        bridge = _make_bridge()
        payload = {"safety_lockout": False, "reason": None}
        bridge._handle_safety_message(_mqtt_msg(json.dumps(payload)))

        assert bridge._safety_state == payload
        assert bridge._safety_state["safety_lockout"] is False

    def test_malformed_json_no_crash(self):
        """P1: broken JSON → warning log, no crash, state unchanged."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": False}
        original = bridge._safety_state

        bridge._handle_safety_message(_mqtt_msg(b"not-json{{{"))
        # State should remain unchanged
        assert bridge._safety_state is original

    def test_non_dict_payload_ignored(self):
        """P1: valid JSON but not a dict → state not updated."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": False}
        original = bridge._safety_state

        bridge._handle_safety_message(_mqtt_msg(json.dumps([1, 2, 3])))
        assert bridge._safety_state is original


# ── _is_safety_locked ───────────────────────────────────────────────


class TestIsSafetyLocked:
    def test_none_returns_false_grace_period(self):
        """P0: _safety_state=None (startup) → False (grace period)."""
        bridge = _make_bridge()
        assert bridge._safety_state is None
        assert bridge._is_safety_locked() is False

    def test_lockout_returns_true(self):
        """P0: cached lockout → True."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": True, "reason": "estop"}
        bridge._safety_state_ts = time.time()
        assert bridge._is_safety_locked() is True

    def test_ok_returns_false(self):
        """P0: cached OK → False."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": False, "reason": None}
        bridge._safety_state_ts = time.time()
        assert bridge._is_safety_locked() is False

    def test_stale_heartbeat_returns_true(self):
        """P0: heartbeat >60s old → True (fail-closed)."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": False}
        bridge._safety_state_ts = time.time() - 120  # 2 minutes old
        assert bridge._is_safety_locked() is True

    def test_missing_key_returns_false(self):
        """Edge case: dict without safety_lockout key → False."""
        bridge = _make_bridge()
        bridge._safety_state = {"reason": "something"}
        bridge._safety_state_ts = time.time()
        assert bridge._is_safety_locked() is False


# ── Safety gate in _handle_command_message ──────────────────────────


class TestSafetyGateCommand:
    def _dispatch(self, bridge, command_name: str, command_id: str = "cmd-1", **extra):
        """Call _handle_command_message with a minimal valid dict."""
        data = {"command_id": command_id, **extra}
        bridge._handle_command_message(command_name, data)

    def test_navigateto_rejected_when_locked(self):
        """P0: navigateTo rejected during safety lockout."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": True, "reason": "estop"}
        bridge._safety_state_ts = time.time()
        bridge._publish_command_error = MagicMock()
        bridge._finish_command = MagicMock()
        # Must pass dedup
        bridge._dedup.try_start = MagicMock(return_value=True)

        self._dispatch(bridge, "navigateTo", target_id="A")

        bridge._publish_command_error.assert_called_once()
        args = bridge._publish_command_error.call_args[0]
        assert "safety lockout" in args[2].lower()
        bridge._finish_command.assert_called_once_with("cmd-1")

    def test_cancel_rejected_when_locked(self):
        """P0: cancel rejected during safety lockout."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": True, "reason": "relay_open"}
        bridge._safety_state_ts = time.time()
        bridge._publish_command_error = MagicMock()
        bridge._finish_command = MagicMock()
        bridge._dedup.try_start = MagicMock(return_value=True)

        self._dispatch(bridge, "cancel")

        bridge._publish_command_error.assert_called_once()
        bridge._finish_command.assert_called_once()

    def test_estop_not_blocked(self):
        """P0: estop command always passes through — never blocked by safety gate."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": True, "reason": "estop"}
        bridge._safety_state_ts = time.time()
        bridge._handle_estop_command = MagicMock()
        bridge._dedup.try_start = MagicMock(return_value=True)

        self._dispatch(bridge, "estop")

        bridge._handle_estop_command.assert_called_once()

    def test_navigateto_allowed_when_safe(self):
        """P1: navigateTo proceeds when safety OK."""
        bridge = _make_bridge()
        bridge._safety_state = {"safety_lockout": False}
        bridge._safety_state_ts = time.time()
        bridge._handle_navigate_command = MagicMock()
        bridge._dedup.try_start = MagicMock(return_value=True)

        self._dispatch(bridge, "navigateTo", target_id="B", timestamp="2026-03-09T12:00:00Z")

        bridge._handle_navigate_command.assert_called_once()
