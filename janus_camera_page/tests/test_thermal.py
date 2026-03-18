"""Tests for the thermal monitoring module."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services import thermal


class TestReadCpuTemp:
    """Tests for read_cpu_temp()."""

    def test_reads_millidegrees(self, tmp_path: Path):
        zone = tmp_path / "temp"
        zone.write_text("72500\n")
        with patch.object(thermal, "THERMAL_ZONE", zone):
            assert thermal.read_cpu_temp() == 72.5

    def test_returns_none_when_file_missing(self, tmp_path: Path):
        zone = tmp_path / "nonexistent"
        with patch.object(thermal, "THERMAL_ZONE", zone):
            assert thermal.read_cpu_temp() is None

    def test_returns_none_on_permission_error(self, tmp_path: Path):
        zone = tmp_path / "temp"
        zone.write_text("50000")
        zone.chmod(0o000)
        try:
            with patch.object(thermal, "THERMAL_ZONE", zone):
                assert thermal.read_cpu_temp() is None
        finally:
            zone.chmod(0o644)


class TestSetFpsProfile:
    """Tests for set_fps_profile()."""

    def test_writes_profile(self, tmp_path: Path):
        profile_path = tmp_path / "fps_profile"
        with patch.object(thermal, "FPS_PROFILE_PATH", profile_path):
            thermal.set_fps_profile("low")
        assert profile_path.read_text() == "low\n"

    def test_creates_parent_dir(self, tmp_path: Path):
        profile_path = tmp_path / "subdir" / "fps_profile"
        with patch.object(thermal, "FPS_PROFILE_PATH", profile_path):
            thermal.set_fps_profile("normal")
        assert profile_path.read_text() == "normal\n"


class TestThermalLoop:
    """Tests for the thermal state machine (thresholds & hysteresis)."""

    def _run_loop_iterations(self, temps: list[float], tmp_path: Path) -> list[str]:
        """Run thermal loop for len(temps) iterations, return profile written each step."""
        zone_file = tmp_path / "temp"
        profile_file = tmp_path / "fps_profile"
        profiles_written: list[str] = []
        call_count = 0

        stop_event = thermal._stop_event
        stop_event.clear()

        def fake_wait(_timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count >= len(temps):
                stop_event.set()  # signal loop to exit
                return True
            # Write next temperature
            zone_file.write_text(f"{int(temps[call_count] * 1000)}\n")
            return False

        # Write initial temp
        zone_file.write_text(f"{int(temps[0] * 1000)}\n")

        def track_profile(profile):
            profiles_written.append(profile)
            profile_file.parent.mkdir(parents=True, exist_ok=True)
            profile_file.write_text(profile + "\n")

        with patch.object(thermal, "THERMAL_ZONE", zone_file), \
             patch.object(thermal, "FPS_PROFILE_PATH", profile_file), \
             patch.object(thermal, "set_fps_profile", side_effect=track_profile), \
             patch("app.services.thermal.system_mode") as mock_mode, \
             patch("app.services.thermal.emit") as mock_emit, \
             patch.object(stop_event, "wait", side_effect=fake_wait):
            thermal._thermal_loop()

        stop_event.clear()  # reset for next test
        return profiles_written

    def test_warn_threshold_triggers_low_profile(self, tmp_path: Path):
        # Normal → Warn (70°C)
        profiles = self._run_loop_iterations([71.0, 71.0], tmp_path)
        assert "low" in profiles

    def test_critical_threshold_triggers_stop(self, tmp_path: Path):
        # Normal → Critical (80°C)
        profiles = self._run_loop_iterations([81.0, 81.0], tmp_path)
        assert "stop" in profiles

    def test_resume_threshold_triggers_normal(self, tmp_path: Path):
        # Force into low state, then cool down
        profiles = self._run_loop_iterations([71.0, 64.0, 64.0], tmp_path)
        assert "low" in profiles
        assert "normal" in profiles

    def test_hysteresis_prevents_flapping(self, tmp_path: Path):
        # Warn at 71, then 67 (above resume threshold 65) — should NOT resume
        profiles = self._run_loop_iterations([71.0, 67.0, 67.0], tmp_path)
        assert "low" in profiles
        assert "normal" not in profiles


class TestStartThermalMonitor:
    """Tests for start_thermal_monitor()."""

    def test_does_not_start_when_zone_missing(self, tmp_path: Path):
        zone = tmp_path / "nonexistent"
        with patch.object(thermal, "THERMAL_ZONE", zone):
            thermal.start_thermal_monitor()
            # No thread started — no error
            # Verify no daemon thread was spawned
            thermal_threads = [
                t for t in threading.enumerate() if t.name == "thermal-monitor-test"
            ]
            assert len(thermal_threads) == 0

    def test_starts_daemon_thread_when_zone_exists(self, tmp_path: Path):
        zone = tmp_path / "temp"
        zone.write_text("50000\n")
        with patch.object(thermal, "THERMAL_ZONE", zone), \
             patch.object(thermal, "_thermal_loop"):
            thermal.start_thermal_monitor()
