"""Tests for ErrorMapper — state flags, transport errors, get_error_reason."""
import pytest
from services.error_mapper import ErrorMapper


class TestMapStateFlagsToError:
    def test_none_flags(self):
        assert ErrorMapper.map_state_flags_to_error(None) is None

    def test_empty_flags(self):
        assert ErrorMapper.map_state_flags_to_error({}) is None

    def test_emergency_stop(self):
        assert ErrorMapper.map_state_flags_to_error({"emergency_stop": True}) == "estop"

    def test_emergency_stop_reset_request(self):
        assert ErrorMapper.map_state_flags_to_error({"emergency_stop_reset_request": True}) == "estop"

    def test_odom_timeout(self):
        assert ErrorMapper.map_state_flags_to_error({"odom_timeout": True}) == "navigation_timeout"

    def test_laser_timeout(self):
        assert ErrorMapper.map_state_flags_to_error({"laser_timeout": True}) == "scanner_blocked"

    def test_waiting_for_scanner(self):
        assert ErrorMapper.map_state_flags_to_error({"waiting_for_scanner": True}) == "scanner_blocked"

    def test_robot_paused(self):
        assert ErrorMapper.map_state_flags_to_error({"robot_paused": True}) == "robot_paused"

    def test_sfuse_blown(self):
        assert ErrorMapper.map_state_flags_to_error({"sfuse_blown": True}) == "fuse_blown"

    def test_priority_estop_over_fuse(self):
        """emergency_stop should be checked before sfuse_blown."""
        result = ErrorMapper.map_state_flags_to_error({
            "emergency_stop": True,
            "sfuse_blown": True,
        })
        assert result == "estop"


class TestMapTransportError:
    def test_no_state_log_key(self):
        """No state_log key at all → returns None."""
        assert ErrorMapper.map_transport_error({"state": 7}) is None

    def test_empty_state_log(self):
        """state_log=[] → early return None (doesn't reach state==7 check)."""
        assert ErrorMapper.map_transport_error({"state": 7, "state_log": []}) is None

    def test_state_log_all_level_none_error_state(self):
        """All entries have level=None, state==7 → transport_error."""
        data = {"state": 7, "state_log": [{"level": None, "status_code": 0, "status_detail": 0}]}
        assert ErrorMapper.map_transport_error(data) == "transport_error"

    def test_state_log_all_level_none_non_error_state(self):
        data = {"state": 5, "state_log": [{"level": None, "status_code": 0, "status_detail": 0}]}
        assert ErrorMapper.map_transport_error(data) is None

    def test_known_error_code(self):
        data = {"state_log": [{"level": 1, "status_code": 1000, "status_detail": 0}]}
        assert ErrorMapper.map_transport_error(data) == "estop"

    def test_unknown_error_code(self):
        data = {"state_log": [{"level": 1, "status_code": 9999, "status_detail": 5}]}
        assert ErrorMapper.map_transport_error(data) == "transport_error"

    def test_state_flags_takes_priority(self):
        data = {"state_log": [{"level": 1, "status_code": 2000, "status_detail": 0}]}
        flags = {"emergency_stop": True}
        assert ErrorMapper.map_transport_error(data, flags) == "estop"

    def test_log_entry_without_level_skipped(self):
        data = {"state_log": [
            {"level": None, "status_code": 1000, "status_detail": 0},
            {"level": 1, "status_code": 3001, "status_detail": 0},
        ]}
        assert ErrorMapper.map_transport_error(data) == "scanner_blocked"


class TestGetErrorReason:
    def test_with_transport_data(self):
        data = {"state_log": [{"level": 1, "status_code": 4000, "status_detail": 0}]}
        assert ErrorMapper.get_error_reason(transport_data=data) == "fuse_blown"

    def test_with_state_flags_only(self):
        assert ErrorMapper.get_error_reason(state_flags={"robot_paused": True}) == "robot_paused"

    def test_with_default(self):
        assert ErrorMapper.get_error_reason() == "unknown_error"

    def test_custom_default(self):
        assert ErrorMapper.get_error_reason(default_reason="custom") == "custom"

    def test_transport_takes_priority(self):
        data = {"state_log": [{"level": 1, "status_code": 2000, "status_detail": 0}]}
        flags = {"sfuse_blown": True}
        # transport_error calls map_transport_error which checks state_flags first
        result = ErrorMapper.get_error_reason(transport_data=data, state_flags=flags)
        assert result == "fuse_blown"
