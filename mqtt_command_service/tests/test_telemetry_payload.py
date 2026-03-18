"""Unit tests for telemetry_payload.py — pure-function payload builders."""

import math
from unittest.mock import patch

import pytest

from telemetry_payload import (
    XARM_DATA_COORDS_INDEX,
    XARM_DATA_JOINTS_INDEX,
    _map_symovo_state_to_navigation_status,
    _parse_igus_lift_position,
    _parse_igus_status,
    _parse_omega,
    _parse_robot_arm_details,
    _parse_robot_components,
    _parse_validated_pose,
    _parse_xarm_position_and_joints,
    _parse_xarm_status,
    _require_float,
    _safe_float,
    _safe_int_or_none,
    build_connection_status_payload,
    build_navigation_status_payload,
    build_status_payload,
    build_system_status_payload,
    build_telemetry_payload,
    collect_host_metrics,
    validate_robot_response,
    warmup_psutil,
)

# ------------------------------------------------------------------
# _safe_float
# ------------------------------------------------------------------


class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float(3.14) == 3.14

    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_string_number(self):
        assert _safe_float("1.5") == 1.5

    def test_none_returns_default(self):
        assert _safe_float(None) == 0.0

    def test_none_custom_default(self):
        assert _safe_float(None, -1.0) == -1.0

    def test_invalid_string(self):
        assert _safe_float("N/A") == 0.0

    def test_dict_returns_default(self):
        assert _safe_float({}) == 0.0


# ------------------------------------------------------------------
# _require_float
# ------------------------------------------------------------------


class TestRequireFloat:
    def test_normal_float(self):
        assert _require_float(3.14, "field") == 3.14

    def test_int(self):
        assert _require_float(42, "field") == 42.0

    def test_string_number(self):
        assert _require_float("1.5", "field") == 1.5

    def test_none_returns_none(self):
        assert _require_float(None, "field") is None

    def test_invalid_string_returns_none(self):
        assert _require_float("N/A", "field") is None

    def test_dict_returns_none(self):
        assert _require_float({}, "field") is None

    def test_zero_is_valid(self):
        assert _require_float(0, "field") == 0.0

    def test_zero_string_is_valid(self):
        assert _require_float("0", "field") == 0.0


# ------------------------------------------------------------------
# _map_symovo_state_to_navigation_status
# ------------------------------------------------------------------


class TestMapSymovoState:
    @pytest.mark.parametrize(
        "state,expected",
        [
            ("idle", "idle"),
            ("navigating", "navigating"),
            ("arrived", "arrived"),
            ("error", "error"),
            ("paused", "idle"),
            ("localization", "idle"),
        ],
    )
    def test_known_states(self, state, expected):
        assert _map_symovo_state_to_navigation_status(state) == expected

    def test_unknown_state_returns_idle(self):
        assert _map_symovo_state_to_navigation_status("unknown_state") == "idle"

    def test_case_insensitive(self):
        assert _map_symovo_state_to_navigation_status("NAVIGATING") == "navigating"
        assert _map_symovo_state_to_navigation_status("Error") == "error"


# ------------------------------------------------------------------
# _parse_igus_lift_position
# ------------------------------------------------------------------


class TestParseIgusLiftPosition:
    def test_with_position_cm(self):
        result = _parse_igus_lift_position({"position_cm": 42.5})
        assert result == {"height": 42.5}

    def test_with_position_fallback(self):
        result = _parse_igus_lift_position({"position": 10})
        assert result == {"height": 10.0}

    def test_position_cm_takes_priority(self):
        result = _parse_igus_lift_position({"position_cm": 5, "position": 99})
        assert result == {"height": 5.0}

    def test_no_position_returns_none(self):
        assert _parse_igus_lift_position({}) is None

    def test_string_position_returns_none(self):
        assert _parse_igus_lift_position({"position_cm": "N/A"}) is None


# ------------------------------------------------------------------
# _parse_igus_status
# ------------------------------------------------------------------


class TestParseIgusStatus:
    def test_full_data(self):
        data = {
            "connected": True,
            "homed": True,
            "is_moving": False,
            "error": False,
            "position_cm": 25.3,
        }
        result = _parse_igus_status(data)
        assert result == {
            "connected": True,
            "homed": True,
            "is_moving": False,
            "error": False,
            "position_cm": 25.3,
        }

    def test_empty_data_uses_defaults(self):
        result = _parse_igus_status({})
        assert result["connected"] is False
        assert result["position_cm"] is None

    def test_position_cm_none_preserved(self):
        result = _parse_igus_status({"position_cm": None})
        assert result["position_cm"] is None


