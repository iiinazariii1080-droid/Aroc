"""Tests for app/services/watchdogs.py — Janus and snapshot watchdogs."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from app.services import watchdogs


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
    @patch("app.services.watchdogs.time.sleep", side_effect=StopIteration)
    @patch("app.services.watchdogs.service_restart")
    @patch("app.services.watchdogs.janus.janus_summary")
    @patch("app.services.watchdogs.get_settings")
    def test_stale_video_triggers_restart(self, mock_settings, mock_summary, mock_restart, _):
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5
        )
        mock_summary.return_value = {"video_age_ms": 99999}
        watchdogs._last_restart = 0.0
        with pytest.raises(StopIteration):
            watchdogs._watchdog_loop()
        mock_restart.assert_called_once()

    @patch("app.services.watchdogs.time.sleep", side_effect=StopIteration)
    @patch("app.services.watchdogs.service_restart")
    @patch("app.services.watchdogs.janus.janus_summary")
    @patch("app.services.watchdogs.get_settings")
    def test_fresh_video_no_restart(self, mock_settings, mock_summary, mock_restart, _):
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5
        )
        mock_summary.return_value = {"video_age_ms": 100}
        with pytest.raises(StopIteration):
            watchdogs._watchdog_loop()
        mock_restart.assert_not_called()

    @patch("app.services.watchdogs.time.sleep", side_effect=StopIteration)
    @patch("app.services.watchdogs.janus.janus_summary", side_effect=Exception("fail"))
    @patch("app.services.watchdogs.get_settings")
    def test_exception_does_not_crash(self, mock_settings, mock_summary, _):
        mock_settings.return_value = MagicMock(
            janus_mount_id=1, watchdog_stale_ms=5000, watchdog_interval_sec=5
        )
        with pytest.raises(StopIteration):
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
    @patch("app.services.watchdogs.service_restart")
    @patch("app.services.watchdogs.os.stat", side_effect=FileNotFoundError)
    @patch("app.services.watchdogs.get_settings")
    async def test_missing_file_triggers_restart(self, mock_settings, mock_stat, mock_restart):
        mock_settings.return_value = MagicMock(
            snapshot_path="/nonexistent.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
        )
        call_count = 0
        original_sleep = asyncio.sleep

        async def _one_shot(sec):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError
            await original_sleep(0)

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_one_shot):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._snapshot_watchdog_loop()
        mock_restart.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.watchdogs.service_restart")
    @patch("app.services.watchdogs.os.stat")
    @patch("app.services.watchdogs.get_settings")
    async def test_stale_snapshot_triggers_restart(self, mock_settings, mock_stat, mock_restart):
        mock_settings.return_value = MagicMock(
            snapshot_path="/tmp/snap.jpg",
            watchdog_stale_ms=5000,
            watchdog_interval_sec=1,
        )
        mock_stat.return_value = MagicMock(st_mtime=time.time() - 60)

        async def _one_shot(sec):
            raise asyncio.CancelledError

        with patch("app.services.watchdogs.asyncio.sleep", side_effect=_one_shot):
            with pytest.raises(asyncio.CancelledError):
                await watchdogs._snapshot_watchdog_loop()
        mock_restart.assert_called_once()
