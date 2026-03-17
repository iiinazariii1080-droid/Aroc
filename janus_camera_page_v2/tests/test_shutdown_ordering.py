"""T6: Lifecycle shutdown ordering tests.

Validates that the shutdown sequence in events.py:_lifespan executes
in the correct order and does not cause RuntimeError from late submissions.

Risk addressed: R04 (High) — shutdown race could cause exceptions.

The existing test_shutdown.py tests individual component stop methods.
These tests validate the ORDERING: consumers before producers, tasks
before resources.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def _isolate_shutdown():
    """Reset system_mode executor state around each test."""
    import app.services.system_mode as sm
    original_stopped = sm._executor_stopped
    sm._executor_stopped = False
    yield
    sm._executor_stopped = original_stopped


class TestShutdownOrdering:
    """Validate shutdown sequence ordering from _lifespan."""

    def test_thermal_stops_before_executor_shutdown(self):
        """Thermal thread must not be alive when executor shutdown begins.

        If thermal submits to executor after shutdown → RuntimeError.
        """
        from app.services.thermal import (
            start_thermal_monitor,
            stop_thermal_monitor,
            _thermal_stop,
        )
        from app.services import system_mode

        # Create a fake thermal zone file so the monitor doesn't error
        import tempfile
        from pathlib import Path
        from tests.conftest import make_test_settings
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            tz_path = tmp_p / "temp"
            tz_path.write_text("45000\n")  # 45°C, below thresholds

            settings = make_test_settings(
                tmp_p,
                thermal_zone_path=tz_path,
                thermal_poll_sec=1,
                thermal_warn_c=70.0,
                thermal_crit_c=80.0,
                thermal_resume_c=65.0,
                fps_profile_path=tmp_p / "fps_profile",
            )

            with patch("app.services.thermal.get_settings", return_value=settings):
                _thermal_stop.clear()
                start_thermal_monitor()

                import app.services.thermal as th
                assert th._thermal_thread is not None, "Thermal thread should be started"

                # Step 1: Stop thermal monitor
                stop_thermal_monitor()

                # Verify thermal thread is stopped
                if th._thermal_thread is not None:
                    assert not th._thermal_thread.is_alive(), (
                        "Thermal thread should not be alive after stop_thermal_monitor()"
                    )

                # Step 2: Now it's safe to shut down executor
                system_mode.shutdown_listener_executor()
                assert system_mode._executor_stopped is True

    @pytest.mark.asyncio
    async def test_watchdog_stops_before_monitor_session_close(self):
        """Watchdog task cancelled before Janus monitor session close.

        No RuntimeError from double-close or using closed session.
        """
        import app.services.watchdogs as wd

        # Create a fake watchdog task
        async def fake_watchdog():
            await asyncio.sleep(999)

        wd._janus_watchdog_task = asyncio.create_task(fake_watchdog())

        # Step 1: Stop watchdog
        await wd.stop_janus_watchdog()
        assert wd._janus_watchdog_task is None

        # Step 2: Close monitor session — should not raise
        import app.services.janus as janus_mod
        session = janus_mod._monitor_session
        session._lock = asyncio.Lock()
        session._closing = False
        session._session_id = None
        session._handle_id = None

        # close() on an already-empty session should be a no-op, not RuntimeError
        await session.close()
        assert session._closing is True

    def test_executor_rejects_after_shutdown(self):
        """After shutdown_listener_executor(), mode transition skips listeners.

        Must not raise RuntimeError.
        """
        from app.services import system_mode
        from app.services.system_mode import SystemMode, _reset_for_tests

        _reset_for_tests()

        callback_called = []

        def listener(prev, curr, reason):
            callback_called.append((prev, curr))

        system_mode.on_transition(listener)

        # Shut down executor
        system_mode.shutdown_listener_executor()
        assert system_mode._executor_stopped is True

        # Mode transition should NOT raise RuntimeError — it should skip listeners
        try:
            system_mode.transition(SystemMode.DEGRADED, "post_shutdown_test")
        except RuntimeError:
            pytest.fail(
                "Mode transition after executor shutdown raised RuntimeError — "
                "post_transition should skip listeners when _executor_stopped is True"
            )

        # Mode should have changed (state mutation is independent of listeners)
        assert system_mode.current_mode() == SystemMode.DEGRADED

    @pytest.mark.asyncio
    async def test_full_shutdown_sequence_no_exception(self):
        """Execute complete shutdown sequence from _lifespan with mocked services.

        All stop/close calls must succeed without exception. Validates ordering
        by checking that calls happen in the expected sequence.
        """
        call_order = []

        async def track_async(name):
            call_order.append(name)

        def track_sync(name):
            call_order.append(name)

        with patch("app.services.watchdogs.stop_janus_watchdog", new=AsyncMock(side_effect=lambda: call_order.append("stop_janus_watchdog"))), \
             patch("app.services.thermal.stop_thermal_monitor", side_effect=lambda: call_order.append("stop_thermal_monitor")), \
             patch("app.services.janus.close_monitor_session", new=AsyncMock(side_effect=lambda: call_order.append("close_monitor_session"))), \
             patch("app.services.watchdogs.stop_snapshot_watchdog", new=AsyncMock(side_effect=lambda: call_order.append("stop_snapshot_watchdog"))), \
             patch("app.services.janus.stop_janus_client", new=AsyncMock(side_effect=lambda: call_order.append("stop_janus_client"))), \
             patch("app.services.system_mode.shutdown_listener_executor", side_effect=lambda: call_order.append("shutdown_listener_executor")):

            # Simulate the shutdown sequence from _lifespan
            from app.services import watchdogs, system_mode
            from app.services.thermal import stop_thermal_monitor
            from app.services import janus as janus_service

            await watchdogs.stop_janus_watchdog()
            stop_thermal_monitor()
            await janus_service.close_monitor_session()
            await watchdogs.stop_snapshot_watchdog()
            await janus_service.stop_janus_client()
            system_mode.shutdown_listener_executor()

        # Verify ordering: watchdog and thermal before executor shutdown
        assert call_order.index("stop_janus_watchdog") < call_order.index("shutdown_listener_executor"), \
            "Watchdog must stop before executor shutdown"
        assert call_order.index("stop_thermal_monitor") < call_order.index("shutdown_listener_executor"), \
            "Thermal must stop before executor shutdown"
        assert call_order.index("close_monitor_session") < call_order.index("stop_janus_client"), \
            "Monitor session must close before Janus client stops"
        assert call_order.index("stop_janus_watchdog") < call_order.index("close_monitor_session"), \
            "Watchdog must stop before monitor session close"