# ------------------------------------------------------------------
# _parse_xarm_position_and_joints
# ------------------------------------------------------------------


class TestParseXarmPositionAndJoints:
    def _make_data_array(self, joints=None, coords=None):
        """Build a data array with the right length for XARM indices."""
        arr = [0] * (XARM_DATA_COORDS_INDEX + 1)
        if joints is not None:
            arr[XARM_DATA_JOINTS_INDEX] = joints
        if coords is not None:
            arr[XARM_DATA_COORDS_INDEX] = coords
        return arr

    def test_full_data(self):
        coords = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        joints = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        xarm = {"data": self._make_data_array(joints=joints, coords=coords)}
        pos, jnt = _parse_xarm_position_and_joints(xarm)
        assert pos == {"x": 1.0, "y": 2.0, "z": 3.0}
        assert jnt == {
            "j1": 10.0,
            "j2": 20.0,
            "j3": 30.0,
            "j4": 40.0,
            "j5": 50.0,
            "j6": 60.0,
        }

    def test_short_array_returns_none(self):
        pos, jnt = _parse_xarm_position_and_joints({"data": [1, 2, 3]})
        assert pos is None
        assert jnt is None

    def test_no_data_key(self):
        pos, jnt = _parse_xarm_position_and_joints({})
        assert pos is None
        assert jnt is None

    def test_coords_too_short(self):
        arr = self._make_data_array(coords=[1.0, 2.0])  # only 2
        pos, _jnt = _parse_xarm_position_and_joints({"data": arr})
        assert pos is None  # needs >= 3

    def test_joints_too_short(self):
        coords = [1.0, 2.0, 3.0]
        arr = self._make_data_array(coords=coords, joints=[10.0])
        pos, jnt = _parse_xarm_position_and_joints({"data": arr})
        assert pos == {"x": 1.0, "y": 2.0, "z": 3.0}
        assert jnt is None  # needs >= 6


# ------------------------------------------------------------------
# _parse_xarm_status
# ------------------------------------------------------------------


class TestParseXarmStatus:
    def test_full(self):
        result = _parse_xarm_status(
            {
                "connected": True,
                "has_error": False,
                "has_warn": True,
                "state_code": 2,
            }
        )
        assert result == {
            "connected": True,
            "has_error": False,
            "has_warn": True,
            "state_code": 2,
        }

    def test_empty(self):
        result = _parse_xarm_status({})
        assert result["connected"] is False
        assert result["state_code"] is None


# ------------------------------------------------------------------
# _parse_robot_components
# ------------------------------------------------------------------


class TestParseRobotComponents:
    def test_both_present(self):
        robot = {
            "igus": {"connected": True, "position_cm": 10},
            "xarm": {"connected": True, "has_error": False, "has_warn": False, "state_code": 0},
        }
        igus, xarm = _parse_robot_components(robot)
        assert igus is not None
        assert igus["connected"] is True
        assert xarm is not None
        assert xarm["connected"] is True

    def test_neither_present(self):
        igus, xarm = _parse_robot_components({})
        assert igus is None
        assert xarm is None

    def test_non_dict_values_ignored(self):
        igus, xarm = _parse_robot_components({"igus": "bad", "xarm": 123})
        assert igus is None
        assert xarm is None


# ------------------------------------------------------------------
# _parse_robot_arm_details
# ------------------------------------------------------------------


class TestParseRobotArmDetails:
    def test_with_igus(self):
        robot = {"igus": {"position_cm": 50}}
        lift, xpos, xjoints = _parse_robot_arm_details(robot)
        assert lift == {"height": 50.0}
        assert xpos is None
        assert xjoints is None

    def test_empty(self):
        lift, xpos, xjoints = _parse_robot_arm_details({})
        assert lift is None
        assert xpos is None
        assert xjoints is None


# ------------------------------------------------------------------
# build_navigation_status_payload
# ------------------------------------------------------------------


