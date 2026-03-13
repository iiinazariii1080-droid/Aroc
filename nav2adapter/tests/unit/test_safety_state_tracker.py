"""Unit tests for SafetyStateTracker — core safety detection logic.

Covers: _check_lockout priority chain, evaluate() transitions & heartbeats,
        current_state() recovery_available logic, SafetyState.to_dict().
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from services.safety_state_tracker import (
    HEARTBEAT_INTERVAL_S,
    SafetyState,
    SafetyStateTracker,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _flags(
    emergency_stop: bool = False,
    safety_relais_closed_state: bool = True,
    sfuse_blown: bool = False,
    **extra: object,
) -> dict:
    """Build a minimal ``state_flags`` dict."""
    flags: dict = {
        "emergency_stop": emergency_stop,
        "safety_relais_closed_state": safety_relais_closed_state,
        "sfuse_blown": sfuse_blown,
    }
    flags.update(extra)
    return flags


def _raw(state_flags: dict | None = None) -> dict:
    """Wrap state_flags into raw_status dict (as returned by Symovo)."""
    return {"state_flags": state_flags or {}}


# ── _check_lockout ──────────────────────────────────────────────────


class TestCheckLockout:
    """Static method — no tracker state needed."""

    def test_empty_flags_fail_closed(self):
        """P0: empty state_flags → lockout (fail-closed)."""
        locked, reason = SafetyStateTracker._check_lockout({})
        assert locked is True
        assert reason == "unknown"

    def test_emergency_stop(self):
        """P0: emergency_stop=True → estop lockout."""
        locked, reason = SafetyStateTracker._check_lockout(
            _flags(emergency_stop=True)
        )
        assert locked is True
        assert reason == "estop"

    def test_relay_open(self):
        """P0: safety_relais_closed_state=False → relay_open lockout."""
        locked, reason = SafetyStateTracker._check_lockout(
            _flags(safety_relais_closed_state=False)
        )
        assert locked is True
        assert reason == "relay_open"

    def test_sfuse_blown(self):
        """P0: sfuse_blown=True → sfuse_blown lockout."""
        locked, reason = SafetyStateTracker._check_lockout(
            _flags(sfuse_blown=True)
        )
        assert locked is True
        assert reason == "sfuse_blown"

    def test_all_safe(self):
        """P0: normal operation → no lockout."""
        locked, reason = SafetyStateTracker._check_lockout(
            _flags()
        )
        assert locked is False
        assert reason is None

    def test_priority_estop_over_relay(self):
        """P1: estop takes priority even when relay is also open."""
        locked, reason = SafetyStateTracker._check_lockout(
            _flags(emergency_stop=True, safety_relais_closed_state=False)
        )
        assert locked is True
        assert reason == "estop"

    def test_relay_key_absent_not_lockout(self):
        """P1: if relay key is missing entirely, it's NOT treated as lockout."""
        flags = {"emergency_stop": False, "sfuse_blown": False}
        locked, reason = SafetyStateTracker._check_lockout(flags)
        assert locked is False
        assert reason is None


# ── evaluate() ──────────────────────────────────────────────────────


