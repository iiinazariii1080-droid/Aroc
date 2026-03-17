"""Tests for shutdown sequence — task cancellation, resource cleanup, ordering.

Covers:
  - stop_janus_watchdog() cancels the async task
  - stop_janus_watchdog() handles None task (no-op)
  - stop_thermal_monitor() signals the stop event
  - shutdown_listener_executor() prevents further submissions
  - Registry reset clears watchdog task reference
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("CAM_TYPE", "depth_camera")
os.environ.setdefault("CAM_ADMIN_TOKEN", "test-token")

from app.services.watchdogs import (
    start_janus_watchdog,
    stop_janus_watchdog,
)
from app.services.thermal import _thermal_stop, stop_thermal_monitor
from app.services import system_mode


@pytest.fixture(autouse=True)
def _isolate():
    """Reset watchdog state before each test."""
    import app.services.watchdogs as _wd
    _wd._janus_watchdog_task = None
    _wd._STARTUP_TS = 0.0
    _wd._last_janus_escalation_ts = 0.0
    yield
    _wd._janus_watchdog_task = None


# ===================================================================
# 1. stop_janus_watchdog async task cancellation
# ===================================================================

class TestStopJanusWatchdog:

    @pytest.mark.asyncio
    async def test_cancels_running_task(self):
        """A running task is cancelled and awaited."""
        import app.services.watchdogs as _wd

        async def fake_loop():
            await asyncio.sleep(999)

        _wd._janus_watchdog_task = asyncio.create_task(fake_loop())
        await stop_janus_watchdog()
        assert _wd._janus_watchdog_task is None

    @pytest.mark.asyncio
    async def test_handles_none_task(self):
        """When no task was started, stop is a no-op."""
        import app.services.watchdogs as _wd
        _wd._janus_watchdog_task = None
        # Should not raise
        await stop_janus_watchdog()
        assert _wd._janus_watchdog_task is None

    @pytest.mark.asyncio
    async def test_clears_task_reference_after_cancel(self):
        """Task reference must be None after stop."""
        import app.services.watchdogs as _wd

        async def done_immediately():
            pass

        task = asyncio.create_task(done_immediately())
        await task  # let it finish
        _wd._janus_watchdog_task = task

        await stop_janus_watchdog()
        assert _wd._janus_watchdog_task is None


# ===================================================================
# 2. stop_thermal_monitor
# ===================================================================

class TestStopThermalMonitor:

    def test_sets_thermal_stop_event(self):
        _thermal_stop.clear()
        stop_thermal_monitor()
        assert _thermal_stop.is_set()


# ===================================================================
# 3. shutdown_listener_executor
# ===================================================================

class TestShutdownListenerExecutor:

    def test_executor_stopped_flag_set(self):
        import app.services.system_mode as sm
        original = sm._executor_stopped
        try:
            sm._executor_stopped = False
            sm.shutdown_listener_executor()
            assert sm._executor_stopped is True
        finally:
            # Restore — but executor is now shut down, which is expected
            sm._executor_stopped = original


# ===================================================================
# 4. Registry reset clears watchdog task
# ===================================================================

class TestRegistryResetWatchdog:

    def test_reset_clears_watchdog_task(self):
        import app.services.watchdogs as _wd
        _wd._janus_watchdog_task = MagicMock()  # simulate a "stale" reference

        from app.core.registry import ServiceRegistry
        ServiceRegistry.reset()

        assert _wd._janus_watchdog_task is None
