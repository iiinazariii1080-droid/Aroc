"""Phase 4 — FDIR Integration Tests (L4-FDIR, X4-FDIR).

Tests the 5-level recovery ladder, system mode state machine,
and FDIR event ring buffer in isolation (no hardware / systemctl).

Covers:
  - Mode transitions: NOMINAL → DEGRADED → LOCAL_ONLY → SAFE
  - Promotion: DEGRADED → NOMINAL on healthy streak
  - Recovery ladder: escalation through retry → pipeline → janus → reboot
  - Reboot circuit breaker: max_fdir_reboots → SAFE
  - Event ring buffer: emit / recent / 500-event cap
  - Ladder reset clears reboot counter
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import asdict
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure test-safe env BEFORE any production imports ────────────────
os.environ.setdefault("CAM_TYPE", "depth_camera")
os.environ.setdefault("CAM_ADMIN_TOKEN", "test-token")
os.environ.setdefault("MAX_FDIR_REBOOTS", "2")
os.environ.setdefault("FDIR_DEDUP_SEC", "0")  # disable dedup for fast tests

from app.services.fdir_events import (
    Domain,
    FdirEvent,
    RecoveryAction,
    Severity,
    _lock as _ring_lock,
    _ring,
    emit,
    recent,
)
from app.services.system_mode import (
    MODE_POLICIES,
    SystemMode,
    _state,
    current_mode,
    degrade,
    mode_info,
    on_transition,
    promote,
    transition,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_mode() -> None:
    """Force system mode back to NOMINAL for test isolation."""
    with _state.lock:
        _state.current = SystemMode.NOMINAL
        _state.entered_at = time.time()
        _state.reason = "test_reset"
        _state.listeners.clear()


def _clear_ring() -> None:
    """Flush the in-memory FDIR event ring buffer."""
    with _ring_lock:
        _ring.clear()


@pytest.fixture(autouse=True)
def _isolate():
    """Reset shared state before each test."""
    _reset_mode()
    _clear_ring()
    yield
    _reset_mode()
    _clear_ring()


# ===================================================================
# 1. System Mode State Machine
# ===================================================================

class TestSystemModeTransitions:
    """Verify SystemMode lattice: NOMINAL → DEGRADED → LOCAL_ONLY → SAFE."""

    def test_initial_mode_is_nominal(self):
        assert current_mode() == SystemMode.NOMINAL

    def test_transition_to_degraded(self):
        changed = transition(SystemMode.DEGRADED, "test_fault")
        assert changed is True
        assert current_mode() == SystemMode.DEGRADED

    def test_transition_same_mode_returns_false(self):
        assert transition(SystemMode.NOMINAL, "noop") is False

    def test_degrade_steps_one_level(self):
        degrade("step1")
        assert current_mode() == SystemMode.DEGRADED
        degrade("step2")
        assert current_mode() == SystemMode.LOCAL_ONLY
        degrade("step3")
        assert current_mode() == SystemMode.SAFE

    def test_degrade_from_safe_stays_safe(self):
        transition(SystemMode.SAFE, "bottom")
        degrade("already_safe")
        assert current_mode() == SystemMode.SAFE

    def test_promote_to_better_mode(self):
        transition(SystemMode.DEGRADED, "fault")
        ok = promote(SystemMode.NOMINAL, "recovery")
        assert ok is True
        assert current_mode() == SystemMode.NOMINAL

    def test_promote_to_worse_mode_rejected(self):
        assert promote(SystemMode.SAFE, "bad") is False
        assert current_mode() == SystemMode.NOMINAL

    def test_promote_to_same_mode_rejected(self):
        transition(SystemMode.DEGRADED, "x")
        assert promote(SystemMode.DEGRADED, "no-op") is False

    def test_mode_info_snapshot(self):
        transition(SystemMode.LOCAL_ONLY, "test_reason")
        info = mode_info()
        assert info["mode"] == "local_only"
        assert info["reason"] == "test_reason"
        assert info["policy"]["require_uplink"] is False

    def test_listener_called_on_transition(self):
        calls: list[tuple] = []
        on_transition(lambda prev, nxt, reason: calls.append((prev, nxt, reason)))
        transition(SystemMode.DEGRADED, "fault_x")
        assert len(calls) == 1
        prev, nxt, reason = calls[0]
        assert prev == SystemMode.NOMINAL
        assert nxt == SystemMode.DEGRADED
        assert reason == "fault_x"


class TestModePolicies:
    """MODE_POLICIES dict must contain sane values for every mode."""

    def test_all_modes_have_policies(self):
        for mode in SystemMode:
            assert mode in MODE_POLICIES

    def test_safe_mode_disables_streams(self):
        p = MODE_POLICIES[SystemMode.SAFE]
        assert p.streams_enabled is False
        assert p.max_fps == 0

    def test_nominal_has_full_quality(self):
        p = MODE_POLICIES[SystemMode.NOMINAL]
        assert p.streams_enabled is True
        assert p.max_fps == 30
        assert p.max_bitrate_kbps == 4000

    def test_degraded_reduces_fps(self):
        assert MODE_POLICIES[SystemMode.DEGRADED].max_fps < MODE_POLICIES[SystemMode.NOMINAL].max_fps

    def test_local_only_no_uplink(self):
        p = MODE_POLICIES[SystemMode.LOCAL_ONLY]
        assert p.require_turn is False
        assert p.require_uplink is False

    def test_mode_level_ordering(self):
        assert SystemMode.NOMINAL.level < SystemMode.DEGRADED.level
        assert SystemMode.DEGRADED.level < SystemMode.LOCAL_ONLY.level
        assert SystemMode.LOCAL_ONLY.level < SystemMode.SAFE.level


# ===================================================================
# 2. FDIR Event Ring Buffer
# ===================================================================

class TestFdirEvents:
    """Validate emit(), recent(), and ring capacity."""

    def test_emit_returns_event(self):
        ev = emit(
            Domain.PIPELINE,
            Severity.WARN,
            "stream_stale",
            RecoveryAction.RESTART_PIPELINE,
            "restarted",
        )
        assert isinstance(ev, FdirEvent)
        assert ev.domain == "pipeline"
        assert ev.severity == "warn"

    def test_recent_returns_newest_first(self):
        emit(Domain.SENSOR, Severity.INFO, "sig1", RecoveryAction.NONE, "ok")
        emit(Domain.SENSOR, Severity.INFO, "sig2", RecoveryAction.NONE, "ok")
        items = recent(10)
        assert items[0]["detection_signal"] == "sig2"
        assert items[1]["detection_signal"] == "sig1"

    def test_ring_capacity_capped(self):
        for i in range(600):
            emit(Domain.SYSTEM, Severity.INFO, f"s{i}", RecoveryAction.NONE, "ok")
        with _ring_lock:
            assert len(_ring) == 500

    def test_event_to_json_roundtrip(self):
        ev = emit(Domain.JANUS, Severity.ERROR, "janus_down", RecoveryAction.RESTART_JANUS, "ok")
        import json
        parsed = json.loads(ev.to_json())
        assert parsed["domain"] == "janus"
        assert parsed["recovery_action"] == "restart_janus"

    def test_emit_with_details(self):
        ev = emit(
            Domain.NETWORK, Severity.WARN, "high_loss",
            RecoveryAction.DEGRADE_PROFILE, "degraded",
            details={"loss_pct": 4.2},
        )
        assert ev.details["loss_pct"] == 4.2
        items = recent(1)
        assert items[0]["details"]["loss_pct"] == 4.2


# ===================================================================
# 3. Recovery Ladder (unit-level, mocked I/O)
# ===================================================================

@pytest.fixture()
def ladder(tmp_path):
    """Create a fresh RecoveryLadder with temp persistence paths."""
    ladder_state = tmp_path / "fdir_ladder.json"
    reboot_dir = tmp_path / "fdir-persist"
    reboot_dir.mkdir()

    patches = [
        patch("app.services.recovery_ladder._LADDER_STATE_PATH", ladder_state),
        patch("app.services.recovery_ladder._REBOOT_COUNT_DIR", reboot_dir),
        patch("app.services.recovery_ladder._REBOOT_COUNT_PATH", reboot_dir / "reboot_count"),
        patch("app.services.recovery_ladder._REBOOT_MARKER_PATH", reboot_dir / "last_reboot_request"),
        patch("app.services.recovery_ladder._DEDUP_WINDOW_SEC", 0),
        # Prevent actual subprocess calls
        patch("app.services.recovery_ladder.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        # Prevent actual Janus call in RETRY_HANDLE
        patch("app.services.recovery_ladder._default_ladder"),
    ]
    for p in patches:
        p.start()

    # Build a controlled ladder: 4 levels (retry 1, pipeline 2, janus 1, reboot 1)
    from app.services.recovery_ladder import LadderLevel, RecoveryAction as RA, RecoveryLadder

    fake_levels = [
        LadderLevel("retry_handle", RA.RETRY_HANDLE, max_attempts=1, cooldown_sec=0),
        LadderLevel("restart_pipeline", RA.RESTART_PIPELINE, max_attempts=2, cooldown_sec=0),
        LadderLevel("restart_janus", RA.RESTART_JANUS, max_attempts=1, cooldown_sec=0),
        LadderLevel("reboot_node", RA.REBOOT_NODE, max_attempts=1, cooldown_sec=0),
    ]

    from app.services import recovery_ladder as rl_mod
    rl_mod._default_ladder.return_value = fake_levels

    # Mock _execute so it doesn't run real commands
    with patch.object(RecoveryLadder, "_execute", return_value=True):
        obj = RecoveryLadder()

    # Re-attach the mock on the living instance
    obj._execute = MagicMock(return_value=True)

    yield obj

    for p in patches:
        p.stop()


class TestRecoveryLadder:
    """Unit tests for the escalating recovery ladder."""

    def test_initial_level_is_zero(self, ladder):
        s = ladder.status()
        assert s["current_level"] == 0
        assert s["current_level_name"] == "retry_handle"

    def test_escalate_consumes_attempt(self, ladder):
        result = ladder.escalate("test_signal")
        assert result["action"] == "retry_handle"
        assert result["attempt"] == 1

    def test_budget_exhausted_escalates(self, ladder):
        """Level 0 has max_attempts=1. Second call must escalate to level 1."""
        ladder.escalate("sig")
        result = ladder.escalate("sig")
        # Should have auto-escalated to restart_pipeline
        assert result["action"] == "restart_pipeline"
        assert ladder.status()["current_level"] == 1

    def test_full_escalation_to_reboot(self, ladder):
        """Exhaust all levels until reboot_node is reached."""
        # L0 retry: 1 attempt
        ladder.escalate("sig")
        # L1 pipeline: 2 attempts
        ladder.escalate("sig")
        ladder.escalate("sig")
        # L2 janus: 1 attempt
        ladder.escalate("sig")
        # L3 reboot: 1 attempt
        result = ladder.escalate("sig")
        assert result["action"] == "reboot_node"

    def test_all_exhausted_enters_safe(self, ladder):
        """After every level is used up, next call → SAFE mode."""
        for _ in range(10):
            ladder.escalate("sig")
        assert current_mode() == SystemMode.SAFE

    def test_reset_clears_ladder(self, ladder):
        ladder.escalate("sig")
        ladder.escalate("sig")
        ladder.reset()
        s = ladder.status()
        assert s["current_level"] == 0
        for lvl in s["levels"]:
            assert lvl["attempts"] == 0

    def test_status_includes_reboot_count(self, ladder):
        s = ladder.status()
        assert "reboot_count" in s
        assert "max_fdir_reboots" in s


class TestRebootCircuitBreaker:
    """The ladder must enter SAFE mode if reboot count ≥ max_fdir_reboots."""

    def test_circuit_breaker_triggers_safe(self, tmp_path):
        """Pre-seed reboot_count = 2 (max), init should enter SAFE mode."""
        reboot_dir = tmp_path / "fdir-persist"
        reboot_dir.mkdir()
        (reboot_dir / "reboot_count").write_text("2\n")

        with (
            patch("app.services.recovery_ladder._LADDER_STATE_PATH", tmp_path / "state.json"),
            patch("app.services.recovery_ladder._REBOOT_COUNT_DIR", reboot_dir),
            patch("app.services.recovery_ladder._REBOOT_COUNT_PATH", reboot_dir / "reboot_count"),
            patch("app.services.recovery_ladder._REBOOT_MARKER_PATH", reboot_dir / "last_reboot_request"),
            patch("app.services.recovery_ladder._DEDUP_WINDOW_SEC", 0),
            patch("app.services.recovery_ladder.subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            from app.services.recovery_ladder import RecoveryLadder
            _reset_mode()
            _rl = RecoveryLadder()
            assert current_mode() == SystemMode.SAFE

    def test_reset_clears_reboot_counter(self, tmp_path):
        """After reset(), the reboot_count file must be zero."""
        reboot_dir = tmp_path / "fdir-persist"
        reboot_dir.mkdir()
        (reboot_dir / "reboot_count").write_text("1\n")

        with (
            patch("app.services.recovery_ladder._LADDER_STATE_PATH", tmp_path / "state.json"),
            patch("app.services.recovery_ladder._REBOOT_COUNT_DIR", reboot_dir),
            patch("app.services.recovery_ladder._REBOOT_COUNT_PATH", reboot_dir / "reboot_count"),
            patch("app.services.recovery_ladder._REBOOT_MARKER_PATH", reboot_dir / "last_reboot_request"),
            patch("app.services.recovery_ladder._DEDUP_WINDOW_SEC", 0),
            patch("app.services.recovery_ladder.subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            from app.services.recovery_ladder import RecoveryLadder
            _rl = RecoveryLadder()
            _rl._execute = MagicMock(return_value=True)
            _rl.reset()
            count = int((reboot_dir / "reboot_count").read_text().strip())
            assert count == 0


# ===================================================================
# 4. Ladder ↔ Mode Integration
# ===================================================================

class TestLadderModeIntegration:
    """Each escalation step should degrade the system mode one level."""

    def test_escalation_degrades_mode(self, ladder):
        """Exhaust L0 (retry) → escalation to L1 must call degrade()."""
        assert current_mode() == SystemMode.NOMINAL
        # L0 retry: 1 attempt (consumes budget)
        ladder.escalate("sig")
        # Next call triggers escalation from L0→L1, which calls degrade()
        ladder.escalate("sig")
        assert current_mode().level >= SystemMode.DEGRADED.level

    def test_mode_events_emitted_on_transition(self):
        """Mode transitions emit FDIR events with SWITCH_MODE action."""
        transition(SystemMode.DEGRADED, "test_mode_event")
        items = recent(5)
        switch_events = [e for e in items if e["recovery_action"] == "switch_mode"]
        assert len(switch_events) >= 1
        assert switch_events[0]["outcome"].startswith("mode:")


# ===================================================================
# 5. Node-local reboot targeting
# ===================================================================

class TestNodeLocalReboot:
    """Each node reboots itself — never the other node."""

    def _make_ladder(self, tmp_path, cam_type, mock_run):
        """Create a fresh ladder with a single reboot_node level."""
        from app.services.recovery_ladder import (
            LadderLevel,
            RecoveryAction as RA,
            RecoveryLadder,
        )

        reboot_dir = tmp_path / "fdir-persist"
        reboot_dir.mkdir(exist_ok=True)

        p_state = patch("app.services.recovery_ladder._LADDER_STATE_PATH", tmp_path / "state.json")
        p_rdir = patch("app.services.recovery_ladder._REBOOT_COUNT_DIR", reboot_dir)
        p_rpath = patch("app.services.recovery_ladder._REBOOT_COUNT_PATH", reboot_dir / "reboot_count")
        p_marker = patch("app.services.recovery_ladder._REBOOT_MARKER_PATH", reboot_dir / "last_reboot_request")
        p_dedup = patch("app.services.recovery_ladder._DEDUP_WINDOW_SEC", 0)
        p_run = patch("app.services.recovery_ladder.subprocess.run", mock_run)
        p_dl = patch("app.services.recovery_ladder._default_ladder")
        p_settings = patch("app.services.recovery_ladder.get_settings")

        all_patches = [p_state, p_rdir, p_rpath, p_marker, p_dedup, p_run, p_dl, p_settings]
        for p in all_patches:
            p.start()

        mock_dl = p_dl.start()
        mock_settings = p_settings.start()

        settings = MagicMock()
        settings.camera_type = cam_type
        settings.watchdog_reboot_enabled = True
        settings.max_fdir_reboots = 2
        settings.service_name = "test.service"
        mock_settings.return_value = settings

        fake_levels = [
            LadderLevel("reboot_node", RA.REBOOT_NODE, max_attempts=1, cooldown_sec=0),
        ]
        mock_dl.return_value = fake_levels

        _reset_mode()
        rl = RecoveryLadder()
        return rl, all_patches

    def test_color_node_reboots_locally(self, tmp_path):
        """Color node (192.168.1.10) reboots itself via systemctl reboot."""
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        rl, patches = self._make_ladder(tmp_path, "color_camera", mock_run)
        try:
            result = rl.escalate("color_cam_fault")
            assert result["action"] == "reboot_node"
            reboot_calls = [c for c in mock_run.call_args_list if "reboot" in str(c)]
            assert len(reboot_calls) >= 1, "color node must call systemctl reboot locally"
        finally:
            for p in patches:
                p.stop()

    def test_depth_node_reboots_locally(self, tmp_path):
        """Depth node (192.168.1.55) reboots itself via systemctl reboot."""
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        rl, patches = self._make_ladder(tmp_path, "depth_camera", mock_run)
        try:
            result = rl.escalate("depth_cam_fault")
            assert result["action"] == "reboot_node"
            reboot_calls = [c for c in mock_run.call_args_list if "reboot" in str(c)]
            assert len(reboot_calls) >= 1, "depth node must call systemctl reboot locally"
        finally:
            for p in patches:
                p.stop()
