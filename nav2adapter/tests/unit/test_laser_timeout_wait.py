"""Tests for laser_timeout / waiting_for_scanner wait-and-replace logic.

When the robot reports laser_timeout or waiting_for_scanner, the CommandHandler
now waits up to ``laser_timeout_wait_s`` for the flag to clear instead of
failing immediately.  If a new navigation command arrives during the wait,
the pending command is replaced (no queue — latest command wins).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationCommand, NavigationStatusEnum
from services.command_handler import CommandHandler


# ── Helpers ──────────────────────────────────────────────────────────

def _status_with_flags(**flag_overrides):
    """Build a Symovo AGV status dict with the given state_flags."""
    flags = {"drive_ready": True, "safety_cleared": True}
    flags.update(flag_overrides)
    return {"state_flags": flags}


_READY = _status_with_flags()
_LASER_TIMEOUT = _status_with_flags(laser_timeout=True)
_WAITING_FOR_SCANNER = _status_with_flags(waiting_for_scanner=True)
_EMERGENCY_STOP = _status_with_flags(emergency_stop=True)


def _make_handler(event_bus, *, symovo=None):
    if symovo is None:
        symovo = MagicMock()
        symovo.status = AsyncMock(return_value=_READY)
        symovo.status_uncached = AsyncMock(return_value=_READY)
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})
        symovo.set_drive_mode = AsyncMock()

    # Ensure transport methods are always present (even on user-provided mocks)
    if not hasattr(symovo, "transport_move_to_pose") or not isinstance(symovo.transport_move_to_pose, AsyncMock):
        symovo.transport_move_to_pose = AsyncMock(return_value={"id": "t1", "state": 1})
    if not hasattr(symovo, "transport_create_station") or not isinstance(symovo.transport_create_station, AsyncMock):
        symovo.transport_create_station = AsyncMock(return_value={"id": "t2", "state": 1})
    if not hasattr(symovo, "transport_start") or not isinstance(symovo.transport_start, AsyncMock):
        symovo.transport_start = AsyncMock(return_value={"ok": True})
    if not hasattr(symovo, "transport_stop") or not isinstance(symovo.transport_stop, AsyncMock):
        symovo.transport_stop = AsyncMock()
    if not hasattr(symovo, "delete_transport") or not isinstance(symovo.delete_transport, AsyncMock):
        symovo.delete_transport = AsyncMock()

    return CommandHandler(
        symovo_client=symovo,
        event_bus=event_bus,
        state_store=_make_state_store_mock(),
    )


def _cmd(target_id="TestPose", command_id="cmd-001"):
    return NavigationCommand(
        command_id=command_id,
        timestamp="2026-01-01T00:00:00Z",
        target_id=target_id,
        x=1.0,
        y=2.0,
        theta=1.5708,
    )


def _make_state_store_mock(**overrides):
    defaults = {
        "get_active_transport": AsyncMock(return_value=None),
        "get_last_navigation_status": AsyncMock(return_value=None),
        "get_last_position_status": AsyncMock(return_value=None),
        "get_all_active_commands": AsyncMock(return_value={}),
        "register_command": AsyncMock(),
        "set_last_navigation_status": AsyncMock(),
        "clear_transport": AsyncMock(),
        "clear_session": AsyncMock(),
        "clear_all_commands": AsyncMock(return_value=0),
        "get_session": AsyncMock(return_value=None),
        "upsert_session": AsyncMock(),
    }
    defaults.update(overrides)
    mock = MagicMock(**defaults)
    return mock


_SETTINGS_PATCH = {
    "laser_timeout_wait_s": 4.0,           # short for tests
    "laser_timeout_poll_interval_s": 0.2,   # fast polling for tests
    "symovo_auto_set_drive_mode": False,
    "symovo_clear_transports_before_navigate": False,
}


def _patch_settings(**extra):
    merged = {**_SETTINGS_PATCH, **extra}
    return patch("services.command_handler.settings", **merged)


# ── Tests: laser_timeout clears within wait window ───────────────────

class TestLaserTimeoutWaitSuccess:
    """laser_timeout active at first check, clears on second poll → navigation proceeds."""

    @pytest.mark.asyncio
    async def test_laser_timeout_clears_then_navigates(self, event_bus_instance):
        """Robot starts with laser_timeout=true, then it clears → NAVIGATING."""
        symovo = MagicMock()
        # First call: laser_timeout active; second call: cleared
        symovo.status_uncached = AsyncMock(side_effect=[_LASER_TIMEOUT, _READY])
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})

        handler = _make_handler(event_bus_instance, symovo=symovo)

        with _patch_settings():
            status = await handler.handle_drive_to_position(_cmd())

        assert status.status == NavigationStatusEnum.NAVIGATING
        assert status.goal_id == "cmd-001"
        # status_uncached called at least twice (initial + wait poll)
        assert symovo.status_uncached.await_count >= 2

    @pytest.mark.asyncio
    async def test_waiting_for_scanner_clears_then_navigates(self, event_bus_instance):
        """Same logic applies to waiting_for_scanner flag."""
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(side_effect=[_WAITING_FOR_SCANNER, _READY])
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})

        handler = _make_handler(event_bus_instance, symovo=symovo)

        with _patch_settings():
            status = await handler.handle_drive_to_position(_cmd())

        assert status.status == NavigationStatusEnum.NAVIGATING


# ── Tests: timeout expires ──────────────────────────────────────────

class TestLaserTimeoutWaitExpires:
    """laser_timeout stays active for the entire wait window → error."""

    @pytest.mark.asyncio
    async def test_laser_timeout_expires(self, event_bus_instance):
        """laser_timeout never clears → error after timeout."""
        symovo = MagicMock()
        # Always returns laser_timeout
        symovo.status_uncached = AsyncMock(return_value=_LASER_TIMEOUT)

        handler = _make_handler(event_bus_instance, symovo=symovo)

        with _patch_settings(laser_timeout_wait_s=1.0, laser_timeout_poll_interval_s=0.2):
            status = await handler.handle_drive_to_position(_cmd())

        assert status.status == NavigationStatusEnum.ERROR
        assert "laser_timeout" in status.error_reason

    @pytest.mark.asyncio
    async def test_waiting_for_scanner_expires(self, event_bus_instance):
        """waiting_for_scanner never clears → error after timeout."""
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(return_value=_WAITING_FOR_SCANNER)

        handler = _make_handler(event_bus_instance, symovo=symovo)

        with _patch_settings(laser_timeout_wait_s=1.0, laser_timeout_poll_interval_s=0.2):
            status = await handler.handle_drive_to_position(_cmd())

        assert status.status == NavigationStatusEnum.ERROR
        assert "waiting_for_scanner" in status.error_reason


# ── Tests: non-scanner error → no wait ──────────────────────────────

class TestNonScannerErrorNoWait:
    """Non-scanner readiness errors (e.g. emergency_stop) fail immediately."""

    @pytest.mark.asyncio
    async def test_emergency_stop_no_wait(self, event_bus_instance):
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(return_value=_EMERGENCY_STOP)

        handler = _make_handler(event_bus_instance, symovo=symovo)

        with _patch_settings():
            status = await handler.handle_drive_to_position(_cmd())

        assert status.status == NavigationStatusEnum.ERROR
        assert "emergency_stop" in status.error_reason
        # Only one status check — no polling loop
        assert symovo.status_uncached.await_count == 1


# ── Tests: command replacement ──────────────────────────────────────

class TestCommandReplacement:
    """New command cancels a pending laser-wait and replaces it."""

    @pytest.mark.asyncio
    async def test_new_command_replaces_pending(self, event_bus_instance):
        """Send cmd-001 (laser_timeout) then cmd-002 → cmd-001 gets replaced, cmd-002 succeeds."""
        symovo = MagicMock()

        # Track call count to decide what to return:
        # - cmd-001 initial check → laser_timeout
        # - cmd-001 poll(s) during wait → laser_timeout
        # - cmd-002 initial check → ready  (after cmd-001 cancels and releases lock)
        call_count = 0

        async def _status_uncached():
            nonlocal call_count
            call_count += 1
            # First 5 calls: laser_timeout (cmd-001 initial + polls)
            # After that: ready (cmd-002's turn)
            if call_count <= 5:
                return _LASER_TIMEOUT
            return _READY

        symovo.status_uncached = AsyncMock(side_effect=_status_uncached)
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})

        handler = _make_handler(event_bus_instance, symovo=symovo)

        results = {}

        # Apply patches at the outer scope so both coroutines share them
        with _patch_settings(laser_timeout_wait_s=10.0, laser_timeout_poll_interval_s=0.3):

            async def send_cmd1():
                results["cmd1"] = await handler.handle_drive_to_position(_cmd(command_id="cmd-001"))

            async def send_cmd2():
                # Give cmd-001 time to enter the wait loop
                await asyncio.sleep(0.5)
                results["cmd2"] = await handler.handle_drive_to_position(_cmd(command_id="cmd-002"))

            await asyncio.gather(send_cmd1(), send_cmd2())

        # cmd-001 should have been replaced
        assert results["cmd1"].status == NavigationStatusEnum.ERROR
        assert results["cmd1"].error_reason == "replaced_by_newer_command"

        # cmd-002 should succeed (or if it got replaced too, check what happened)
        assert results["cmd2"].status == NavigationStatusEnum.NAVIGATING, \
            f"cmd2 failed with: {results['cmd2'].error_reason}"
        assert results["cmd2"].goal_id == "cmd-002"


# ── Tests: different error appears during scanner wait ──────────────

class TestDifferentErrorDuringWait:
    """If a different readiness error appears during wait, it's handled normally."""

    @pytest.mark.asyncio
    async def test_emergency_stop_during_laser_wait(self, event_bus_instance):
        """laser_timeout first, then emergency_stop on next poll → error emergency_stop."""
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(side_effect=[_LASER_TIMEOUT, _EMERGENCY_STOP])

        handler = _make_handler(event_bus_instance, symovo=symovo)

        with _patch_settings():
            status = await handler.handle_drive_to_position(_cmd())

        assert status.status == NavigationStatusEnum.ERROR
        assert "emergency_stop" in status.error_reason


