"""
Tests for orphaned-transport cleanup when create succeeds but start fails.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationCommand, NavigationStatusEnum
from services.command_handler import CommandHandler
from services.transport_orchestrator import TransportOrchestrator


# ---------------------------------------------------------------------------
# Helper: build a CommandHandler wired to mocks
# ---------------------------------------------------------------------------

def _make_handler(
    orchestrator: MagicMock,
    symovo_client: MagicMock,
    event_bus,
):
    """Return a configured CommandHandler with standard mocks."""
    return CommandHandler(
        symovo_client=symovo_client,
        transport_orchestrator=orchestrator,
        mqtt_adapter=None,
        event_bus=event_bus,
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


def _state_store_mocks():
    """Patch values for the state_store module-level singleton."""
    return {
        "get_active_transport": AsyncMock(return_value=None),
        "get_last_navigation_status": AsyncMock(return_value=None),
        "get_all_active_commands": AsyncMock(return_value={}),
        "register_command": AsyncMock(),
        "set_last_navigation_status": AsyncMock(),
        "get_session": AsyncMock(return_value=None),
        "set_session": AsyncMock(),
    }


_CMD = NavigationCommand(
    command_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    timestamp="2026-02-18T00:00:00Z",
    target_id="TestPose",
)

_DB_RECORD = {
    "id": "db-1",
    "name": "TestPose",
    "params": {
        "location": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0, "map_id": 0},
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_called_when_start_transport_fails(event_bus_instance):
    """If transport_create succeeds but start_transport fails,
    delete_transport must be called with the orphaned transport id."""
    symovo = _ready_symovo_client()
    orch = MagicMock()
    orch.create_transport_to_pose = AsyncMock(return_value={"id": "t-42", "state": 1})
    orch.start_transport = AsyncMock(side_effect=Exception("controller timeout"))
    orch.delete_transport = AsyncMock(return_value=True)

    handler = _make_handler(orch, symovo, event_bus_instance)

    with patch("services.command_handler.get_robot_position_by_name", return_value=_DB_RECORD), \
         patch("services.command_handler.state_store") as ss:
        for k, v in _state_store_mocks().items():
            setattr(ss, k, v)

        status = await handler.handle_navigate_to(_CMD)

    assert status.status == NavigationStatusEnum.ERROR
    assert "transport_creation_failed" in (status.error_reason or "")
    orch.delete_transport.assert_awaited_once_with("t-42")


@pytest.mark.asyncio
async def test_cleanup_not_called_when_create_fails(event_bus_instance):
    """If transport_create itself fails, there is nothing to clean up."""
    symovo = _ready_symovo_client()
    orch = MagicMock()
    orch.create_transport_to_pose = AsyncMock(side_effect=Exception("create boom"))
    orch.delete_transport = AsyncMock()

    handler = _make_handler(orch, symovo, event_bus_instance)

    with patch("services.command_handler.get_robot_position_by_name", return_value=_DB_RECORD), \
         patch("services.command_handler.state_store") as ss:
        for k, v in _state_store_mocks().items():
            setattr(ss, k, v)

        status = await handler.handle_navigate_to(_CMD)

    assert status.status == NavigationStatusEnum.ERROR
    orch.delete_transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mask_original_error(event_bus_instance):
    """Even if delete_transport itself throws, the original error must be
    surfaced and the handler must not raise."""
    symovo = _ready_symovo_client()
    orch = MagicMock()
    orch.create_transport_to_pose = AsyncMock(return_value={"id": "t-99", "state": 1})
    orch.start_transport = AsyncMock(side_effect=Exception("start boom"))
    orch.delete_transport = AsyncMock(return_value=False)  # cleanup failed

    handler = _make_handler(orch, symovo, event_bus_instance)

    with patch("services.command_handler.get_robot_position_by_name", return_value=_DB_RECORD), \
         patch("services.command_handler.state_store") as ss:
        for k, v in _state_store_mocks().items():
            setattr(ss, k, v)

        status = await handler.handle_navigate_to(_CMD)

    assert status.status == NavigationStatusEnum.ERROR
    # P2-19: error_reason is sanitized (no raw exception strings)
    assert status.error_reason == "transport_creation_failed"
    orch.delete_transport.assert_awaited_once_with("t-99")


@pytest.mark.asyncio
async def test_cleanup_called_for_station_based_transport(event_bus_instance):
    """Station-based navigation follows the same cleanup path."""
    symovo = _ready_symovo_client()
    orch = MagicMock()
    orch.create_transport_to_station = AsyncMock(return_value={"id": "t-77", "state": 1})
    orch.start_transport = AsyncMock(side_effect=Exception("start fail"))
    orch.delete_transport = AsyncMock(return_value=True)

    handler = _make_handler(orch, symovo, event_bus_instance)

    with patch("services.command_handler.state_store") as ss, \
         patch.object(handler, "_resolve_target_config", new_callable=AsyncMock) as mock_resolve:
        # _resolve_target_config returns a station_id-based config
        mock_resolve.return_value = {"station_id": 5}
        for k, v in _state_store_mocks().items():
            setattr(ss, k, v)

        cmd = NavigationCommand(
            command_id="bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            timestamp="2026-02-18T00:00:00Z",
            target_id="ChargerStation",
        )
        status = await handler.handle_navigate_to(cmd)

    assert status.status == NavigationStatusEnum.ERROR
    orch.delete_transport.assert_awaited_once_with("t-77")


# ---------------------------------------------------------------------------
# TransportOrchestrator.delete_transport unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_transport_success():
    """delete_transport returns True when controller DELETE succeeds."""
    client = MagicMock()
    client.delete = AsyncMock(return_value={})
    orch = TransportOrchestrator(symovo_client=client)

    result = await orch.delete_transport("t-1")

    assert result is True
    client.delete.assert_awaited_once_with("/transport/t-1", op_timeout=5.0)


@pytest.mark.asyncio
async def test_delete_transport_failure_returns_false():
    """delete_transport returns False when controller DELETE raises."""
    client = MagicMock()
    client.delete = AsyncMock(side_effect=Exception("connection refused"))
    orch = TransportOrchestrator(symovo_client=client)

    result = await orch.delete_transport("t-1")

    assert result is False
    client.delete.assert_awaited_once()
