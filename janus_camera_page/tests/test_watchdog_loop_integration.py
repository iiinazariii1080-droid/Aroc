"""Integration tests for watchdog loop with fake clock.

Validates:
- TD-2: Watchdog loop integration (N healthy checks → ladder reset + promote)
- Grace period suppression
- First healthy frame ends grace early
- Dual watchdog dedup (only 1 escalation within window)
- Exception path: retry then escalate

Markers: unit, integration
"""
from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def _reset_watchdog_globals():
    """Reset watchdog module globals before each test."""
    import app.services.watchdogs as wd
    wd._first_healthy_seen = False
    wd._last_escalation_ts = 0.0
    wd._snapshot_missing_logged = False
    wd._stop_event = threading.Event()
    yield
    wd._stop_event.set()


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.watchdog_enabled = True
    settings.snapshot_watchdog_enabled = True
    settings.watchdog_interval_sec = 0  # no sleep in tests
    settings.watchdog_stale_ms = 10000
    settings.watchdog_grace_sec = 60
    settings.janus_mount_id = 1305
    settings.snapshot_path = "/tmp/test_snapshot.jpg"
    return settings


def _make_summary(age_ms=5000, status="ok"):
    """Create a janus_summary-like response."""
    return {
        "mountpoint_id": 1305,
        "enabled": True,
        "video_active": age_ms is not None,
        "video_age_ms": age_ms,
        "codec": "h264",
        "pt": 96,
        "fmtp": None,
        "status": status,
    }


class TestWatchdogHealthyStreak:
    """N consecutive healthy checks → ladder reset + mode promote."""

    def test_healthy_streak_resets_ladder(self, mock_settings):
        import app.services.watchdogs as wd

        mock_ladder = MagicMock()
        iteration = [0]
        max_iters = 12

        def fake_summary(mount_id):
            return _make_summary(age_ms=500)  # healthy

        def fake_wait(timeout):
            iteration[0] += 1
            if iteration[0] >= max_iters:
                wd._stop_event.set()
            return False

        with patch("app.services.watchdogs.get_settings", return_value=mock_settings), \
             patch("app.services.watchdogs.get_ladder", return_value=mock_ladder), \
             patch("app.services.watchdogs.janus") as mock_janus, \
             patch("app.services.watchdogs.time") as mock_time, \
             patch.object(wd._stop_event, "wait", side_effect=fake_wait), \
             patch.object(wd._stop_event, "is_set", side_effect=lambda: iteration[0] >= max_iters), \
             patch("app.services.watchdogs.system_mode") as mock_sm:

            mock_janus.janus_summary = fake_summary
            mock_time.monotonic = MagicMock(return_value=100.0)  # past grace period

            wd._STARTUP_TS = 0.0  # ensure grace period is over
            wd._watchdog_loop()

        # After 10+ healthy checks, ladder.reset() and promote should be called
        mock_ladder.reset.assert_called()
        mock_sm.promote.assert_called()

    def test_stale_then_healthy_resets_streak(self, mock_settings):
        """Interleaved stale/healthy checks never reach the streak threshold."""
        import app.services.watchdogs as wd

        mock_ladder = MagicMock()
        iteration = [0]
        max_iters = 20

        responses = []
        for i in range(max_iters):
            if i % 2 == 0:
                responses.append(_make_summary(age_ms=500))   # healthy
            else:
                responses.append(_make_summary(age_ms=20000))  # stale

        def fake_summary(mount_id):
            idx = min(iteration[0], len(responses) - 1)
            return responses[idx]

        def fake_wait(timeout):
            iteration[0] += 1
            if iteration[0] >= max_iters:
                wd._stop_event.set()
            return False

        with patch("app.services.watchdogs.get_settings", return_value=mock_settings), \
             patch("app.services.watchdogs.get_ladder", return_value=mock_ladder), \
             patch("app.services.watchdogs.janus") as mock_janus, \
             patch("app.services.watchdogs.time") as mock_time, \
             patch.object(wd._stop_event, "wait", side_effect=fake_wait), \
             patch.object(wd._stop_event, "is_set", side_effect=lambda: iteration[0] >= max_iters), \
             patch("app.services.watchdogs.system_mode"):

            mock_janus.janus_summary = fake_summary
            mock_time.monotonic = MagicMock(return_value=100.0)

            wd._STARTUP_TS = 0.0
            wd._watchdog_loop()

        # Streak never reaches 10, so ladder.reset() should NOT be called
        mock_ladder.reset.assert_not_called()


