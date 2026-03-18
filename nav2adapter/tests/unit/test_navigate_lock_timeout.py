"""Test that navigate lock timeout returns error status (not deadlock)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationCommand, NavigationStatusEnum
from services.command_handler import CommandHandler


def _make_handler():
    handler = CommandHandler(
        symovo_client=MagicMock(),
        event_bus=AsyncMock(),
        state_store=AsyncMock(),
    )
    return handler


def _cmd(command_id="cmd-lock-test"):
    return NavigationCommand(
        command_id=command_id,
        timestamp="2026-01-01T00:00:00Z",
        target_id="TestPose",
        x=1.0, y=2.0, theta=1.5708,
    )


class TestNavigateLockTimeout:
    @pytest.mark.asyncio
    async def test_lock_timeout_returns_error_status(self):
        """When navigate lock is held externally, handle_drive_to_position
        times out and returns ERROR with reason 'navigate_lock_timeout'."""
        handler = _make_handler()

        # Acquire the lock externally to simulate contention
        await handler._navigate_lock.acquire()

        try:
            # The merged code uses asyncio.timeout(120). We need to make it
            # very short. Monkey-patch the inner asyncio.timeout call.
            original_timeout = asyncio.timeout

            def _short_timeout(delay):
                # Replace the 120s timeout with 0.1s
                return original_timeout(0.1)

            with patch.object(asyncio, "timeout", side_effect=_short_timeout):
                status = await handler.handle_drive_to_position(_cmd())
        finally:
            handler._navigate_lock.release()

        assert status.status == NavigationStatusEnum.ERROR
        assert status.error_reason == "navigate_lock_timeout"
        assert status.goal_id == "cmd-lock-test"

    @pytest.mark.asyncio
    async def test_lock_acquired_normally_succeeds(self):
        """When lock is free, navigate proceeds normally (no timeout)."""
        handler = _make_handler()

        mock_ss = MagicMock()
        mock_ss.get_active_transport = AsyncMock(return_value=None)
        mock_ss.get_all_active_commands = AsyncMock(return_value={})
        mock_ss.get_session = AsyncMock(return_value=None)
        mock_ss.register_command = AsyncMock()
        mock_ss.upsert_session = AsyncMock()
        mock_ss.get_last_position_status = AsyncMock(return_value=None)
        mock_ss.set_last_navigation_status = AsyncMock()
        mock_ss.clear_all_commands = AsyncMock(return_value=0)

        handler.symovo_client.status_uncached = AsyncMock(
            return_value={"state_flags": {"drive_ready": True, "safety_cleared": True}}
        )
        handler.symovo_client.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        handler.symovo_client.transport_move_to_pose = AsyncMock(
            return_value={"id": "t1", "state": 1}
        )
        handler.symovo_client.transport_start = AsyncMock(return_value={"ok": True})
        handler.symovo_client.transport_get = AsyncMock(return_value={"state": 1})

        handler.state_store = mock_ss
        status = await handler.handle_drive_to_position(_cmd())

        assert status.status == NavigationStatusEnum.NAVIGATING
