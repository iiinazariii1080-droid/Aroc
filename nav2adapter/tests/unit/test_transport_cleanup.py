"""
Tests for orphaned-transport cleanup when create succeeds but start fails.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationCommand, NavigationStatusEnum
from services.command_handler import CommandHandler


# ---------------------------------------------------------------------------
# Helper: build a CommandHandler wired to mocks
# ---------------------------------------------------------------------------

def _make_state_store_mock():
    return MagicMock(
        get_active_transport=AsyncMock(return_value=None),
        get_last_navigation_status=AsyncMock(return_value=None),
        get_all_active_commands=AsyncMock(return_value={}),
        register_command=AsyncMock(),
        set_last_navigation_status=AsyncMock(),
        get_session=AsyncMock(return_value=None),
        upsert_session=AsyncMock(),
        clear_session=AsyncMock(),
        clear_all_commands=AsyncMock(return_value=0),
        get_last_position_status=AsyncMock(return_value=None),
    )


def _make_handler(
    symovo_client: MagicMock,
    event_bus,
):
    """Return a configured CommandHandler with standard mocks."""
    return CommandHandler(
        symovo_client=symovo_client,
        event_bus=event_bus,
        state_store=_make_state_store_mock(),
    )


def _ready_symovo_client() -> MagicMock:
    """SymovoAgvClient mock that passes readiness checks."""
    client = MagicMock()
    client.status_uncached = AsyncMock(return_value={
        "state_flags": {"drive_ready": True, "safety_cleared": True},
    })
    client.pose_uncached = AsyncMock(return_value={"x": 0.0, "y": 0.0, "theta": 0.0})
    client.transport_get = AsyncMock(return_value={"state": 1})
    return client


_CMD = NavigationCommand(
    command_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    timestamp="2026-02-18T00:00:00Z",
    target_id="TestPose",
    x=1.0,
    y=2.0,
    theta=0.0,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_called_when_start_transport_fails(event_bus_instance):
    """If transport_create succeeds but start_transport fails,
    delete_transport must be called with the orphaned transport id."""
    symovo = _ready_symovo_client()
    symovo.transport_move_to_pose = AsyncMock(return_value={"id": "t-42", "state": 1})
    symovo.transport_start = AsyncMock(side_effect=Exception("controller timeout"))
    symovo.delete_transport = AsyncMock(return_value=True)

    handler = _make_handler(symovo, event_bus_instance)
    status = await handler.handle_drive_to_position(_CMD)

    assert status.status == NavigationStatusEnum.ERROR
    assert "transport_creation_failed" in (status.error_reason or "")
    symovo.delete_transport.assert_awaited_once_with("t-42")


@pytest.mark.asyncio
async def test_cleanup_not_called_when_create_fails(event_bus_instance):
    """If transport_create itself fails, there is nothing to clean up."""
    symovo = _ready_symovo_client()
    symovo.transport_move_to_pose = AsyncMock(side_effect=Exception("create boom"))
    symovo.delete_transport = AsyncMock()

    handler = _make_handler(symovo, event_bus_instance)
    status = await handler.handle_drive_to_position(_CMD)

    assert status.status == NavigationStatusEnum.ERROR
    symovo.delete_transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mask_original_error(event_bus_instance):
    """Even if delete_transport itself throws, the original error must be
    surfaced and the handler must not raise."""
    symovo = _ready_symovo_client()
    symovo.transport_move_to_pose = AsyncMock(return_value={"id": "t-99", "state": 1})
    symovo.transport_start = AsyncMock(side_effect=Exception("start boom"))
    symovo.delete_transport = AsyncMock(return_value=False)  # cleanup failed

    handler = _make_handler(symovo, event_bus_instance)
    status = await handler.handle_drive_to_position(_CMD)

    assert status.status == NavigationStatusEnum.ERROR
    # P2-19: error_reason is sanitized (no raw exception strings)
    assert status.error_reason == "transport_creation_failed"
    symovo.delete_transport.assert_awaited_once_with("t-99")


@pytest.mark.asyncio
async def test_cleanup_called_for_station_based_transport(event_bus_instance):
    """Station-based navigation follows the same cleanup path."""
    symovo = _ready_symovo_client()
    symovo.transport_create_station = AsyncMock(return_value={"id": "t-77", "state": 1})
    symovo.transport_start = AsyncMock(side_effect=Exception("start fail"))
    symovo.delete_transport = AsyncMock(return_value=True)

    handler = _make_handler(symovo, event_bus_instance)
    cmd = NavigationCommand(
        command_id="bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        timestamp="2026-02-18T00:00:00Z",
        target_id="ChargerStation",
        station_id=5,
    )
    status = await handler.handle_drive_to_position(cmd)

    assert status.status == NavigationStatusEnum.ERROR
    symovo.delete_transport.assert_awaited_once_with("t-77")


# ---------------------------------------------------------------------------
# SymovoAgvClient.delete_transport unit tests (via mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_transport_success():
    """delete_transport returns True when controller DELETE succeeds."""
    client = _ready_symovo_client()
    client.delete_transport = AsyncMock(return_value=True)

    result = await client.delete_transport("t-1")

    assert result is True
    client.delete_transport.assert_awaited_once_with("t-1")


@pytest.mark.asyncio
async def test_delete_transport_failure_returns_false():
    """delete_transport returns False when controller DELETE raises."""
    client = _ready_symovo_client()
    client.delete_transport = AsyncMock(return_value=False)

    result = await client.delete_transport("t-1")

    assert result is False
    client.delete_transport.assert_awaited_once()
