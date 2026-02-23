"""Tests for telemetry_payload – pure payload builder functions."""

import math
from unittest.mock import patch

import pytest

from telemetry_payload import (
    _extract_navigation_data,
    _extract_system_data,
    _map_symovo_state_to_navigation_status,
    build_connection_status_payload,
    build_navigation_status_payload,
    build_status_payload,
    build_system_status_payload,
    build_telemetry_payload,
    iso_timestamp,
)

# ---- helpers ------------------------------------------------------------

ROBOT_ID = "test-robot-01"

_FULL_ROBOT_DATA = {
    "igus": {
        "connected": True,
        "homed": True,
        "is_moving": False,
        "error": False,
        "position_cm": 42.5,
    },
    "xarm": {
        "connected": True,
        "has_error": False,
        "has_warn": False,
        "state_code": 2,
        "data": [None] * 18 + [
            [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],  # data[18] joints
            [100.0, 200.0, 300.0],                   # data[19] coords
        ],
    },
    "target_id": "nav-42",
}

_FULL_SYMOVO_DATA = {
    "pose": {"x_m": 1.5, "y_m": 2.5, "theta_deg": 90.0, "map_id": 3},
    "velocity": {"vx_m_s": 0.5, "vy_m_s": 0.1, "omega_rad_s": 0.2},
    "state": "navigating",
    "battery_level_percent": 78.5,
    "state_flags": {"obstacle": True},
}


# ---- iso_timestamp -------------------------------------------------------

class TestIsoTimestamp:
    def test_iso_format(self):
        ts = iso_timestamp()
        assert ts.endswith("Z") or "+00:00" in ts
        assert "T" in ts

    def test_returns_string(self):
        assert isinstance(iso_timestamp(), str)


# ---- _map_symovo_state_to_navigation_status ------------------------------

class TestMapSymovoState:
    @pytest.mark.parametrize("state,expected", [
        ("localization", "idle"),
        ("idle", "idle"),
        ("navigating", "navigating"),
        ("arrived", "arrived"),
        ("error", "error"),
        ("paused", "idle"),
        ("NAVIGATING", "navigating"),  # case-insensitive
        ("unknown_state", "idle"),     # fallback
    ])
    def test_mapping(self, state, expected):
        assert _map_symovo_state_to_navigation_status(state) == expected


# ---- _extract_navigation_data -------------------------------------------

class TestExtractNavigationData:
    def test_valid_data(self):
        result = _extract_navigation_data(_FULL_ROBOT_DATA, _FULL_SYMOVO_DATA)
        assert result is not None
        assert result["status"] == "navigating"
        assert result["target_id"] == "nav-42"
        pos = result["current_position"]
        assert pos["x"] == 1.5
        assert pos["y"] == 2.5
        assert abs(pos["theta"] - math.radians(90.0)) < 1e-9

    def test_none_symovo(self):
        assert _extract_navigation_data(_FULL_ROBOT_DATA, None) is None

    def test_empty_dict_symovo(self):
        assert _extract_navigation_data({}, {}) is None

    def test_symovo_no_pose(self):
        assert _extract_navigation_data({}, {"state": "idle"}) is None

    def test_target_id_from_symovo(self):
        """Falls back to symovo target_id when robot_data has none."""
        result = _extract_navigation_data(
            {},
            {**_FULL_SYMOVO_DATA, "target_id": "sym-99"},
        )
        assert result is not None
        assert result["target_id"] == "sym-99"


# ---- _extract_system_data -----------------------------------------------

class TestExtractSystemData:
    def test_valid_data(self):
        result = _extract_system_data(_FULL_SYMOVO_DATA)
        assert result is not None
        assert result["battery"] == 78  # int(round(78.5)) = 78

    def test_none_input(self):
        assert _extract_system_data(None) is None

    def test_no_battery(self):
        assert _extract_system_data({"state": "idle"}) is None

    @patch("telemetry_payload.PSUTIL_AVAILABLE", False)
    def test_no_psutil(self):
        result = _extract_system_data(_FULL_SYMOVO_DATA)
        assert result is not None
        assert result["cpu"] is None
        assert result["ram"] is None


# ---- build_navigation_status_payload ------------------------------------

