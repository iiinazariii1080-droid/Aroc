"""Tests for SafetyGate: fail-closed safety state management."""

import json
import time
from unittest.mock import MagicMock

from safety_gate import SafetyGate


def _make_mqtt_message(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.payload = json.dumps(payload).encode("utf-8")
    return msg


class TestFailClosed:
    def test_locked_before_any_message(self):
        gate = SafetyGate(startup_grace_seconds=0.0, heartbeat_timeout=60.0)
        assert gate.is_locked() is True

    def test_startup_grace_seconds_allows_commands(self):
        gate = SafetyGate(startup_grace_seconds=10.0, heartbeat_timeout=60.0)
        assert gate.is_locked() is False

    def test_locked_after_grace_period_expires(self):
        gate = SafetyGate(startup_grace_seconds=0.01, heartbeat_timeout=60.0)
        time.sleep(0.05)
        assert gate.is_locked() is True


class TestHandleSafetyMessage:
    def test_ok_message_unlocks(self):
        gate = SafetyGate(heartbeat_timeout=60.0)
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": False}))
        assert gate.is_locked() is False

    def test_lockout_message_locks(self):
        gate = SafetyGate(heartbeat_timeout=60.0)
        gate.handle_safety_message(
            _make_mqtt_message(
                {
                    "safety_lockout": True,
                    "reason": "collision detected",
                }
            )
        )
        assert gate.is_locked() is True

    def test_non_dict_payload_ignored(self):
        gate = SafetyGate(heartbeat_timeout=60.0)
        msg = MagicMock()
        msg.payload = b'"just a string"'
        gate.handle_safety_message(msg)
        assert gate.is_locked() is True  # still no valid state

    def test_invalid_json_does_not_crash(self):
        gate = SafetyGate(heartbeat_timeout=60.0)
        msg = MagicMock()
        msg.payload = b"not json at all"
        gate.handle_safety_message(msg)  # should not raise
        assert gate.is_locked() is True


class TestHeartbeatTimeout:
    def test_stale_heartbeat_causes_lockout(self):
        gate = SafetyGate(heartbeat_timeout=0.05)
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": False}))
        assert gate.is_locked() is False
        time.sleep(0.1)
        assert gate.is_locked() is True

    def test_fresh_heartbeat_not_locked(self):
        gate = SafetyGate(heartbeat_timeout=60.0)
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": False}))
        assert gate.is_locked() is False


class TestState:
    def test_state_none_before_message(self):
        gate = SafetyGate()
        assert gate.state is None

    def test_state_returns_cached_payload(self):
        gate = SafetyGate()
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": False, "reason": ""}))
        assert gate.state == {"safety_lockout": False, "reason": ""}

    def test_state_timestamp_initially_zero(self):
        gate = SafetyGate()
        assert gate.state_timestamp == 0.0

    def test_state_timestamp_updated_after_message(self):
        gate = SafetyGate()
        before = time.time()
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": False}))
        after = time.time()
        assert before <= gate.state_timestamp <= after


class TestTransitions:
    def test_unlock_then_lock_via_lockout(self):
        gate = SafetyGate(heartbeat_timeout=60.0)
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": False}))
        assert gate.is_locked() is False
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": True, "reason": "e-stop"}))
        assert gate.is_locked() is True

    def test_lock_then_unlock(self):
        gate = SafetyGate(heartbeat_timeout=60.0)
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": True, "reason": "e-stop"}))
        assert gate.is_locked() is True
        gate.handle_safety_message(_make_mqtt_message({"safety_lockout": False}))
        assert gate.is_locked() is False
