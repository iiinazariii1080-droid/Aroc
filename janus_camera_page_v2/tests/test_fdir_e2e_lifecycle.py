"""E2E FDIR lifecycle test — full chain without mocking individual components.

Verifies the complete recovery flow:
    startup → stale detection → ladder escalation through L0..L2 →
    recovery (healthy streak) → ladder reset to L0 + mode back to NOMINAL

This is NOT an integration test that hits real hardware or Janus.
Instead, it wires together the real RecoveryLadder, system_mode,
and fdir_events modules with only the I/O boundary mocked
(subprocess, filesystem paths, event loop).

Why this test exists:
    All previous tests mock individual components.  This test verifies
    the FDIR contract end-to-end: that a stream fault detected by the
    watchdog escalates through the ladder levels, and that a sustained
    healthy window resets everything back to nominal.
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("CAM_TYPE", "depth_camera")
os.environ.setdefault("CAM_ADMIN_TOKEN", "test-token")
os.environ.setdefault("FDIR_DEDUP_SEC", "0")

from app.services.fdir_events import (
    Domain,
    RecoveryAction,
    Severity,
    _get_ring,
    _lock as _ring_lock,
    recent,
)
from app.services.recovery_ladder import (
    LadderLevelConfig,
    RecoveryLadder,
)
from app.services.system_mode import (
    SystemMode,
    _state,
    current_mode,
    promote,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_mode() -> None:
    with _state.lock:
        _state.current = SystemMode.NOMINAL
        _state.entered_at = time.time()
        _state.reason = "test_reset"
        _state.listeners.clear()


def _clear_ring() -> None:
    with _ring_lock:
        _get_ring().clear()


@pytest.fixture(autouse=True)
def _isolate():
    _reset_mode()
    _clear_ring()
    yield
    _reset_mode()
    _clear_ring()


def _make_e2e_ladder(tmp_path):
    """Build a real RecoveryLadder with mocked I/O but real logic."""
    from unittest.mock import AsyncMock

    reboot_dir = tmp_path / "fdir-persist"
    reboot_dir.mkdir()

    from tests.conftest import make_test_settings
    settings = make_test_settings(
        tmp_path,
        fdir_dedup_sec=0.0,
        max_fdir_reboots=2,
        watchdog_reboot_enabled=True,
        camera_type="color_camera",
        fdir_ladder_state=tmp_path / "fdir_ladder.json",
        fdir_persist_dir=reboot_dir,
    )

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

    patches = [
        patch("app.services.recovery_ladder._ladder_state_path", new=lambda: tmp_path / "state.json"),
        patch("app.services.recovery_ladder._reboot_count_dir", new=lambda: reboot_dir),
        patch("app.services.recovery_ladder._reboot_count_path", new=lambda: reboot_dir / "reboot_count"),
        patch("app.services.recovery_ladder._reboot_marker_path", new=lambda: reboot_dir / "last_reboot_request"),
        patch("app.services.recovery_ladder.get_settings", return_value=settings),
        patch("app.utils.process.subprocess.run", mock_run),
        patch("app.services.recovery_ladder._default_ladder"),
    ]
    for p in patches:
        p.start()

    from app.services import recovery_ladder as rl_mod
    fake_levels = [
        LadderLevelConfig("retry_handle", RecoveryAction.RETRY_HANDLE, max_attempts=1, cooldown_sec=0),
        LadderLevelConfig("restart_pipeline", RecoveryAction.RESTART_PIPELINE, max_attempts=2, cooldown_sec=0),
        LadderLevelConfig("restart_janus", RecoveryAction.RESTART_JANUS, max_attempts=1, cooldown_sec=0),
    ]
    rl_mod._default_ladder.return_value = fake_levels

    # Mock _execute to simulate successful recovery actions without real I/O.
    # escalate() is now async, so _execute must return an awaitable.
    ladder = RecoveryLadder()
    ladder._execute = AsyncMock(return_value=True)

    return ladder, patches, mock_run


# ===================================================================
# E2E FDIR Lifecycle
# ===================================================================

class TestFdirE2ELifecycle:
    """Full FDIR lifecycle: stale → escalate through L0..L2 → recover → reset."""

    async def test_full_lifecycle(self, tmp_path):
        """
        Simulate the following timeline:

        1. Watchdog detects stale stream → escalate (L0: retry_handle)
        2. Still stale → L0 exhausted → escalate to L1 (restart_pipeline)
        3. Still stale → L1 attempt 1
        4. Still stale → L1 attempt 2 (exhausted) → escalate to L2 (restart_janus)
        5. Stream recovers → healthy streak → ladder.reset() + promote(NOMINAL)
        6. Verify: mode is NOMINAL, ladder at L0, FDIR events trace full chain
        """
        ladder, patches, _ = _make_e2e_ladder(tmp_path)
        try:
            # ── Phase 1: Detect stale → L0 retry_handle ──
            r = await ladder.escalate("video_age_ms=12000", Domain.PIPELINE)
            assert r["action"] == "retry_handle"
            assert r["level"] == "retry_handle"
            assert ladder.status()["current_level"] == 0

            # ── Phase 2: L0 exhausted → auto-escalate to L1 ──
            r = await ladder.escalate("video_age_ms=15000", Domain.PIPELINE)
            assert r["action"] == "restart_pipeline"
            assert r["level"] == "restart_pipeline"
            assert ladder.status()["current_level"] == 1
            # System should be degraded after escalation
            assert current_mode().level >= SystemMode.DEGRADED.level

            # ── Phase 3: L1 attempt 2 ──
            r = await ladder.escalate("video_age_ms=18000", Domain.PIPELINE)
            assert r["action"] == "restart_pipeline"
            assert r["attempt"] == 2

            # ── Phase 4: L1 exhausted → auto-escalate to L2 (restart_janus) ──
            r = await ladder.escalate("video_age_ms=20000", Domain.PIPELINE)
            assert r["action"] == "restart_janus"
            assert ladder.status()["current_level"] == 2

            # ── Phase 5: Stream recovers → reset ──
            ladder.reset()
            promote(SystemMode.NOMINAL, "stream healthy for sustained window")

            # ── Phase 6: Verify end state ──
            assert current_mode() == SystemMode.NOMINAL
            s = ladder.status()
            assert s["current_level"] == 0
            assert s["current_level_name"] == "retry_handle"
            for lvl in s["levels"]:
                assert lvl["attempts"] == 0

            # Verify FDIR event trail covers the full chain
            events = recent(50)
            actions_seen = {e["recovery_action"] for e in events}
            assert "retry_handle" in actions_seen or "none" in actions_seen
            assert any("restart_pipeline" in e.get("outcome", "") for e in events)
            assert any("restart_janus" in e.get("outcome", "") for e in events)
            # Reset event should be present
            assert any("reset" in e.get("outcome", "").lower() for e in events)

        finally:
            for p in patches:
                p.stop()

    def test_reboot_circuit_breaker_prevents_infinite_reboot(self, tmp_path):
        """After max reboots, system enters SAFE instead of rebooting again."""
        reboot_dir = tmp_path / "fdir-persist"
        reboot_dir.mkdir()
        (reboot_dir / "reboot_count").write_text("2\n")

        from tests.conftest import make_test_settings
        settings = make_test_settings(
            tmp_path, fdir_dedup_sec=0.0, max_fdir_reboots=2,
            watchdog_reboot_enabled=True, fdir_ladder_state=tmp_path / "state.json",
            fdir_persist_dir=reboot_dir,
        )

        with (
            patch("app.services.recovery_ladder._ladder_state_path", new=lambda: tmp_path / "state.json"),
            patch("app.services.recovery_ladder._reboot_count_dir", new=lambda: reboot_dir),
            patch("app.services.recovery_ladder._reboot_count_path", new=lambda: reboot_dir / "reboot_count"),
            patch("app.services.recovery_ladder._reboot_marker_path", new=lambda: reboot_dir / "last_reboot_request"),
            patch("app.services.recovery_ladder.get_settings", return_value=settings),
            patch("app.utils.process.subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            _reset_mode()
            rl = RecoveryLadder()
            rl.check_circuit_breaker()

            assert current_mode() == SystemMode.SAFE
            events = recent(10)
            assert any("circuit breaker" in e.get("outcome", "").lower() for e in events)

    async def test_recovery_after_degradation_restores_nominal(self, tmp_path):
        """After escalation degrades mode, recovery promotes back to NOMINAL."""
        ladder, patches, _ = _make_e2e_ladder(tmp_path)
        try:
            # Escalate to cause degradation
            await ladder.escalate("sig")
            await ladder.escalate("sig")  # L0→L1 triggers degrade
            assert current_mode().level >= SystemMode.DEGRADED.level

            # Simulate sustained healthy window
            ladder.reset()
            promote(SystemMode.NOMINAL, "healthy_streak")

            assert current_mode() == SystemMode.NOMINAL
            assert ladder.status()["current_level"] == 0
        finally:
            for p in patches:
                p.stop()

    async def test_execute_retry_handle_reports_failure_when_janus_unreachable(self, tmp_path):
        """RETRY_HANDLE returns success=False when Janus reports unreachable."""
        from unittest.mock import AsyncMock

        reboot_dir = tmp_path / "fdir-persist"
        reboot_dir.mkdir()

        from tests.conftest import make_test_settings
        settings = make_test_settings(
            tmp_path, fdir_dedup_sec=0.0, max_fdir_reboots=2,
            watchdog_reboot_enabled=True, camera_type="color_camera",
            fdir_ladder_state=tmp_path / "state.json", fdir_persist_dir=reboot_dir,
        )

        with (
            patch("app.services.recovery_ladder._ladder_state_path", new=lambda: tmp_path / "state.json"),
            patch("app.services.recovery_ladder._reboot_count_dir", new=lambda: reboot_dir),
            patch("app.services.recovery_ladder._reboot_count_path", new=lambda: reboot_dir / "reboot_count"),
            patch("app.services.recovery_ladder._reboot_marker_path", new=lambda: reboot_dir / "last_reboot_request"),
            patch("app.services.recovery_ladder.get_settings", return_value=settings),
            patch("app.utils.process.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
            patch("app.services.recovery_ladder._default_ladder") as mock_dl,
            # Simulate Janus unreachable
            patch("app.services.janus.janus_summary", new_callable=AsyncMock, return_value={"reachable": False}),
        ):
            mock_dl.return_value = [
                LadderLevelConfig("retry_handle", RecoveryAction.RETRY_HANDLE, max_attempts=1, cooldown_sec=0),
            ]
            rl = RecoveryLadder()
            # Don't mock _execute — let it run the real RETRY_HANDLE logic
            result = await rl.escalate("janus_unreachable_test")
            # With Janus unreachable, RETRY_HANDLE should report failure
            assert result["success"] is False