class TestEvaluate:
    """Tests for the state machine: transitions, heartbeats, edge cases."""

    def test_transition_normal_to_lockout(self):
        """P0: entering lockout returns SafetyState with safety_lockout=True."""
        tracker = SafetyStateTracker()
        # First call — tracker starts as not-locked, estop triggers transition
        result = tracker.evaluate({"state_flags": _flags(emergency_stop=True)})

        assert result is not None
        assert result.safety_lockout is True
        assert result.reason == "estop"
        assert result.since_ts is not None
        assert tracker.is_locked_out is True

    def test_transition_lockout_to_normal(self):
        """P0: leaving lockout returns SafetyState with safety_lockout=False."""
        tracker = SafetyStateTracker()
        # Enter lockout first
        tracker.evaluate({"state_flags": _flags(emergency_stop=True)})
        assert tracker.is_locked_out is True

        # Clear lockout
        result = tracker.evaluate({"state_flags": _flags()})
        assert result is not None
        assert result.safety_lockout is False
        assert result.reason is None
        assert result.since_ts is None
        assert tracker.is_locked_out is False

    def test_no_transition_no_heartbeat_returns_none(self):
        """P1: same state, heartbeat not due → None (nothing to publish)."""
        tracker = SafetyStateTracker()
        # First call: transition from initial → safe (initial is not-locked, safe is not-locked → no transition)
        # But first call with empty flags triggers lockout (fail-closed) then next call clears it
        # Actually: tracker starts _locked_out=False, safe flags → no transition, publishes heartbeat on first call
        result1 = tracker.evaluate({"state_flags": _flags()})
        # First call: no transition (False→False), but heartbeat is due (last_publish=0)
        assert result1 is not None  # heartbeat fires on first call

        # Second call immediately: no transition, heartbeat not due
        result2 = tracker.evaluate({"state_flags": _flags()})
        assert result2 is None

    def test_heartbeat_fires_after_interval(self):
        """P1: heartbeat publishes when interval elapses without transition."""
        tracker = SafetyStateTracker()
        tracker.evaluate({"state_flags": _flags()})  # initial heartbeat

        # Fast-forward past heartbeat interval
        tracker._last_publish_time = time.monotonic() - HEARTBEAT_INTERVAL_S - 1
        result = tracker.evaluate({"state_flags": _flags()})
        assert result is not None
        assert result.safety_lockout is False

    def test_raw_status_not_dict(self):
        """P1: raw_status not a dict → state_flags={} → lockout (fail-closed)."""
        tracker = SafetyStateTracker()
        result = tracker.evaluate("not a dict")  # type: ignore[arg-type]
        assert result is not None
        assert result.safety_lockout is True
        assert result.reason == "unknown"

    def test_raw_status_missing_state_flags(self):
        """P1: raw_status dict without state_flags key → empty → lockout."""
        tracker = SafetyStateTracker()
        result = tracker.evaluate({"something_else": 123})
        assert result is not None
        assert result.safety_lockout is True
        assert result.reason == "unknown"


# ── current_state() ────────────────────────────────────────────────


class TestCurrentState:
    def test_recovery_available_when_relay_restored(self):
        """P1: locked due to estop + relay now closed → recovery_available=True."""
        tracker = SafetyStateTracker()
        # Activate estop lockout
        tracker.evaluate({"state_flags": _flags(emergency_stop=True, safety_relais_closed_state=False)})
        assert tracker.is_locked_out is True

        # Relay restores (e-stop released), but we haven't cleared lockout yet
        # Update last_state_flags to indicate relay restored
        tracker._last_state_flags["safety_relais_closed_state"] = True

        state = tracker.current_state()
        assert state.recovery_available is True
        assert state.safety_lockout is True

    def test_recovery_not_available_when_relay_still_open(self):
        """P1: locked due to estop + relay still open → recovery_available=False."""
        tracker = SafetyStateTracker()
        tracker.evaluate({"state_flags": _flags(emergency_stop=True, safety_relais_closed_state=False)})

        state = tracker.current_state()
        assert state.recovery_available is False
        assert state.safety_lockout is True

    def test_recovery_not_available_when_not_locked(self):
        """P2: not locked → recovery_available=False."""
        tracker = SafetyStateTracker()
        tracker.evaluate({"state_flags": _flags()})

        state = tracker.current_state()
        assert state.recovery_available is False
        assert state.safety_lockout is False


# ── to_dict() ──────────────────────────────────────────────────────


class TestSafetyStateToDict:
    def test_to_dict_has_all_keys(self):
        """P2: to_dict() contains every documented field."""
        state = SafetyState(
            safety_lockout=True,
            reason="estop",
            state_flags={"emergency_stop": True},
            since_ts="2026-03-09T12:00:00+00:00",
            ts="2026-03-09T12:00:01+00:00",
            recovery_available=False,
        )
        d = state.to_dict()
        expected_keys = {"safety_lockout", "reason", "state_flags", "since_ts", "ts", "recovery_available"}
        assert set(d.keys()) == expected_keys
        assert d["safety_lockout"] is True
        assert d["reason"] == "estop"