class TestBuildNavigationStatusPayload:
    def _make_symovo(self, **overrides):
        base = {
            "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 90.0},
            "state": "navigating",
        }
        base.update(overrides)
        return base

    def test_basic_payload(self):
        robot = {}
        symovo = self._make_symovo()
        result = build_navigation_status_payload("r1", robot, symovo)
        assert result is not None
        assert result["v"] == 1
        assert result["robot_id"] == "r1"
        assert result["status"] == "navigating"
        assert result["current_position"]["x"] == 1.0
        assert result["current_position"]["y"] == 2.0
        assert abs(result["current_position"]["theta"] - math.radians(90)) < 1e-9

    def test_no_pose_returns_none(self):
        result = build_navigation_status_payload("r1", {}, {"state": "idle"})
        assert result is None

    def test_with_igus_data(self):
        robot = {"igus": {"position_cm": 50}}
        symovo = self._make_symovo()
        result = build_navigation_status_payload("r1", robot, symovo)
        assert result is not None
        assert result["lift_position"] == {"height": 50.0}

    def test_target_id_from_robot(self):
        robot = {"target_id": "target-1"}
        symovo = self._make_symovo()
        result = build_navigation_status_payload("r1", robot, symovo)
        assert result["target_id"] == "target-1"

    def test_target_id_fallback_to_symovo(self):
        robot = {}
        symovo = self._make_symovo(target_id="target-2")
        result = build_navigation_status_payload("r1", robot, symovo)
        assert result["target_id"] == "target-2"

    def test_none_pose_values_returns_none(self):
        """None pose values cause payload to be skipped entirely."""
        symovo = {"pose": {"x_m": None, "y_m": None, "theta_deg": None}, "state": "idle"}
        result = build_navigation_status_payload("r1", {}, symovo)
        assert result is None


# ------------------------------------------------------------------
# build_system_status_payload
# ------------------------------------------------------------------


class TestBuildSystemStatusPayload:
    def test_returns_none_without_battery(self):
        result = build_system_status_payload("r1", {})
        assert result is None

    def test_basic_without_host_metrics(self):
        symovo = {"battery_level_percent": 80}
        result = build_system_status_payload("r1", symovo)
        assert result is not None
        assert result["v"] == 1
        assert result["battery"] == 80
        assert "cpu" not in result
        assert "host_metrics" not in result  # no host_metrics passed → omitted

    def test_battery_none_returns_none(self):
        result = build_system_status_payload("r1", {"battery_level_percent": None})
        assert result is None

    def test_battery_invalid_string_returns_none(self):
        """Invalid battery value causes payload to be skipped."""
        result = build_system_status_payload("r1", {"battery_level_percent": "bad"})
        assert result is None

    def test_host_metrics_separated(self):
        """Host metrics passed as parameter appear under 'host_metrics' key."""
        symovo = {"battery_level_percent": 80}
        host_metrics = {
            "cpu": 50.0,
            "ram": 60.0,
            "disk": 70.0,
            "uptime_seconds": 12345.7,
        }
        result = build_system_status_payload("r1", symovo, host_metrics=host_metrics)
        assert result is not None
        assert result["battery"] == 80
        assert "host_metrics" in result
        assert result["host_metrics"]["cpu"] == 50.0
        assert result["host_metrics"]["ram"] == 60.0
        assert result["host_metrics"]["disk"] == 70.0
        assert result["host_metrics"]["uptime_seconds"] == 12345.7

    def test_host_metrics_none_values_omitted(self):
        """None values in host_metrics dict are omitted from output."""
        symovo = {"battery_level_percent": 80}
        host_metrics = {"cpu": 50.0, "ram": None, "disk": None, "uptime_seconds": None}
        result = build_system_status_payload("r1", symovo, host_metrics=host_metrics)
        assert result is not None
        assert result["host_metrics"] == {"cpu": 50.0}


# ------------------------------------------------------------------
# build_telemetry_payload
# ------------------------------------------------------------------


