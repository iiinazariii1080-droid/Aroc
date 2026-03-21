"""Tests for mode_enforcer — subprocess handling, verification, path config.

Validates that:
- systemctl stop/start use check=True and raise on failure
- Post-stop verification detects still-active services
- FPS profile and mode history use Settings paths
- FDIR events are emitted on failures

Markers: unit
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.services.mode_enforcer import (
    _clear_fps_profile,
    _ensure_pipeline_running,
    _is_service_active,
    _on_mode_transition,
    _persist_history,
    _stop_pipeline,
    _write_fps_profile,
)
from app.services.system_mode import SystemMode


SERVICE = "rtp-rgb@cam-rgb.service"


@pytest.fixture(autouse=True)
def _mock_settings(tmp_path):
    """Provide Settings with temp paths for fps_profile and mode_history."""
    settings = MagicMock()
    settings.service_name = SERVICE
    settings.fps_profile_path = tmp_path / "fps_profile"
    settings.mode_history_path = tmp_path / "mode_history.json"
    with patch("app.services.mode_enforcer.get_settings", return_value=settings):
        yield settings


@pytest.fixture(autouse=True)
def _mock_emit():
    with patch("app.services.mode_enforcer.emit") as mock:
        yield mock


class TestStopPipeline:
    """_stop_pipeline uses check=True and verifies service stopped."""

    def test_successful_stop(self, _mock_emit):
        with patch("app.services.mode_enforcer.subprocess.run") as run_mock, \
             patch("app.services.mode_enforcer._is_service_active", side_effect=[True, False]):
            _stop_pipeline(SERVICE, "test_reason")

        run_mock.assert_called_once()
        args = run_mock.call_args
        assert args.kwargs["check"] is True
        assert args.kwargs["capture_output"] is True
        # FDIR event emitted with success outcome
        _mock_emit.assert_called_once()
        assert "stopped" in _mock_emit.call_args.kwargs["outcome"]

    def test_stop_fails_with_called_process_error(self, _mock_emit):
        exc = subprocess.CalledProcessError(1, "systemctl", stderr=b"unit not found")
        with patch("app.services.mode_enforcer.subprocess.run", side_effect=exc), \
             patch("app.services.mode_enforcer._is_service_active", side_effect=[True, True]):
            _stop_pipeline(SERVICE, "test_reason")

        # FDIR event should report failure (service still active)
        _mock_emit.assert_called_once()
        assert "FAILED" in _mock_emit.call_args.kwargs["outcome"]

    def test_stop_timeout(self, _mock_emit):
        with patch("app.services.mode_enforcer.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("cmd", 30)), \
             patch("app.services.mode_enforcer._is_service_active", side_effect=[True, True]):
            _stop_pipeline(SERVICE, "test_reason")

        _mock_emit.assert_called_once()
        assert "FAILED" in _mock_emit.call_args.kwargs["outcome"]

    def test_already_stopped_skips(self, _mock_emit):
        with patch("app.services.mode_enforcer._is_service_active", return_value=False):
            _stop_pipeline(SERVICE, "test_reason")

        _mock_emit.assert_not_called()

    def test_safety_violation_logged_when_still_active(self, _mock_emit):
        """If service is still active after stop, emit CRITICAL FDIR event."""
        with patch("app.services.mode_enforcer.subprocess.run"), \
             patch("app.services.mode_enforcer._is_service_active", side_effect=[True, True]):
            _stop_pipeline(SERVICE, "test_reason")

        _mock_emit.assert_called_once()
        assert _mock_emit.call_args.kwargs["severity"].value.upper() == "CRITICAL"
        assert "still active" in _mock_emit.call_args.kwargs["outcome"]


class TestEnsurePipelineRunning:
    """_ensure_pipeline_running uses check=True."""

    def test_starts_when_inactive(self, _mock_emit):
        with patch("app.services.mode_enforcer.subprocess.run") as run_mock, \
             patch("app.services.mode_enforcer._is_service_active", return_value=False):
            _ensure_pipeline_running(SERVICE, "test_reason")

        run_mock.assert_called_once()
        assert run_mock.call_args.kwargs["check"] is True
        _mock_emit.assert_called_once()

    def test_skips_when_already_active(self, _mock_emit):
        with patch("app.services.mode_enforcer._is_service_active", return_value=True):
            _ensure_pipeline_running(SERVICE, "test_reason")

        _mock_emit.assert_not_called()

    def test_start_failure_logged(self, _mock_emit):
        exc = subprocess.CalledProcessError(1, "systemctl", stderr=b"failed")
        with patch("app.services.mode_enforcer.subprocess.run", side_effect=exc), \
             patch("app.services.mode_enforcer._is_service_active", return_value=False):
            _ensure_pipeline_running(SERVICE, "test_reason")

        # Still emits event (best-effort)
        _mock_emit.assert_called_once()


class TestOnModeTransition:
    """_on_mode_transition dispatches correctly for each mode."""

    def test_safe_mode_stops_pipeline(self, _mock_emit):
        with patch("app.services.mode_enforcer._stop_pipeline") as stop, \
             patch("app.services.mode_enforcer._persist_history"):
            _on_mode_transition(SystemMode.NOMINAL, SystemMode.SAFE, "test")
        stop.assert_called_once_with(SERVICE, "test")

    def test_nominal_mode_starts_and_clears(self, _mock_emit):
        with patch("app.services.mode_enforcer._ensure_pipeline_running") as start, \
             patch("app.services.mode_enforcer._clear_fps_profile") as clear, \
             patch("app.services.mode_enforcer._persist_history"):
            _on_mode_transition(SystemMode.SAFE, SystemMode.NOMINAL, "test")
        start.assert_called_once()
        clear.assert_called_once()

    def test_degraded_mode_writes_profile_and_starts(self, _mock_emit):
        with patch("app.services.mode_enforcer._write_fps_profile") as write, \
             patch("app.services.mode_enforcer._ensure_pipeline_running") as start, \
             patch("app.services.mode_enforcer._persist_history"):
            _on_mode_transition(SystemMode.NOMINAL, SystemMode.DEGRADED, "test")
        write.assert_called_once()
        start.assert_called_once()


class TestWriteFpsProfile:
    """_write_fps_profile creates correct JSON structure."""

    def test_writes_degraded_profile(self, _mock_settings):
        _write_fps_profile(SystemMode.DEGRADED, 15, 1500)
        data = json.loads(_mock_settings.fps_profile_path.read_text())
        assert data["profile"] == "low"
        assert data["max_fps"] == 15
        assert data["max_bitrate_kbps"] == 1500
        assert data["mode"].upper() == "DEGRADED"
        assert "ts" in data

    def test_writes_local_only_profile(self, _mock_settings):
        _write_fps_profile(SystemMode.LOCAL_ONLY, 15, 2000)
        data = json.loads(_mock_settings.fps_profile_path.read_text())
        assert data["profile"] == "local"


class TestClearFpsProfile:
    """_clear_fps_profile removes the signal file."""

    def test_clears_existing_file(self, _mock_settings):
        _mock_settings.fps_profile_path.write_text("{}")
        _clear_fps_profile()
        assert not _mock_settings.fps_profile_path.exists()

    def test_noop_when_missing(self, _mock_settings):
        _clear_fps_profile()  # Should not raise


class TestPersistHistory:
    """_persist_history maintains a ring buffer."""

    def test_appends_entry(self, _mock_settings):
        _persist_history(SystemMode.NOMINAL, SystemMode.DEGRADED, "test")
        data = json.loads(_mock_settings.mode_history_path.read_text())
        assert len(data) == 1
        assert data[0]["from"].upper() == "NOMINAL"
        assert data[0]["to"].upper() == "DEGRADED"

    def test_ring_buffer_max_50(self, _mock_settings):
        # Pre-fill with 50 entries
        existing = [{"from": "A", "to": "B", "reason": "x", "ts": 0}] * 50
        _mock_settings.mode_history_path.write_text(json.dumps(existing))

        _persist_history(SystemMode.NOMINAL, SystemMode.SAFE, "overflow")

        data = json.loads(_mock_settings.mode_history_path.read_text())
        assert len(data) == 50
        assert data[-1]["to"].upper() == "SAFE"

    def test_handles_corrupt_history(self, _mock_settings):
        _mock_settings.mode_history_path.write_text("not json")
        _persist_history(SystemMode.NOMINAL, SystemMode.DEGRADED, "test")
        data = json.loads(_mock_settings.mode_history_path.read_text())
        assert len(data) == 1
