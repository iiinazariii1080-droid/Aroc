"""Unit tests for ReadinessChecker."""

import pytest
from services.readiness_checker import ReadinessChecker
from domain.models import RobotReadiness


def _make_flags(**overrides) -> dict:
    """Build a state_flags dict that passes all checks by default."""
    defaults = {
        "drive_ready": True,
        "safety_cleared": True,
        "emergency_stop": False,
        "emergency_stop_reset_request": False,
        "sfuse_blown": False,
        "waiting_for_scanner": False,
        "laser_timeout": False,
        "odom_timeout": False,
        "drive_manual": False,
        "robot_paused": False,
        "charging_connector": False,
        "charging": False,
    }
    defaults.update(overrides)
    return defaults


class TestReadinessCheckerReady:
    def test_ready_when_drive_ready_and_safety_cleared(self):
        status = {"state_flags": _make_flags()}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is True
        assert result.error_detail is None

    def test_is_ready_shorthand(self):
        status = {"state_flags": _make_flags()}
        assert ReadinessChecker.is_ready(status) is True


class TestReadinessCheckerNotReady:
    def test_drive_not_ready(self):
        status = {"state_flags": _make_flags(drive_ready=False)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "drive" in result.error_detail

    def test_safety_not_cleared(self):
        status = {"state_flags": _make_flags(safety_cleared=False)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "safety" in result.error_detail

    def test_laser_timeout(self):
        status = {"state_flags": _make_flags(laser_timeout=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "laser" in result.error_detail

    def test_waiting_for_scanner(self):
        status = {"state_flags": _make_flags(waiting_for_scanner=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "scanner" in result.error_detail

    def test_emergency_stop(self):
        status = {"state_flags": _make_flags(emergency_stop=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "emergency" in result.error_detail

    def test_emergency_stop_reset_request(self):
        status = {"state_flags": _make_flags(emergency_stop_reset_request=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "emergency" in result.error_detail

    def test_fuse_blown(self):
        status = {"state_flags": _make_flags(sfuse_blown=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "fuse" in result.error_detail

    def test_odom_timeout(self):
        status = {"state_flags": _make_flags(odom_timeout=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "odom" in result.error_detail

    def test_drive_manual(self):
        status = {"state_flags": _make_flags(drive_manual=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "manual" in result.error_detail

    def test_robot_paused(self):
        status = {"state_flags": _make_flags(robot_paused=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "paused" in result.error_detail

    def test_charging(self):
        status = {"state_flags": _make_flags(charging=True)}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False
        assert "charging" in result.error_detail


class TestReadinessCheckerMissingFlags:
    def test_empty_state_flags(self):
        status = {"state_flags": {}}
        result = ReadinessChecker.check_readiness(status)
        assert result.ready is False

    def test_missing_state_flags_key(self):
        result = ReadinessChecker.check_readiness({})
        assert result.ready is False
        assert "missing_state_flags" in result.error_detail

    def test_state_flags_is_none(self):
        result = ReadinessChecker.check_readiness({"state_flags": None})
        assert result.ready is False
        assert "missing_state_flags" in result.error_detail

    def test_state_flags_is_string(self):
        result = ReadinessChecker.check_readiness({"state_flags": "bad"})
        assert result.ready is False
        assert "missing_state_flags" in result.error_detail

    def test_state_flags_is_list(self):
        result = ReadinessChecker.check_readiness({"state_flags": []})
        assert result.ready is False
        assert "missing_state_flags" in result.error_detail