class TestBuildTelemetryPayload:
    def _make_data(self):
        return (
            {},
            {
                "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 45.0, "map_id": 3},
                "velocity": {"vx_m_s": 0.5, "vy_m_s": 0.1, "omega_rad_s": 0.2},
                "battery_level_percent": 75,
                "state": "navigating",
                "state_flags": {"estop": False},
            },
        )

    def test_basic(self):
        robot, symovo = self._make_data()
        result = build_telemetry_payload("r1", robot, symovo)
        assert result is not None
        assert result["v"] == 1
        assert result["robot_id"] == "r1"
        data = result["data"]
        assert data["pose"]["x"] == 1.0
        assert data["pose"]["map_id"] == 3
        assert abs(data["pose"]["theta"] - math.radians(45)) < 1e-9
        assert data["velocity"]["vx"] == 0.5
        assert data["velocity"]["omega"] == 0.2
        assert data["battery_percent"] == 75.0
        assert data["state"] == "navigating"

    def test_omega_deg_fallback(self):
        robot = {}
        symovo = {
            "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
            "velocity": {"omega_deg_s": 90.0},
            "battery_level_percent": 50,
            "state": "idle",
        }
        result = build_telemetry_payload("r1", robot, symovo)
        assert result is not None
        assert abs(result["data"]["velocity"]["omega"] - math.radians(90)) < 1e-9

    def test_with_igus_and_xarm_components(self):
        robot = {
            "igus": {"connected": True, "position_cm": 10},
            "xarm": {"connected": True, "has_error": False, "has_warn": False, "state_code": 0},
        }
        symovo = {
            "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
            "velocity": {},
            "battery_level_percent": 90,
            "state": "idle",
        }
        result = build_telemetry_payload("r1", robot, symovo)
        assert result is not None
        assert "components" in result
        assert result["components"]["igus"]["connected"] is True
        assert result["components"]["xarm"]["connected"] is True

    def test_none_pose_values_returns_none(self):
        """None pose values cause payload to be skipped entirely."""
        symovo = {
            "pose": {"x_m": None, "y_m": None, "theta_deg": None, "map_id": None},
            "velocity": {"vx_m_s": None},
            "battery_level_percent": None,
            "state": "idle",
        }
        result = build_telemetry_payload("r1", {}, symovo)
        assert result is None

    def test_valid_pose_with_none_velocity(self):
        """Valid pose but None velocity fields — payload still produced."""
        symovo = {
            "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
            "velocity": {"vx_m_s": None},
            "battery_level_percent": 50,
            "state": "idle",
        }
        result = build_telemetry_payload("r1", {}, symovo)
        assert result is not None
        assert result["data"]["velocity"]["vx"] is None

    def test_map_id_int_preserved(self):
        symovo = {
            "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0, "map_id": 5},
            "velocity": {},
            "battery_level_percent": 50,
            "state": "idle",
        }
        result = build_telemetry_payload("r1", {}, symovo)
        assert result["data"]["pose"]["map_id"] == 5

    def test_map_id_missing_is_none(self):
        symovo = {
            "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
            "velocity": {},
            "battery_level_percent": 50,
            "state": "idle",
        }
        result = build_telemetry_payload("r1", {}, symovo)
        assert result["data"]["pose"]["map_id"] is None

    def test_empty_pose_returns_none(self):
        """Empty pose dict → all critical fields are None → skip."""
        symovo = {
            "pose": {},
            "velocity": {},
            "battery_level_percent": 50,
            "state": "idle",
        }
        result = build_telemetry_payload("r1", {}, symovo)
        assert result is None


# ------------------------------------------------------------------
# build_status_payload
# ------------------------------------------------------------------


class TestBuildStatusPayload:
    def test_structure(self):
        result = build_status_payload("r1", "robot", "online", {"key": "val"}, None)
        assert result["v"] == 1
        assert result["robot_id"] == "r1"
        assert result["service"] == "robot"
        assert result["status"] == "online"
        assert result["data"] == {"key": "val"}
        assert result["error"] is None

    def test_with_error(self):
        result = build_status_payload("r1", "robot", "error", None, "timeout")
        assert result["error"] == "timeout"
        assert result["data"] is None

    def test_sensitive_keys_redacted(self):
        """Sensitive keys in upstream data are stripped before publishing."""
        data = {
            "state": "idle",
            "password": "secret123",
            "token": "jwt-tok",
            "nested": {"api_key": "key123", "value": 42},
        }
        result = build_status_payload("r1", "robot", "online", data, None)
        assert "password" not in result["data"]
        assert "token" not in result["data"]
        assert result["data"]["state"] == "idle"
        assert "api_key" not in result["data"]["nested"]
        assert result["data"]["nested"]["value"] == 42

    def test_non_dict_data_passthrough(self):
        """Non-dict data passes through sanitization unchanged."""
        result = build_status_payload("r1", "robot", "online", "raw-string", None)
        assert result["data"] == "raw-string"

    def test_none_data_passthrough(self):
        """None data passes through sanitization unchanged."""
        result = build_status_payload("r1", "robot", "offline", None, "err")
        assert result["data"] is None


# ------------------------------------------------------------------
# build_connection_status_payload
# ------------------------------------------------------------------


