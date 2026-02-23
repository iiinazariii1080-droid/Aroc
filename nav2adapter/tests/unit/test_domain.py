"""Tests for navigation domain layer.

Covers: NavigationStateMachine (pure logic), domain models (Pydantic),
and event models (Pydantic).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from domain.state_machine import NavigationStateMachine as SM
from domain.models import (
    NavigationStatusEnum,
    NavigationCommand,
    NavigationStatus,
    PositionStatus,
    RobotReadiness,
    ActiveTransport,
    Pose2D,
    NavigationSession,
)
from domain.events import (
    AckEvent,
    StateExecutingEvent,
    StateProgressEvent,
    ResultSuccessEvent,
    ResultCanceledEvent,
    ResultErrorEvent,
    now_iso,
)


# ── NavigationStateMachine ─────────────────────────────────────────────────

class TestMapSymovoToAehub:
    def test_finished_maps_to_arrived(self):
        assert SM.map_symovo_to_aehub(SM.FINISHED) == NavigationStatusEnum.ARRIVED

    def test_error_maps_to_error(self):
        assert SM.map_symovo_to_aehub(SM.ERROR) == NavigationStatusEnum.ERROR

    def test_canceled_maps_to_idle(self):
        assert SM.map_symovo_to_aehub(SM.CANCELED) == NavigationStatusEnum.IDLE

    @pytest.mark.parametrize("state", [
        SM.UNKNOWN, SM.UNASSIGNED, SM.ASSIGNED,
        SM.RECEIVED, SM.STARTING, SM.RUNNING, SM.CANCELING,
    ])
    def test_non_terminal_maps_to_navigating(self, state):
        assert SM.map_symovo_to_aehub(state) == NavigationStatusEnum.NAVIGATING


class TestIsTerminalState:
    @pytest.mark.parametrize("state", [SM.FINISHED, SM.ERROR, SM.CANCELED])
    def test_terminal_states(self, state):
        assert SM.is_terminal_state(state) is True

    @pytest.mark.parametrize("state", [
        SM.UNKNOWN, SM.UNASSIGNED, SM.ASSIGNED,
        SM.RECEIVED, SM.STARTING, SM.RUNNING, SM.CANCELING,
    ])
    def test_non_terminal_states(self, state):
        assert SM.is_terminal_state(state) is False


class TestIsActiveState:
    @pytest.mark.parametrize("state", [SM.STARTING, SM.RUNNING, SM.CANCELING])
    def test_active_states(self, state):
        assert SM.is_active_state(state) is True

    @pytest.mark.parametrize("state", [
        SM.UNKNOWN, SM.UNASSIGNED, SM.ASSIGNED,
        SM.RECEIVED, SM.FINISHED, SM.ERROR, SM.CANCELED,
    ])
    def test_non_active_states(self, state):
        assert SM.is_active_state(state) is False


# ── Domain Models ──────────────────────────────────────────────────────────

class TestNavigationCommand:
    def test_valid_command(self):
        cmd = NavigationCommand(
            command_id="abc-123",
            timestamp="2025-01-01T00:00:00Z",
            target_id="position_A",
        )
        assert cmd.command_id == "abc-123"
        assert cmd.priority is None

    def test_with_priority(self):
        cmd = NavigationCommand(
            command_id="c1",
            timestamp="2025-01-01T00:00:00Z",
            target_id="pos_B",
            priority=5,
        )
        assert cmd.priority == 5


class TestNavigationStatus:
    def test_idle_status(self):
        s = NavigationStatus(status=NavigationStatusEnum.IDLE)
        assert s.progress_percent == 0
        assert s.goal_id is None

    def test_navigating_status(self):
        s = NavigationStatus(
            status=NavigationStatusEnum.NAVIGATING,
            goal_id="cmd-1",
            progress_percent=42,
            eta_seconds=15.5,
        )
        assert s.progress_percent == 42
        assert s.eta_seconds == 15.5

    def test_progress_bounds(self):
        with pytest.raises(Exception):
            NavigationStatus(status=NavigationStatusEnum.NAVIGATING, progress_percent=101)
        with pytest.raises(Exception):
            NavigationStatus(status=NavigationStatusEnum.NAVIGATING, progress_percent=-1)


class TestPositionStatus:
    def test_basic_position(self):
        p = PositionStatus(x=1.5, y=2.5, theta=math.pi)
        assert p.frame_id == "map"


class TestActiveTransport:
    def test_creation(self):
        t = ActiveTransport(
            command_id="c1",
            transport_id="t42",
            state=SM.RUNNING,
        )
        assert t.generation == 0
        assert isinstance(t.created_at, datetime)


class TestNavigationSession:
    def test_session_creation(self):
        session = NavigationSession(
            command_id="c1",
            start=Pose2D(x=0.0, y=0.0),
            goal=Pose2D(x=3.0, y=4.0),
            total_dist_m=5.0,
            min_remaining_dist_m=5.0,
        )
        assert session.progress_percent == 0
        assert session.total_dist_m == 5.0


# ── Event Models ───────────────────────────────────────────────────────────

class TestEvents:
    def test_ack_event(self):
        e = AckEvent(type="ack.received", command_id="c1")
        assert e.type == "ack.received"
        assert e.command_id == "c1"
        assert e.timestamp  # auto-filled

    def test_state_executing(self):
        e = StateExecutingEvent(type="state.executing", command_id="c2")
        assert e.type == "state.executing"

    def test_state_progress(self):
        e = StateProgressEvent(
            type="state.progress",
            command_id="c3",
            progress_percent=50,
            eta_seconds=10.0,
        )
        assert e.progress_percent == 50

    def test_result_success(self):
        e = ResultSuccessEvent(type="result.success", command_id="c4")
        assert e.type == "result.success"

    def test_result_canceled(self):
        e = ResultCanceledEvent(type="result.canceled", command_id="c5")
        assert e.type == "result.canceled"

    def test_result_error(self):
        e = ResultErrorEvent(
            type="result.error",
            command_id="c6",
            reason="timeout",
        )
        assert e.reason == "timeout"

    def test_now_iso_format(self):
        ts = now_iso()
        # Should be parseable
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None  # timezone-aware
