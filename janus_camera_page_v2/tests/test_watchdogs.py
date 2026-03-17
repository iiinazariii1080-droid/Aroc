"""Tests for app/services/watchdogs.py — async Janus and snapshot watchdogs with FDIR ladder."""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import watchdogs
from tests.conftest import make_test_settings

# Shared tmp_path for settings construction in @patch-decorated tests
# that don't receive tmp_path as a fixture parameter.
_WATCHDOG_TMP = Path(tempfile.mkdtemp(prefix="watchdog_test_"))


def _wd_settings(**overrides):
    """Create real Settings for watchdog tests."""
    return make_test_settings(_WATCHDOG_TMP, **overrides)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ladder_mock():
    """Create a ladder mock with async escalate and sync reset/status."""
    ladder_inst = MagicMock()
    ladder_inst.escalate = AsyncMock()
    return ladder_inst


def _cancel_after(n: int):
    """Return an ``asyncio.sleep`` side-effect that cancels after *n* calls."""
    calls = 0

    async def _sleep(seconds):
        nonlocal calls
        calls += 1
        if calls >= n:
            raise asyncio.CancelledError
    return _sleep


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_watchdog_state():
    """Reset watchdog module-level state before each test."""
    watchdogs._last_janus_escalation_ts = 0.0
    watchdogs._STARTUP_TS = 0.0  # far in the past → no grace period
    watchdogs._janus_watchdog_task = None
    yield
    watchdogs._janus_watchdog_task = None


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------

class TestStartJanusWatchdog:
    @pytest.mark.asyncio
    @patch("app.services.watchdogs.asyncio.create_task")
    @patch("app.services.watchdogs.get_settings")
    async def test_disabled(self, mock_settings, mock_task):
        mock_settings.return_value = _wd_settings(watchdog_enabled=False)
        await watchdogs.start_janus_watchdog()
        mock_task.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.asyncio.create_task")
    @patch("app.services.watchdogs.get_settings")
    async def test_enabled_creates_task(self, mock_settings, mock_task):
        mock_settings.return_value = _wd_settings(watchdog_enabled=True)
        await watchdogs.start_janus_watchdog()
        mock_task.assert_called_once()



# ---------------------------------------------------------------------------
# Janus watchdog loop — core scenarios
# ---------------------------------------------------------------------------

class TestWatchdogLoop:
    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_stale_video_triggers_escalate(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        mock_summary.return_value = {"video_age_ms": 99999, "reachable": True}
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()

        ladder_inst.escalate.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_fresh_video_no_escalate(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0, watchdog_nominal_checks=999,
        )
        mock_summary.return_value = {"video_age_ms": 100, "reachable": True}
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()

        ladder_inst.escalate.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_exception_does_not_crash(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        mock_summary.side_effect = Exception("fail")
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()
        # Should not raise — the loop handles exceptions internally.

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_grace_period_suppresses_escalation(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=9999,
        )
        # Still within grace period: _STARTUP_TS is now.
        watchdogs._STARTUP_TS = time.time()
        mock_summary.return_value = {"video_age_ms": 99999, "reachable": True}
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()

        ladder_inst.escalate.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_healthy_streak_resets_ladder(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        """After WATCHDOG_NOMINAL_CHECKS consecutive healthy checks, ladder.reset() is called."""
        nominal_window = 3
        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=1,
            watchdog_grace_sec=0, watchdog_nominal_checks=nominal_window,
        )
        mock_summary.return_value = {"video_age_ms": 100, "reachable": True}
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(nominal_window)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()

        ladder_inst.reset.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_janus_domain_used_when_unreachable(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        """Domain.JANUS is used when reachable=False, Domain.PIPELINE when reachable=True."""
        from app.services.fdir_events import Domain

        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        mock_summary.return_value = {"video_age_ms": None, "reachable": False}
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()

        args, kwargs = ladder_inst.escalate.call_args
        assert args[1] == Domain.JANUS or kwargs.get("domain") == Domain.JANUS

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_retry_on_exception_success_skips_escalation(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        """If first check fails but retry succeeds, no escalation."""
        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        # First call fails, retry succeeds
        mock_summary.side_effect = [Exception("fail"), {"video_age_ms": 100, "reachable": True}]
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(2)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()

        ladder_inst.escalate.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.janus.janus_summary", new_callable=AsyncMock)
    @patch("app.services.watchdogs.get_settings")
    async def test_retry_on_exception_fail_triggers_escalation(
        self, mock_settings, mock_summary, mock_ladder,
    ):
        """If both first check and retry fail, escalation occurs."""
        mock_settings.return_value = _wd_settings(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5,
            watchdog_grace_sec=0,
        )
        mock_summary.side_effect = Exception("fail")
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(2)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._watchdog_loop()

        ladder_inst.escalate.assert_called_once()


# ---------------------------------------------------------------------------
# Snapshot watchdog
# ---------------------------------------------------------------------------

class TestStartSnapshotWatchdog:
    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_settings")
    async def test_disabled(self, mock_settings):
        mock_settings.return_value = _wd_settings(snapshot_watchdog_enabled=False)
        await watchdogs.start_snapshot_watchdog()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.asyncio.create_task")
    @patch("app.services.watchdogs.get_settings")
    async def test_enabled_creates_task(self, mock_settings, mock_task):
        mock_settings.return_value = _wd_settings(snapshot_watchdog_enabled=True)
        await watchdogs.start_snapshot_watchdog()
        mock_task.assert_called_once()


class TestSnapshotWatchdogLoop:
    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat", side_effect=FileNotFoundError)
    @patch("app.services.watchdogs.get_settings")
    async def test_missing_file_triggers_escalate(
        self, mock_settings, mock_stat, mock_ladder,
    ):
        mock_settings.return_value = _wd_settings(
            snapshot_path="/nonexistent.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat")
    @patch("app.services.watchdogs.get_settings")
    async def test_stale_snapshot_triggers_escalate(
        self, mock_settings, mock_stat, mock_ladder,
    ):
        mock_settings.return_value = _wd_settings(
            snapshot_path="/tmp/snap.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        mock_stat.return_value = MagicMock(st_mtime=time.time() - 60)
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat")
    @patch("app.services.watchdogs.get_settings")
    async def test_fresh_snapshot_no_escalate(
        self, mock_settings, mock_stat, mock_ladder,
    ):
        mock_settings.return_value = _wd_settings(
            snapshot_path="/tmp/snap.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        mock_stat.return_value = MagicMock(st_mtime=time.time())
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.get_ladder")
    @patch("app.services.watchdogs.os.stat", side_effect=FileNotFoundError)
    @patch("app.services.watchdogs.get_settings")
    async def test_dedup_suppresses_snapshot_escalation(
        self, mock_settings, mock_stat, mock_ladder,
    ):
        """Snapshot watchdog skips escalation if Janus watchdog already escalated recently."""
        mock_settings.return_value = _wd_settings(
            snapshot_path="/nonexistent.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
            watchdog_grace_sec=0,
        )
        ladder_inst = _make_ladder_mock()
        mock_ladder.return_value = ladder_inst
        # Simulate a recent Janus escalation within the dedup window.
        watchdogs._last_janus_escalation_ts = time.monotonic()

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_cancel_after(1)):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._snapshot_watchdog_loop()
        ladder_inst.escalate.assert_not_called()
