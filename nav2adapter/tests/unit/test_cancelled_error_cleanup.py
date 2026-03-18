"""Test that CancelledError during navigate cleans up orphaned transports."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from domain.models import NavigationCommand
from services.command_handler import CommandHandler


def _make_handler():
    symovo = MagicMock()
    symovo.status_uncached = AsyncMock(
        return_value={"state_flags": {"drive_ready": True, "safety_cleared": True}}
    )
    symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
    symovo.transport_get = AsyncMock(return_value={"state": 1})
    symovo.transport_move_to_pose = AsyncMock(return_value={"id": "orphan-t1", "state": 1})
    # transport_start raises CancelledError mid-flight
    symovo.transport_start = AsyncMock(side_effect=asyncio.CancelledError)
    symovo.delete_transport = AsyncMock()

    mock_ss = MagicMock()
    mock_ss.get_active_transport = AsyncMock(return_value=None)
    mock_ss.get_all_active_commands = AsyncMock(return_value={})
    mock_ss.get_session = AsyncMock(return_value=None)
    mock_ss.register_command = AsyncMock()
    mock_ss.upsert_session = AsyncMock()
    mock_ss.clear_session = AsyncMock()
    mock_ss.clear_transport = AsyncMock()
    mock_ss.get_last_position_status = AsyncMock(return_value=None)

    handler = CommandHandler(
        symovo_client=symovo,
        event_bus=AsyncMock(),
        state_store=mock_ss,
    )

    return handler, symovo, mock_ss


def _target_dict():
    return {"x": 1.0, "y": 2.0, "theta": 1.5708, "map_id": 0}


class TestCancelledErrorCleanup:
    @pytest.mark.asyncio
    async def test_cancelled_error_deletes_orphan_transport(self):
        """When CancelledError occurs after create but during start,
        the orphaned transport should be deleted."""
        handler, symovo, mock_ss = _make_handler()

        cmd = NavigationCommand(
            command_id="cmd-cancel-test",
            timestamp="2026-01-01T00:00:00Z",
            target_id="TestPose",
            x=1.0, y=2.0, theta=1.5708,
        )

        with pytest.raises(asyncio.CancelledError):
            await handler.handle_drive_to_position(cmd)

        # Cleanup should delete the orphan transport
        symovo.delete_transport.assert_awaited_with("orphan-t1")

    @pytest.mark.asyncio
    async def test_cancelled_error_clears_session(self):
        """CancelledError cleanup also clears the navigation session."""
        handler, symovo, mock_ss = _make_handler()

        cmd = NavigationCommand(
            command_id="cmd-cancel-test-2",
            timestamp="2026-01-01T00:00:00Z",
            target_id="TestPose",
            x=1.0, y=2.0, theta=1.5708,
        )

        with pytest.raises(asyncio.CancelledError):
            await handler.handle_drive_to_position(cmd)

        mock_ss.clear_session.assert_awaited_with("cmd-cancel-test-2")