class TestBuildNavigationStatusPayload:
    def test_full_payload(self):
        p = build_navigation_status_payload(ROBOT_ID, _FULL_ROBOT_DATA, _FULL_SYMOVO_DATA)
        assert p is not None
        assert p["robot_id"] == ROBOT_ID
        assert "timestamp" in p
        assert p["status"] == "navigating"
        assert p["target_id"] == "nav-42"
        assert "current_position" in p
        # Extra components
        assert p["lift_position"]["height"] == 42.5
        assert p["xarm_position"]["x"] == 100.0
        assert p["xarm_joints"]["j1"] == 10.0

    def test_returns_none_without_symovo(self):
        assert build_navigation_status_payload(ROBOT_ID, {}, None) is None

    def test_no_igus_no_xarm(self):
        p = build_navigation_status_payload(ROBOT_ID, {}, _FULL_SYMOVO_DATA)
        assert p is not None
        assert "lift_position" not in p
        assert "xarm_position" not in p
        assert "xarm_joints" not in p


# ---- build_system_status_payload ----------------------------------------

class TestBuildSystemStatusPayload:
    def test_full_payload(self):
        p = build_system_status_payload(ROBOT_ID, _FULL_SYMOVO_DATA)
        assert p is not None
        assert p["robot_id"] == ROBOT_ID
        assert p["battery"] == 78

    def test_no_battery(self):
        assert build_system_status_payload(ROBOT_ID, {"state": "idle"}) is None


# ---- build_telemetry_payload --------------------------------------------

class TestBuildTelemetryPayload:
    def test_full_payload(self):
        p = build_telemetry_payload(ROBOT_ID, _FULL_ROBOT_DATA, _FULL_SYMOVO_DATA)
        assert p is not None
        assert p["robot_id"] == ROBOT_ID
        data = p["data"]
        assert data["pose"]["x"] == 1.5
        assert data["velocity"]["vx"] == 0.5
        assert data["battery_percent"] == 78.5
        assert data["state"] == "navigating"
        assert data["state_flags"] == {"obstacle": True}
        # Components
        assert p["components"]["igus"]["connected"] is True
        assert p["components"]["xarm"]["state_code"] == 2

    def test_empty_symovo(self):
        assert build_telemetry_payload(ROBOT_ID, {}, None) is None
        assert build_telemetry_payload(ROBOT_ID, {}, {}) is None

    def test_no_components(self):
        p = build_telemetry_payload(ROBOT_ID, {}, _FULL_SYMOVO_DATA)
        assert p is not None
        assert "components" not in p

    def test_omega_deg_fallback(self):
        """Uses omega_deg_s when omega_rad_s missing."""
        symovo = {
            **_FULL_SYMOVO_DATA,
            "velocity": {"vx_m_s": 0, "vy_m_s": 0, "omega_deg_s": 180.0},
        }
        p = build_telemetry_payload(ROBOT_ID, {}, symovo)
        assert p is not None
        omega = p["data"]["velocity"]["omega"]
        assert abs(omega - math.pi) < 1e-9


# ---- build_status_payload -----------------------------------------------

class TestBuildStatusPayload:
    def test_structure(self):
        p = build_status_payload(ROBOT_ID, "robot", "online", {"ok": True}, None)
        assert p["robot_id"] == ROBOT_ID
        assert p["service"] == "robot"
        assert p["status"] == "online"
        assert p["data"] == {"ok": True}
        assert p["error"] is None
        assert "timestamp" in p

    def test_with_error(self):
        p = build_status_payload(ROBOT_ID, "igus", "error", None, "Connection refused")
        assert p["status"] == "error"
        assert p["error"] == "Connection refused"


# ---- build_connection_status_payload ------------------------------------

class TestBuildConnectionStatusPayload:
    def test_all_connected(self):
        p = build_connection_status_payload(ROBOT_ID, True, True)
        assert p["webrtc"] is True
        assert p["mqtt"] is True
        assert p["robot_id"] == ROBOT_ID

    def test_all_disconnected(self):
        p = build_connection_status_payload(ROBOT_ID, False, False)
        assert p["webrtc"] is False
        assert p["mqtt"] is False