class TestGracePeriod:
    """Grace period suppresses escalation."""

    def test_grace_period_skips_escalation(self, mock_settings):
        import app.services.watchdogs as wd

        mock_ladder = MagicMock()
        iteration = [0]
        max_iters = 3

        def fake_summary(mount_id):
            return _make_summary(age_ms=None)  # stale

        def fake_wait(timeout):
            iteration[0] += 1
            if iteration[0] >= max_iters:
                wd._stop_event.set()
            return False

        with patch("app.services.watchdogs.get_settings", return_value=mock_settings), \
             patch("app.services.watchdogs.get_ladder", return_value=mock_ladder), \
             patch("app.services.watchdogs.janus") as mock_janus, \
             patch("app.services.watchdogs.time") as mock_time, \
             patch.object(wd._stop_event, "wait", side_effect=fake_wait), \
             patch.object(wd._stop_event, "is_set", side_effect=lambda: iteration[0] >= max_iters), \
             patch("app.services.watchdogs.system_mode"):

            mock_janus.janus_summary = fake_summary
            # monotonic = 10 means we're still in grace period (grace_sec=60)
            mock_time.monotonic = MagicMock(return_value=10.0)

            wd._STARTUP_TS = 0.0
            wd._first_healthy_seen = False
            wd._watchdog_loop()

        # No escalation during grace period
        mock_ladder.escalate.assert_not_called()


class TestFirstHealthyEndsGrace:
    """First healthy frame sets _first_healthy_seen → ends grace early."""

    def test_first_healthy_seen_flag(self, mock_settings):
        import app.services.watchdogs as wd

        mock_ladder = MagicMock()
        iteration = [0]
        max_iters = 2

        def fake_summary(mount_id):
            return _make_summary(age_ms=500)  # healthy

        def fake_wait(timeout):
            iteration[0] += 1
            if iteration[0] >= max_iters:
                wd._stop_event.set()
            return False

        with patch("app.services.watchdogs.get_settings", return_value=mock_settings), \
             patch("app.services.watchdogs.get_ladder", return_value=mock_ladder), \
             patch("app.services.watchdogs.janus") as mock_janus, \
             patch("app.services.watchdogs.time") as mock_time, \
             patch.object(wd._stop_event, "wait", side_effect=fake_wait), \
             patch.object(wd._stop_event, "is_set", side_effect=lambda: iteration[0] >= max_iters), \
             patch("app.services.watchdogs.system_mode"):

            mock_janus.janus_summary = fake_summary
            mock_time.monotonic = MagicMock(return_value=5.0)  # within grace period

            wd._STARTUP_TS = 0.0
            wd._first_healthy_seen = False
            wd._watchdog_loop()

        assert wd._first_healthy_seen is True


class TestDualWatchdogDedup:
    """Dedup: two escalations within 5s window → only first takes effect."""

    def test_dedup_prevents_second_escalation(self, mock_settings):
        import app.services.watchdogs as wd

        mock_ladder = MagicMock()
        mono_time = [100.0]

        with patch("app.services.watchdogs.time") as mock_time:
            mock_time.monotonic = MagicMock(side_effect=lambda: mono_time[0])

            # First escalation succeeds
            result1 = wd._try_escalate(mock_ladder, "test_signal_1", MagicMock())
            assert result1 is True
            assert mock_ladder.escalate.call_count == 1

            # Second within dedup window (< 5s) — should be rejected
            mono_time[0] = 103.0  # 3 seconds later
            result2 = wd._try_escalate(mock_ladder, "test_signal_2", MagicMock())
            assert result2 is False
            assert mock_ladder.escalate.call_count == 1  # still 1

            # Third after dedup window — should succeed
            mono_time[0] = 106.0  # 6 seconds after first
            result3 = wd._try_escalate(mock_ladder, "test_signal_3", MagicMock())
            assert result3 is True
            assert mock_ladder.escalate.call_count == 2


class TestExceptionRetryEscalate:
    """Exception in summary → retry after backoff → escalate if still failing."""

    def test_exception_triggers_retry_then_escalate(self, mock_settings):
        import app.services.watchdogs as wd

        mock_ladder = MagicMock()
        iteration = [0]
        max_iters = 1

        def fake_wait(timeout):
            iteration[0] += 1
            if iteration[0] >= max_iters:
                wd._stop_event.set()
            return False

        with patch("app.services.watchdogs.get_settings", return_value=mock_settings), \
             patch("app.services.watchdogs.get_ladder", return_value=mock_ladder), \
             patch("app.services.watchdogs.janus") as mock_janus, \
             patch("app.services.watchdogs.time") as mock_time, \
             patch.object(wd._stop_event, "wait", side_effect=fake_wait), \
             patch.object(wd._stop_event, "is_set", side_effect=lambda: iteration[0] >= max_iters), \
             patch("app.services.watchdogs.system_mode"):

            # Both calls fail
            mock_janus.janus_summary = MagicMock(side_effect=RuntimeError("janus down"))
            mock_time.monotonic = MagicMock(return_value=100.0)
            mock_time.sleep = MagicMock()  # don't actually sleep

            wd._STARTUP_TS = 0.0
            wd._watchdog_loop()

        # Should have retried (time.sleep called) and then escalated
        mock_time.sleep.assert_called()
        mock_ladder.escalate.assert_called()