# ── Tests: _wait_for_scanner_clear unit test ────────────────────────

class TestWaitForScannerClearUnit:
    """Direct tests for the _wait_for_scanner_clear method."""

    @pytest.mark.asyncio
    async def test_cancel_event_immediately_exits(self, event_bus_instance):
        """If cancel_event is already set, _wait_for_scanner_clear returns None immediately."""
        handler = _make_handler(event_bus_instance)
        cancel = asyncio.Event()
        cancel.set()

        with _patch_settings(laser_timeout_wait_s=30.0, laser_timeout_poll_interval_s=0.1):
            result = await handler._wait_for_scanner_clear(cancel, "cmd-test")

        assert result is None

    @pytest.mark.asyncio
    async def test_status_fetch_failure_retries(self, event_bus_instance):
        """If status fetch fails once, keep polling until success."""
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(
            side_effect=[Exception("network error"), _READY]
        )

        handler = _make_handler(event_bus_instance, symovo=symovo)
        cancel = asyncio.Event()

        with _patch_settings(laser_timeout_wait_s=5.0, laser_timeout_poll_interval_s=0.1):
            result = await handler._wait_for_scanner_clear(cancel, "cmd-test")

        assert result is not None
        assert result.ready is True

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, event_bus_instance):
        """If scanner flag never clears within timeout, returns None."""
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(return_value=_LASER_TIMEOUT)

        handler = _make_handler(event_bus_instance, symovo=symovo)
        cancel = asyncio.Event()

        with _patch_settings(laser_timeout_wait_s=0.5, laser_timeout_poll_interval_s=0.1):
            result = await handler._wait_for_scanner_clear(cancel, "cmd-test")

        assert result is None
        assert not cancel.is_set()  # cancel was NOT set; it was a timeout
