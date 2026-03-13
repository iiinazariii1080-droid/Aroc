"""Integration tests for GET /safety/state endpoint."""
import pytest
from unittest.mock import AsyncMock, patch


class TestSafetyStateEndpoint:
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
        with patch("routes.symovo_agv.state_store") as ss:
            ss.get_last_raw_status = AsyncMock(return_value=raw)
            resp = test_client.get("/safety/state")

        assert resp.status_code == 200
        body = resp.json()
        # reason / since_ts are excluded when None (response_model_exclude_none)
        for key in ("safety_lockout", "state_flags", "ts", "recovery_available"):
            assert key in body

    def test_estop_returns_lockout(self, test_client):
        """E-Stop active → safety_lockout=True, reason='estop'."""
        raw = {
            "state_flags": {
                "emergency_stop": True,
                "safety_relais_closed_state": False,
                "sfuse_blown": False,
            },
        }
        with patch("routes.symovo_agv.state_store") as ss:
            ss.get_last_raw_status = AsyncMock(return_value=raw)
            resp = test_client.get("/safety/state")

        assert resp.status_code == 200
        body = resp.json()
        assert body["safety_lockout"] is True
        assert body["reason"] == "estop"

    def test_normal_state_not_locked(self, test_client):
        """Normal flags → safety_lockout=False."""
        raw = {
            "state_flags": {
                "emergency_stop": False,
                "safety_relais_closed_state": True,
                "sfuse_blown": False,
            },
        }
        with patch("routes.symovo_agv.state_store") as ss:
            ss.get_last_raw_status = AsyncMock(return_value=raw)
            resp = test_client.get("/safety/state")

        assert resp.status_code == 200
        body = resp.json()
        assert body["safety_lockout"] is False
        # reason is None → excluded by response_model_exclude_none
        assert "reason" not in body or body["reason"] is None

    def test_no_data_returns_lockout(self, test_client):
        """No raw status available → fail-closed: safety_lockout=True."""
        with patch("routes.symovo_agv.state_store") as ss:
            ss.get_last_raw_status = AsyncMock(return_value=None)
            resp = test_client.get("/safety/state")

        assert resp.status_code == 200
        body = resp.json()
        assert body["safety_lockout"] is True
        assert body["reason"] == "no_data"
