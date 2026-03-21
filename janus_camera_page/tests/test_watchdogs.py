"""Tests for app/services/watchdogs.py — Janus and snapshot watchdogs with FDIR ladder."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch, call

import pytest

from app.services import watchdogs


@pytest.fixture(autouse=True)
def _reset_watchdog_state():
    """Reset all watchdog module-level state between tests."""
    watchdogs._STARTUP_TS = 0  # far in the past — skip grace by default
    watchdogs._last_escalation_ts = 0.0
    watchdogs._first_healthy_seen = False
    watchdogs._last_observed_mtime = 0.0
    watchdogs._last_mtime_change_mono = 0.0
    watchdogs._stop_event.clear()
    yield
    watchdogs._stop_event.clear()
    watchdogs._first_healthy_seen = False
    watchdogs._last_observed_mtime = 0.0
    watchdogs._last_mtime_change_mono = 0.0
    watchdogs._STARTUP_TS = time.monotonic()


class TestStartJanusWatchdog:
    @patch("app.services.watchdogs.threading.Thread")
    @patch("app.services.watchdogs.get_settings")
    def test_disabled(self, mock_settings, mock_thread):
        mock_settings.return_value = MagicMock(watchdog_enabled=False)
        watchdogs.start_janus_watchdog()
        mock_thread.assert_not_called()

    @patch("app.services.watchdogs.threading.Thread")
    @patch("app.services.watchdogs.get_settings")
    def test_enabled_starts_thread(self, mock_settings, mock_thread):
        mock_settings.return_value = MagicMock(watchdog_enabled=True)
        watchdogs.start_janus_watchdog()
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()


class TestWatchdogLoop:
    def _stop_after_one(self, _timeout=None):
        """Signal the loop to stop after one iteration."""
        watchdogs._stop_event.set()

    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.janus.janus_summary")
    @patch("app.services.watchdogs.get_settings")
    def test_stale_video_triggers_escalate(self, mock_settings, mock_summary, mock_ladder):
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        mock_summary.return_value = {"video_age_ms": 99999}
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst
        with patch.object(watchdogs._stop_event, "wait", side_effect=self._stop_after_one):
            watchdogs._watchdog_loop()
        ladder_inst.escalate.assert_called_once()

    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.janus.janus_summary")
    @patch("app.services.watchdogs.get_settings")
    def test_fresh_video_no_escalate(self, mock_settings, mock_summary, mock_ladder):
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        mock_summary.return_value = {"video_age_ms": 100}
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst
        with patch.object(watchdogs._stop_event, "wait", side_effect=self._stop_after_one):
            watchdogs._watchdog_loop()
        ladder_inst.escalate.assert_not_called()

    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.janus.janus_summary", side_effect=Exception("fail"))
    @patch("app.services.watchdogs.get_settings")
    def test_exception_does_not_crash(self, mock_settings, mock_summary, mock_ladder):
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst
        with patch.object(watchdogs._stop_event, "wait", side_effect=self._stop_after_one):
            watchdogs._watchdog_loop()


class TestStartSnapshotWatchdog:
    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_settings")
    async def test_disabled(self, mock_settings):
        mock_settings.return_value = MagicMock(snapshot_watchdog_enabled=False)
        # Should return without creating a task
        await watchdogs.start_snapshot_watchdog()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.asyncio.create_task")
    @patch("app.services.watchdogs.get_settings")
    async def test_enabled_creates_task(self, mock_settings, mock_task):
        mock_settings.return_value = MagicMock(snapshot_watchdog_enabled=True)
        await watchdogs.start_snapshot_watchdog()
        mock_task.assert_called_once()


class TestSnapshotWatchdogLoop:
    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat", side_effect=FileNotFoundError)
    @patch("app.services.watchdogs.get_settings")
    async def test_missing_file_triggers_escalate(self, mock_settings, mock_stat, mock_ladder):
        mock_settings.return_value = MagicMock(
            snapshot_path="/nonexistent.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst

        async def _one_shot(sec):
            watchdogs._stop_event.set()  # stop after first iteration

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_one_shot):
            await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat")
    @patch("app.services.watchdogs.get_settings")
    async def test_stale_snapshot_triggers_escalate(self, mock_settings, mock_stat, mock_ladder):
        mock_settings.return_value = MagicMock(
            snapshot_path="/tmp/snap.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        mock_stat.return_value = MagicMock(st_mtime=1000.0)
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst

        # Pre-seed: mtime was seen 60s ago (monotonic), hasn't changed since.
        watchdogs._last_observed_mtime = 1000.0
        watchdogs._last_mtime_change_mono = time.monotonic() - 60

        async def _one_shot(sec):
            watchdogs._stop_event.set()  # stop after first iteration

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_one_shot):
            await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat")
    @patch("app.services.watchdogs.get_settings")
    async def test_ntp_jump_forward_no_false_stale(self, mock_settings, mock_stat, mock_ladder):
        """DEF-02: NTP jump forward must NOT cause false stale when mtime just changed."""
        mock_settings.return_value = MagicMock(
            snapshot_path="/tmp/snap.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        # Mtime changed (new value vs last observed) → treated as fresh
        mock_stat.return_value = MagicMock(st_mtime=2000.0)
        watchdogs._last_observed_mtime = 1000.0
        watchdogs._last_mtime_change_mono = 0.0  # will be seeded to now

        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst

        async def _one_shot(sec):
            watchdogs._stop_event.set()

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_one_shot):
            await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat")
    @patch("app.services.watchdogs.get_settings")
    async def test_ntp_jump_backward_no_false_fresh(self, mock_settings, mock_stat, mock_ladder):
        """DEF-02: NTP jump backward must NOT cause false fresh — monotonic detects stale."""
        mock_settings.return_value = MagicMock(
            snapshot_path="/tmp/snap.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        # Mtime unchanged (same as last observed) — monotonic says 60s stale
        mock_stat.return_value = MagicMock(st_mtime=1000.0)
        watchdogs._last_observed_mtime = 1000.0
        watchdogs._last_mtime_change_mono = time.monotonic() - 60

        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst

        async def _one_shot(sec):
            watchdogs._stop_event.set()

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_one_shot):
            await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_called_once()


class TestGracePeriodSuppression:
    """Direct tests for grace period suppressing escalation."""

    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.janus.janus_summary")
    @patch("app.services.watchdogs.get_settings")
    def test_grace_period_suppresses_escalation(self, mock_settings, mock_summary, mock_ladder):
        """During grace period, stale video must NOT trigger escalation."""
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=9999,  # long grace — still active
        )
        watchdogs._STARTUP_TS = time.monotonic()  # just started
        mock_summary.return_value = {"video_age_ms": 99999}
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst

        def _stop(_timeout=None):
            watchdogs._stop_event.set()

        with patch.object(watchdogs._stop_event, "wait", side_effect=_stop):
            watchdogs._watchdog_loop()
        ladder_inst.escalate.assert_not_called()

    def test_grace_period_ends_early_on_healthy(self):
        """Setting _first_healthy_seen=True must end grace immediately."""
        watchdogs._STARTUP_TS = time.monotonic()  # just started
        # Grace should be active
        with patch("app.services.watchdogs.get_settings") as mock_s:
            mock_s.return_value = MagicMock(watchdog_grace_sec=9999)
            assert watchdogs._in_grace_period() is True
            # First healthy seen → grace ends
            with watchdogs._escalation_lock:
                watchdogs._first_healthy_seen = True
            assert watchdogs._in_grace_period() is False

    def test_grace_period_expired(self):
        """After grace_sec elapsed, grace period must be False."""
        watchdogs._STARTUP_TS = 0  # far in the past
        with patch("app.services.watchdogs.get_settings") as mock_s:
            mock_s.return_value = MagicMock(watchdog_grace_sec=60)
            assert watchdogs._in_grace_period() is False


class TestEscalationDedup:
    """Direct tests for _try_escalate dedup window."""

    def test_first_escalation_succeeds(self):
        """First escalation must succeed and call ladder.escalate()."""
        ladder = MagicMock()
        result = watchdogs._try_escalate(ladder, "test_signal", watchdogs.Domain.PIPELINE)
        assert result is True
        ladder.escalate.assert_called_once_with("test_signal", watchdogs.Domain.PIPELINE)

    def test_rapid_second_escalation_deduped(self):
        """Second escalation within dedup window must be suppressed."""
        ladder = MagicMock()
        watchdogs._try_escalate(ladder, "first", watchdogs.Domain.PIPELINE)
        result = watchdogs._try_escalate(ladder, "second", watchdogs.Domain.PIPELINE)
        assert result is False
        # Only the first call went through
        assert ladder.escalate.call_count == 1

    def test_escalation_after_dedup_window_succeeds(self):
        """After dedup window expires, escalation must succeed again."""
        ladder = MagicMock()
        watchdogs._try_escalate(ladder, "first", watchdogs.Domain.PIPELINE)
        # Move last escalation timestamp back beyond the dedup window
        watchdogs._last_escalation_ts = time.monotonic() - watchdogs._ESCALATION_DEDUP_SEC - 1
        result = watchdogs._try_escalate(ladder, "second", watchdogs.Domain.JANUS)
        assert result is True
        assert ladder.escalate.call_count == 2

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat")
    @patch("app.services.watchdogs.get_settings")
    async def test_snapshot_respects_dedup(self, mock_settings, mock_stat, mock_ladder):
        """Snapshot watchdog must respect dedup when Janus watchdog just escalated."""
        mock_settings.return_value = MagicMock(
            snapshot_path="/tmp/snap.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        mock_stat.return_value = MagicMock(st_mtime=1000.0)
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst

        # Pre-seed: mtime seen 60s ago (stale by monotonic)
        watchdogs._last_observed_mtime = 1000.0
        watchdogs._last_mtime_change_mono = time.monotonic() - 60

        # Simulate Janus watchdog just escalated
        watchdogs._last_escalation_ts = time.monotonic()

        async def _one_shot(sec):
            watchdogs._stop_event.set()

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_one_shot):
            await watchdogs._snapshot_watchdog_loop()
        # Snapshot should NOT escalate — dedup window active
        ladder_inst.escalate.assert_not_called()


class TestHealthyStreakReset:
    """Test that sustained healthy checks reset ladder and promote to NOMINAL."""

    @patch("app.services.watchdogs.system_mode")
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.janus.janus_summary")
    @patch("app.services.watchdogs.get_settings")
    def test_nominal_after_sustained_healthy(self, mock_settings, mock_summary, mock_ladder, mock_mode):
        """After _NOMINAL_WINDOW_CHECKS healthy iterations, ladder resets and mode promotes."""
        nominal_checks = watchdogs._NOMINAL_WINDOW_CHECKS
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=0,
            watchdog_grace_sec=0,
        )
        mock_summary.return_value = {"video_age_ms": 100}  # healthy
        ladder_inst = MagicMock()
        mock_ladder.return_value = ladder_inst

        iteration = 0

        def _stop_after_n(_timeout=None):
            nonlocal iteration
            iteration += 1
            if iteration >= nominal_checks:
                watchdogs._stop_event.set()

        with patch.object(watchdogs._stop_event, "wait", side_effect=_stop_after_n):
            watchdogs._watchdog_loop()

        ladder_inst.reset.assert_called_once()
        mock_mode.promote.assert_called_once()


class TestSafeModeAutoReset:
    """SAFE mode auto-reset: after timeout, reset ladder and promote to NOMINAL."""

    @patch("app.services.watchdogs.system_mode")
    @patch("app.services.watchdogs.get_settings")
    @patch("app.services.watchdogs.janus")
    @patch("app.services.watchdogs.get_ladder")
    def test_safe_mode_auto_reset_triggers(self, mock_get_ladder, mock_janus, mock_settings, mock_mode):
        """When SAFE mode has been active longer than threshold, auto-reset fires."""
        from app.services.system_mode import SystemMode

        mock_settings.return_value = MagicMock(
            watchdog_enabled=True,
            janus_mount_id=1,
            watchdog_stale_ms=5000,
            watchdog_grace_sec=0,
            watchdog_interval_sec=1,
        )
        mock_janus.janus_summary.return_value = {"video_age_ms": 99999}

        ladder_inst = MagicMock()
        mock_get_ladder.return_value = ladder_inst

        # Use real enum values so equality checks work
        mock_mode.SystemMode = SystemMode
        mock_mode.current_mode.return_value = SystemMode.SAFE
        mock_mode.mode_uptime_sec.return_value = watchdogs._SAFE_MODE_AUTO_RESET_SEC + 10

        iteration = 0
        def _stop_after_one(_timeout=None):
            nonlocal iteration
            iteration += 1
            if iteration >= 1:
                watchdogs._stop_event.set()

        with patch.object(watchdogs._stop_event, "wait", side_effect=_stop_after_one):
            watchdogs._watchdog_loop()

        ladder_inst.reset.assert_called_once()
        mock_mode.promote.assert_called_once_with(
            SystemMode.NOMINAL,
            "safe_mode_auto_reset",
        )

    @patch("app.services.watchdogs.system_mode")
    @patch("app.services.watchdogs.get_settings")
    @patch("app.services.watchdogs.janus")
    @patch("app.services.watchdogs.get_ladder")
    def test_safe_mode_no_reset_before_threshold(self, mock_get_ladder, mock_janus, mock_settings, mock_mode):
        """When SAFE mode hasn't reached threshold, normal escalation happens."""
        from app.services.system_mode import SystemMode

        mock_settings.return_value = MagicMock(
            watchdog_enabled=True,
            janus_mount_id=1,
            watchdog_stale_ms=5000,
            watchdog_grace_sec=0,
            watchdog_interval_sec=1,
        )
        mock_janus.janus_summary.return_value = {"video_age_ms": 99999}

        ladder_inst = MagicMock()
        mock_get_ladder.return_value = ladder_inst

        mock_mode.SystemMode = SystemMode
        mock_mode.current_mode.return_value = SystemMode.SAFE
        mock_mode.mode_uptime_sec.return_value = 10  # way below threshold

        iteration = 0
        def _stop_after_one(_timeout=None):
            nonlocal iteration
            iteration += 1
            if iteration >= 1:
                watchdogs._stop_event.set()

        with patch.object(watchdogs._stop_event, "wait", side_effect=_stop_after_one):
            watchdogs._watchdog_loop()

        ladder_inst.reset.assert_not_called()
        ladder_inst.escalate.assert_called_once()
