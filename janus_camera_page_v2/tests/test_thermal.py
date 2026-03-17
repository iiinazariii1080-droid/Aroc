"""Tests for app/services/thermal.py — CPU thermal monitor.

Covers:
  - Temperature reading (success, missing, permission denied)
  - FPS profile file writing via set_fps_profile()
  - Thermal loop state transitions: normal → low → stop → resume
  - Hysteresis: no flapping when temp hovers near threshold
  - Threshold misconfiguration detection
  - System mode transitions triggered by thermal events
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("CAM_TYPE", "depth_camera")
os.environ.setdefault("CAM_ADMIN_TOKEN", "test-token")

from app.services import system_mode
from app.services.system_mode import SystemMode, current_mode
from app.services.thermal import (
    PROFILE_LOW,
    PROFILE_NORMAL,
    PROFILE_STOP,
    _thermal_loop,
    _thermal_stop,
    read_cpu_temp,
    set_fps_profile,
    start_thermal_monitor,
    stop_thermal_monitor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate():
    system_mode._reset_for_tests()
    _thermal_stop.clear()
    yield
    _thermal_stop.set()
    system_mode._reset_for_tests()


def _make_settings(
    tmp_path: Path,
    warn_c: float = 70.0,
    crit_c: float = 80.0,
    resume_c: float = 65.0,
    poll_sec: int = 1,
    temp_millideg: int | None = 55000,
):
    """Create a real Settings instance with thermal zone file."""
    from tests.conftest import make_test_settings

    tz = tmp_path / "thermal_zone0" / "temp"
    tz.parent.mkdir(parents=True, exist_ok=True)
    if temp_millideg is not None:
        tz.write_text(f"{temp_millideg}\n")

    fps_path = tmp_path / "fps_profile"

    return make_test_settings(
        tmp_path,
        thermal_zone_path=tz,
        thermal_warn_c=warn_c,
        thermal_crit_c=crit_c,
        thermal_resume_c=resume_c,
        thermal_poll_sec=poll_sec,
        fps_profile_path=fps_path,
    )


# ===================================================================
# 1. read_cpu_temp
# ===================================================================

class TestReadCpuTemp:

    def test_reads_temp_from_file(self, tmp_path):
        settings = _make_settings(tmp_path, temp_millideg=72500)
        with patch("app.services.thermal.get_settings", return_value=settings):
            temp = read_cpu_temp()
        assert temp == 72.5

    def test_returns_none_when_file_missing(self, tmp_path):
        settings = _make_settings(tmp_path, temp_millideg=55000)
        # Remove the file
        settings.thermal_zone_path.unlink()
        with patch("app.services.thermal.get_settings", return_value=settings):
            assert read_cpu_temp() is None

    def test_returns_none_on_permission_error(self, tmp_path):
        settings = _make_settings(tmp_path, temp_millideg=55000)
        with patch("app.services.thermal.get_settings", return_value=settings), \
             patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            assert read_cpu_temp() is None

    def test_returns_none_on_unexpected_error(self, tmp_path):
        settings = _make_settings(tmp_path, temp_millideg=55000)
        with patch("app.services.thermal.get_settings", return_value=settings), \
             patch.object(Path, "read_text", side_effect=ValueError("bad")):
            assert read_cpu_temp() is None


# ===================================================================
# 2. set_fps_profile
# ===================================================================

class TestSetFpsProfile:

    def test_writes_profile_to_file(self, tmp_path):
        from tests.conftest import make_test_settings
        fps_path = tmp_path / "fps_profile"
        settings = make_test_settings(tmp_path, fps_profile_path=fps_path)
        with patch("app.services.thermal.get_settings", return_value=settings), \
             patch("app.utils.fs.atomic_write_text") as mock_write:
            set_fps_profile("low")
            mock_write.assert_called_once_with(fps_path, "low\n")

    def test_logs_warning_on_write_failure(self, tmp_path):
        from tests.conftest import make_test_settings
        settings = make_test_settings(tmp_path, fps_profile_path=tmp_path / "fps_profile")
        with patch("app.services.thermal.get_settings", return_value=settings), \
             patch("app.utils.fs.atomic_write_text", side_effect=OSError("disk full")), \
             patch("app.services.thermal.logger") as mock_logger:
            set_fps_profile("stop")
            mock_logger.warning.assert_called()


# ===================================================================
# 3. Thermal loop state transitions
# ===================================================================

class TestThermalLoop:
    """Test the _thermal_loop state machine by controlling temperature reads."""

    def _run_loop_iterations(self, temps: list[int], settings, stop_after: int | None = None):
        """Run the thermal loop with a sequence of temperatures.

        Patches read_cpu_temp to return values from `temps` in order,
        then sets _thermal_stop to exit the loop.
        """
        call_count = 0
        max_calls = stop_after or len(temps)

        original_read = read_cpu_temp

        def fake_read():
            nonlocal call_count
            if call_count >= max_calls:
                _thermal_stop.set()
                return None
            val = temps[min(call_count, len(temps) - 1)]
            call_count += 1
            return val / 1000.0 if val > 1000 else float(val)

        # Stop after processing all temps
        def fake_wait(timeout=None):
            if call_count >= max_calls:
                _thermal_stop.set()
                return True
            return False

        _thermal_stop.clear()

        with patch("app.services.thermal.read_cpu_temp", side_effect=fake_read), \
             patch("app.services.thermal.get_settings", return_value=settings), \
             patch("app.services.thermal.set_fps_profile") as mock_set_fps, \
             patch.object(_thermal_stop, "wait", side_effect=fake_wait):
            try:
                _thermal_loop()
            except StopIteration:
                pass
            finally:
                _thermal_stop.set()

        return mock_set_fps

    def test_warn_threshold_triggers_degrade(self, tmp_path):
        """Temp >= warn_c should trigger system_mode.degrade()."""
        settings = _make_settings(tmp_path, warn_c=70, crit_c=80, resume_c=65)
        with patch("app.services.thermal.system_mode.degrade") as mock_deg:
            self._run_loop_iterations([75000], settings)  # 75°C
            mock_deg.assert_called_once()

    def test_crit_threshold_triggers_safe_mode(self, tmp_path):
        """Temp >= crit_c should transition to SAFE mode."""
        settings = _make_settings(tmp_path, warn_c=70, crit_c=80, resume_c=65)
        with patch("app.services.thermal.system_mode.transition") as mock_trans:
            self._run_loop_iterations([85000], settings)  # 85°C
            mock_trans.assert_called_once()
            assert mock_trans.call_args[0][0] == SystemMode.SAFE

    def test_resume_threshold_triggers_promote(self, tmp_path):
        """Temp dropping to <= resume_c should trigger promote to NOMINAL."""
        settings = _make_settings(tmp_path, warn_c=70, crit_c=80, resume_c=65)
        with patch("app.services.thermal.system_mode.promote") as mock_prom:
            # First go to warn (sets current_profile=LOW), then cool down
            self._run_loop_iterations([75000, 60000], settings, stop_after=2)
            mock_prom.assert_called_once()
            assert mock_prom.call_args[0][0] == SystemMode.NOMINAL

    def test_hysteresis_no_mode_change_between_thresholds(self, tmp_path):
        """Temp between resume_c and warn_c should not trigger mode changes."""
        settings = _make_settings(tmp_path, warn_c=70, crit_c=80, resume_c=65)
        # Start at 67°C — between resume(65) and warn(70), should stay NORMAL
        with patch("app.services.thermal.system_mode.degrade") as mock_deg, \
             patch("app.services.thermal.system_mode.transition") as mock_trans, \
             patch("app.services.thermal.system_mode.promote") as mock_prom:
            self._run_loop_iterations([67000], settings)
            mock_deg.assert_not_called()
            mock_trans.assert_not_called()
            mock_prom.assert_not_called()

    def test_none_temp_continues_loop(self, tmp_path):
        """When read_cpu_temp returns None, loop should continue without action."""
        settings = _make_settings(tmp_path, warn_c=70, crit_c=80, resume_c=65)

        call_count = 0

        def fake_read():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                _thermal_stop.set()
            return None

        _thermal_stop.clear()
        with patch("app.services.thermal.read_cpu_temp", side_effect=fake_read), \
             patch("app.services.thermal.get_settings", return_value=settings), \
             patch("app.services.thermal.set_fps_profile") as mock_fps, \
             patch.object(_thermal_stop, "wait", return_value=False):
            try:
                _thermal_loop()
            except StopIteration:
                pass
            finally:
                _thermal_stop.set()

        mock_fps.assert_not_called()

    # Mode transition tests consolidated into test_warn_threshold_triggers_degrade,
    # test_crit_threshold_triggers_safe_mode, and test_resume_threshold_triggers_promote above.


# ===================================================================
# 4. start / stop lifecycle
# ===================================================================

class TestThermalLifecycle:

    def test_start_skips_when_thermal_zone_missing(self, tmp_path):
        from tests.conftest import make_test_settings
        settings = make_test_settings(
            tmp_path, thermal_zone_path=tmp_path / "nonexistent" / "temp",
        )
        with patch("app.services.thermal.get_settings", return_value=settings):
            start_thermal_monitor()
            # Should not have started a thread (thermal zone missing)
            # The function just returns early

    def test_start_logs_misconfiguration(self, tmp_path):
        """Warn when resume_c >= warn_c (invalid thresholds).

        Uses Settings.__new__() to bypass __post_init__ validation since
        this test intentionally creates invalid thresholds to verify that
        the thermal monitor detects the misconfiguration at runtime.
        """
        from app.core.settings import Settings
        from tests.conftest import make_test_settings
        # Ensure thermal zone file exists so monitor doesn't skip
        tz = tmp_path / "thermal_zone0" / "temp"
        tz.parent.mkdir(parents=True, exist_ok=True)
        tz.write_text("55000\n")

        valid = make_test_settings(tmp_path, thermal_zone_path=tz)
        # Create a Settings with invalid thresholds bypassing __post_init__
        settings = Settings.__new__(Settings)
        for field_name in valid.__dataclass_fields__:
            object.__setattr__(settings, field_name, getattr(valid, field_name))
        object.__setattr__(settings, "thermal_warn_c", 70.0)
        object.__setattr__(settings, "thermal_crit_c", 60.0)
        object.__setattr__(settings, "thermal_resume_c", 75.0)
        with patch("app.services.thermal.get_settings", return_value=settings), \
             patch("app.services.thermal.logger") as mock_logger, \
             patch("app.services.thermal.threading.Thread"):
            start_thermal_monitor()
            mock_logger.error.assert_called()
            error_msg = mock_logger.error.call_args[0][0]
            assert "MISCONFIGURATION" in error_msg

    def test_stop_sets_event(self):
        _thermal_stop.clear()
        stop_thermal_monitor()
        assert _thermal_stop.is_set()
