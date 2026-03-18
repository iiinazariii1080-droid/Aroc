"""Tests for shared.healthcheck — file-based health check."""

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.healthcheck import check, start_heartbeat, touch


def _patch_healthcheck(tmp_path):
    """Context manager that patches both HEALTHCHECK_FILE and _HEALTHCHECK_PATH."""
    hc_file = str(tmp_path / "healthy")
    return patch.multiple(
        "shared.healthcheck",
        HEALTHCHECK_FILE=hc_file,
        _HEALTHCHECK_PATH=Path(hc_file),
    ), hc_file


class TestTouch:
    def test_touch_creates_file(self, tmp_path):
        ctx, hc_file = _patch_healthcheck(tmp_path)
        with ctx:
            touch()
            assert os.path.exists(hc_file)

    def test_touch_updates_mtime(self, tmp_path):
        ctx, hc_file = _patch_healthcheck(tmp_path)
        with ctx:
            touch()
            mtime = os.path.getmtime(hc_file)
            assert mtime > 0


class TestCheck:
    def test_check_exits_if_file_missing(self, tmp_path):
        hc_file = str(tmp_path / "nonexistent")
        with patch.multiple(
            "shared.healthcheck",
            HEALTHCHECK_FILE=hc_file,
            _HEALTHCHECK_PATH=Path(hc_file),
        ):
            with pytest.raises(SystemExit):
                check()

    def test_check_exits_if_file_stale(self, tmp_path):
        ctx, hc_file = _patch_healthcheck(tmp_path)
        with ctx:
            # Create a file and set mtime to 100 seconds ago
            Path(hc_file).touch()
            old_time = time.time() - 100
            os.utime(hc_file, (old_time, old_time))
            with pytest.raises(SystemExit):
                check(max_age=30)

    def test_check_passes_if_file_recent(self, tmp_path):
        ctx, hc_file = _patch_healthcheck(tmp_path)
        with ctx:
            Path(hc_file).touch()
            # Should not raise — file was just created
            check(max_age=30)


class TestStartHeartbeat:
    def test_heartbeat_creates_file(self, tmp_path):
        ctx, hc_file = _patch_healthcheck(tmp_path)
        shutdown = threading.Event()
        with ctx:
            start_heartbeat(shutdown, interval=0.1)
            time.sleep(0.3)
            assert os.path.exists(hc_file)
            shutdown.set()

    def test_heartbeat_removes_file_when_unhealthy(self, tmp_path):
        ctx, hc_file = _patch_healthcheck(tmp_path)
        shutdown = threading.Event()
        with ctx:
            start_heartbeat(shutdown, interval=0.1, is_healthy=lambda: False)
            time.sleep(0.3)
            assert not os.path.exists(hc_file)
            shutdown.set()

    def test_heartbeat_stops_on_shutdown(self, tmp_path):
        ctx, hc_file = _patch_healthcheck(tmp_path)
        shutdown = threading.Event()
        with ctx:
            start_heartbeat(shutdown, interval=0.1)
            time.sleep(0.2)
            shutdown.set()
            time.sleep(0.3)
            # File should be cleaned up
            assert not os.path.exists(hc_file)