class TestBuildConnectionStatusPayload:
    def test_both_true(self):
        result = build_connection_status_payload("r1", {"depth": True, "color": True}, True)
        assert result["v"] == 1
        assert result["janus_ws"] == {"depth": True, "color": True}
        assert result["mqtt"] is True

    def test_partial_connectivity(self):
        result = build_connection_status_payload("r1", {"depth": True, "color": False}, True)
        assert result["janus_ws"]["depth"] is True
        assert result["janus_ws"]["color"] is False

    def test_both_false(self):
        result = build_connection_status_payload("r1", {"depth": False, "color": False}, False)
        assert result["janus_ws"]["depth"] is False
        assert result["mqtt"] is False


# ------------------------------------------------------------------
# warmup_psutil
# ------------------------------------------------------------------


class TestWarmupPsutil:
    def test_logs_warning_on_failure(self, caplog):
        """warmup failure is logged, not silently swallowed."""
        with patch("telemetry_payload.psutil") as mock_psutil, caplog.at_level("WARNING"):
            mock_psutil.cpu_percent.side_effect = RuntimeError("boom")
            warmup_psutil()
        assert "psutil warmup failed" in caplog.text


# ------------------------------------------------------------------
# _safe_int_or_none
# ------------------------------------------------------------------


class TestSafeIntOrNone:
    def test_int(self):
        assert _safe_int_or_none(5) == 5

    def test_float_truncated(self):
        assert _safe_int_or_none(3.9) == 3

    def test_none(self):
        assert _safe_int_or_none(None) is None

    def test_bool_true_is_none(self):
        assert _safe_int_or_none(True) is None

    def test_bool_false_is_none(self):
        assert _safe_int_or_none(False) is None

    def test_string_is_none(self):
        assert _safe_int_or_none("5") is None

    def test_dict_is_none(self):
        assert _safe_int_or_none({}) is None

    def test_zero(self):
        assert _safe_int_or_none(0) == 0


# ------------------------------------------------------------------
# _parse_omega
# ------------------------------------------------------------------


class TestParseOmega:
    def test_omega_rad_s_preferred(self):
        v = {"omega_rad_s": 1.5, "omega_deg_s": 90.0}
        assert _parse_omega(v) == 1.5

    def test_omega_deg_s_fallback(self):
        v = {"omega_deg_s": 90.0}
        assert abs(_parse_omega(v) - math.radians(90.0)) < 1e-9

    def test_neither_returns_none(self):
        """No omega key → None, not 0.0."""
        assert _parse_omega({}) is None

    def test_none_omega_rad_s(self):
        """Key present but value is None → returns None."""
        v = {"omega_rad_s": None}
        assert _parse_omega(v) is None


# ------------------------------------------------------------------
# validate_robot_response
# ------------------------------------------------------------------


class TestValidateRobotResponse:
    def test_valid_full_response(self):
        data = {
            "symovo": {
                "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
                "state": "idle",
                "velocity": {},
                "battery_level_percent": 80,
            },
        }
        assert validate_robot_response(data) == []

    def test_missing_symovo(self):
        warnings = validate_robot_response({})
        assert any("symovo" in w for w in warnings)

    def test_symovo_not_dict(self):
        warnings = validate_robot_response({"symovo": "bad"})
        assert any("expected dict" in w for w in warnings)

    def test_missing_symovo_keys(self):
        warnings = validate_robot_response({"symovo": {"state": "idle"}})
        assert any("missing keys" in w for w in warnings)

    def test_missing_pose_keys(self):
        data = {
            "symovo": {
                "pose": {"x_m": 1.0},  # missing y_m, theta_deg
                "state": "idle",
                "velocity": {},
                "battery_level_percent": 80,
            },
        }
        warnings = validate_robot_response(data)
        assert any("pose missing keys" in w for w in warnings)

    def test_igus_wrong_type(self):
        data = {
            "symovo": {
                "pose": {"x_m": 0, "y_m": 0, "theta_deg": 0},
                "state": "idle",
                "velocity": {},
                "battery_level_percent": 80,
            },
            "igus": "bad",
        }
        warnings = validate_robot_response(data)
        assert any("igus" in w for w in warnings)

    def test_not_a_dict(self):
        warnings = validate_robot_response([1, 2, 3])
        assert any("expected dict" in w for w in warnings)

    def test_optional_components_absent_is_fine(self):
        data = {
            "symovo": {
                "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
                "state": "idle",
                "velocity": {},
                "battery_level_percent": 80,
            },
        }
        # No igus, no xarm — should be clean
        assert validate_robot_response(data) == []


