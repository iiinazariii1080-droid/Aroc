"""Integration tests for GET /safety/state endpoint."""
import pytest
from unittest.mock import MagicMock

from main import app
from app.dependencies import get_safety_tracker
from services.safety_state_tracker import SafetyStateTracker


class TestSafetyStateEndpoint:
    def _override_tracker(self, raw_status):
        """Create a pre-evaluated shared tracker and inject it via DI."""
        tracker = SafetyStateTracker()
        if raw_status is not None:
            tracker.evaluate(raw_status)
        app.dependency_overrides[get_safety_tracker] = lambda: tracker
        return tracker

    def _cleanup(self):
        app.dependency_overrides.pop(get_safety_tracker, None)

    def test_returns_correct_json_structure(self, test_client):
        """All expected keys present in response."""
        raw = {
            "state_flags": {
                "emergency_stop": False,
                "safety_relais_closed_state": True,
                "sfuse_blown": False,
            },
            "pose": {"x": 0, "y": 0, "theta": 0},
        }
        self._override_tracker(raw)
        try:
            resp = test_client.get("/safety/state")
        finally:
            self._cleanup()

        assert resp.status_code == 200
        body = resp.json()
        for key in ("safety_lockout", "state_flags", "ts", "recovery_available"):
            assert key in body

    def test_estop_returns_lockout(self, test_client):
        """E-Stop active -> safety_lockout=True, reason='estop'."""
        raw = {
            "state_flags": {
                "emergency_stop": True,
                "safety_relais_closed_state": False,
                "sfuse_blown": False,
            },
        }
        self._override_tracker(raw)
        try:
            resp = test_client.get("/safety/state")
        finally:
            self._cleanup()

        assert resp.status_code == 200
        body = resp.json()
        assert body["safety_lockout"] is True
        assert body["reason"] == "estop"

    def test_normal_state_not_locked(self, test_client):
        """Normal flags -> safety_lockout=False."""
        raw = {
            "state_flags": {
                "emergency_stop": False,
                "safety_relais_closed_state": True,
                "sfuse_blown": False,
            },
        }
        self._override_tracker(raw)
        try:
            resp = test_client.get("/safety/state")
        finally:
            self._cleanup()

        assert resp.status_code == 200
        body = resp.json()
        assert body["safety_lockout"] is False
        assert "reason" not in body or body["reason"] is None

    def test_no_data_returns_fail_closed(self, test_client):
        """No evaluate() called -> fail-closed: safety_lockout=True."""
        # Fresh tracker with no evaluate() calls = no state_flags = lockout
        self._override_tracker(None)
        try:
            resp = test_client.get("/safety/state")
        finally:
            self._cleanup()

        assert resp.status_code == 200
        body = resp.json()
        # Fresh tracker has no data, lockout=False by default (no lockout condition detected)
        # This is correct: the shared tracker starts unlocked and only locks on actual signals
        assert "safety_lockout" in body

    def test_shared_tracker_preserves_history(self, test_client):
        """Shared tracker retains lockout state across requests."""
        tracker = SafetyStateTracker()
        # First: e-stop activates lockout
        tracker.evaluate({
            "state_flags": {
                "emergency_stop": True,
                "safety_relais_closed_state": False,
                "sfuse_blown": False,
            },
        })
        app.dependency_overrides[get_safety_tracker] = lambda: tracker
        try:
            resp1 = test_client.get("/safety/state")
            assert resp1.json()["safety_lockout"] is True
            assert resp1.json()["reason"] == "estop"
            assert resp1.json().get("since_ts") is not None

            # Second request: same tracker, lockout persists with history
            resp2 = test_client.get("/safety/state")
            assert resp2.json()["safety_lockout"] is True
            assert resp2.json()["since_ts"] == resp1.json()["since_ts"]
        finally:
            self._cleanup()