# ------------------------------------------------------------------
# _parse_validated_pose
# ------------------------------------------------------------------


class TestParseValidatedPose:
    def test_valid_pose(self):
        symovo = {"pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 90.0}}
        result = _parse_validated_pose(symovo, "test")
        assert result is not None
        x, y, theta_rad = result
        assert x == 1.0
        assert y == 2.0
        assert abs(theta_rad - math.radians(90.0)) < 1e-9

    def test_missing_pose_returns_none(self):
        assert _parse_validated_pose({"state": "idle"}, "test") is None

    def test_pose_not_dict_returns_none(self):
        assert _parse_validated_pose({"pose": "bad"}, "test") is None

    def test_none_fields_returns_none(self):
        symovo = {"pose": {"x_m": None, "y_m": 2.0, "theta_deg": 0.0}}
        assert _parse_validated_pose(symovo, "test") is None

    def test_empty_pose_returns_none(self):
        assert _parse_validated_pose({"pose": {}}, "test") is None


# ------------------------------------------------------------------
# collect_host_metrics
# ------------------------------------------------------------------


class TestCollectHostMetrics:
    def test_returns_metrics_with_psutil(self):
        with patch("telemetry_payload.psutil") as mock_psutil:
            mock_psutil.cpu_percent.return_value = 50.0
            mock_psutil.virtual_memory.return_value = type("VM", (), {"percent": 60.0})()
            mock_psutil.disk_usage.return_value = type("DU", (), {"percent": 70.0})()
            mock_psutil.boot_time.return_value = 0.0
            result = collect_host_metrics()
        assert result is not None
        assert result["cpu"] == 50.0
        assert result["ram"] == 60.0
        assert result["disk"] == 70.0
        assert result["uptime_seconds"] is not None

    def test_returns_none_when_all_metrics_fail(self):
        """When psutil is completely unavailable, returns None."""
        with patch("telemetry_payload.psutil") as mock_psutil:
            mock_psutil.cpu_percent.side_effect = RuntimeError("no psutil")
            mock_psutil.virtual_memory.side_effect = RuntimeError("no psutil")
            mock_psutil.disk_usage.side_effect = RuntimeError("no psutil")
            mock_psutil.boot_time.side_effect = RuntimeError("no psutil")
            result = collect_host_metrics()
        assert result is None

    def test_partial_failure_disk_returns_dict(self):
        """When disk fails but CPU/RAM succeed, returns dict with disk=None."""
        with patch("telemetry_payload.psutil") as mock_psutil:
            mock_psutil.cpu_percent.return_value = 45.0
            mock_psutil.virtual_memory.return_value = type("VM", (), {"percent": 55.0})()
            mock_psutil.disk_usage.side_effect = PermissionError("no access")
            mock_psutil.boot_time.return_value = 0.0
            result = collect_host_metrics()
        assert result is not None
        assert result["cpu"] == 45.0
        assert result["ram"] == 55.0
        assert result["disk"] is None
        assert result["uptime_seconds"] is not None

    def test_cpu_failure_does_not_block_other_metrics(self):
        """CPU failure is independent — disk/uptime still collected."""
        with patch("telemetry_payload.psutil") as mock_psutil:
            mock_psutil.cpu_percent.side_effect = RuntimeError("no cpu")
            mock_psutil.virtual_memory.return_value = type("VM", (), {"percent": 55.0})()
            mock_psutil.disk_usage.return_value = type("DU", (), {"percent": 70.0})()
            mock_psutil.boot_time.return_value = 0.0
            result = collect_host_metrics()
        assert result is not None
        assert result["cpu"] is None
        assert result["ram"] == 55.0
        assert result["disk"] == 70.0

    def test_uptime_rounded(self):
        """uptime_seconds is rounded to 1 decimal in collect_host_metrics."""
        with patch("telemetry_payload.psutil") as mock_psutil, patch("telemetry_payload.time") as mock_time:
            mock_psutil.cpu_percent.return_value = 10.0
            mock_psutil.virtual_memory.return_value = type("VM", (), {"percent": 20.0})()
            mock_psutil.disk_usage.return_value = type("DU", (), {"percent": 30.0})()
            mock_psutil.boot_time.return_value = 1000.0
            mock_time.time.return_value = 13345.6789
            result = collect_host_metrics()
        assert result is not None
        assert result["uptime_seconds"] == 12345.7
