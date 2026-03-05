"""Tests for charger_workflow module — all 4 public functions."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from domain.models import NavigationSession, Pose2D
from datetime import datetime, timezone


@pytest.fixture
def mock_client():
    c = MagicMock()
    c.set_charging_station_enabled_by_name = AsyncMock(return_value=42)
    c.disable_all_charging_stations = AsyncMock()
    c.wait_until_charging_stations_inactive = AsyncMock(return_value=True)
    return c


# ── maybe_deactivate_on_cancel ──────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_on_cancel_disabled(mock_client):
    """No-op when charger_activation_enabled=False."""
    from services import charger_workflow
    transport = MagicMock(target_id="CHARGER")
    with patch.object(charger_workflow, "settings", MagicMock(charger_activation_enabled=False)):
        await charger_workflow.maybe_deactivate_on_cancel(mock_client, transport)
    mock_client.set_charging_station_enabled_by_name.assert_not_called()


@pytest.mark.asyncio
async def test_deactivate_on_cancel_wrong_target(mock_client):
    """No-op when target_id doesn't match charger name."""
    from services import charger_workflow
    transport = MagicMock(target_id="KITCHEN")
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_target_name="CHARGER",
        charger_station_name="charger",
    )):
        await charger_workflow.maybe_deactivate_on_cancel(mock_client, transport)
    mock_client.set_charging_station_enabled_by_name.assert_not_called()


@pytest.mark.asyncio
async def test_deactivate_on_cancel_happy_path(mock_client):
    """Calls disable when target matches charger name (case-insensitive)."""
    from services import charger_workflow
    transport = MagicMock(target_id="  Charger  ")
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_target_name="CHARGER",
        charger_station_name="my_charger",
    )):
        await charger_workflow.maybe_deactivate_on_cancel(mock_client, transport)
    mock_client.set_charging_station_enabled_by_name.assert_awaited_once_with("my_charger", enabled=False)


@pytest.mark.asyncio
async def test_deactivate_on_cancel_station_not_found(mock_client):
    """Graceful when station not found on controller."""
    from services import charger_workflow
    mock_client.set_charging_station_enabled_by_name = AsyncMock(return_value=None)
    transport = MagicMock(target_id="CHARGER")
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_target_name="CHARGER",
        charger_station_name="charger",
    )):
        await charger_workflow.maybe_deactivate_on_cancel(mock_client, transport)


@pytest.mark.asyncio
async def test_deactivate_on_cancel_exception_handled(mock_client):
    """Doesn't raise when disable call fails."""
    from services import charger_workflow
    mock_client.set_charging_station_enabled_by_name = AsyncMock(side_effect=RuntimeError("boom"))
    transport = MagicMock(target_id="CHARGER")
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_target_name="CHARGER",
        charger_station_name="charger",
    )):
        await charger_workflow.maybe_deactivate_on_cancel(mock_client, transport)  # no raise


# ── maybe_activate_after_arrival ─────────────────────────────────────

@pytest.mark.asyncio
async def test_activate_after_arrival_disabled(mock_client):
    from services import charger_workflow
    session = NavigationSession(
        command_id="c1", target_id="CHARGER",
        start=Pose2D(x=0, y=0), goal=Pose2D(x=1, y=1),
        total_dist_m=1.4, min_remaining_dist_m=0,
    )
    with patch.object(charger_workflow, "settings", MagicMock(charger_activation_enabled=False)):
        await charger_workflow.maybe_activate_after_arrival(mock_client, session)
    mock_client.set_charging_station_enabled_by_name.assert_not_called()


@pytest.mark.asyncio
async def test_activate_after_arrival_target_mismatch(mock_client):
    from services import charger_workflow
    session = NavigationSession(
        command_id="c1", target_id="WAREHOUSE",
        start=Pose2D(x=0, y=0), goal=Pose2D(x=1, y=1),
        total_dist_m=1.4, min_remaining_dist_m=0,
    )
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_target_name="CHARGER",
        charger_station_name="charger",
    )):
        await charger_workflow.maybe_activate_after_arrival(mock_client, session)
    mock_client.set_charging_station_enabled_by_name.assert_not_called()


@pytest.mark.asyncio
async def test_activate_after_arrival_happy(mock_client):
    from services import charger_workflow
    session = NavigationSession(
        command_id="c1", target_id="CHARGER",
        start=Pose2D(x=0, y=0), goal=Pose2D(x=1, y=1),
        total_dist_m=1.4, min_remaining_dist_m=0,
    )
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_target_name="CHARGER",
        charger_station_name="dock1",
    )):
        await charger_workflow.maybe_activate_after_arrival(mock_client, session)
    mock_client.set_charging_station_enabled_by_name.assert_awaited_once_with("dock1", enabled=True)


@pytest.mark.asyncio
async def test_activate_after_arrival_empty_target(mock_client):
    from services import charger_workflow
    session = NavigationSession(
        command_id="c1", target_id="",
        start=Pose2D(x=0, y=0), goal=Pose2D(x=1, y=1),
        total_dist_m=1.4, min_remaining_dist_m=0,
    )
    with patch.object(charger_workflow, "settings", MagicMock(charger_activation_enabled=True)):
        await charger_workflow.maybe_activate_after_arrival(mock_client, session)
    mock_client.set_charging_station_enabled_by_name.assert_not_called()


@pytest.mark.asyncio
async def test_activate_after_arrival_exception_handled(mock_client):
    from services import charger_workflow
    mock_client.set_charging_station_enabled_by_name = AsyncMock(side_effect=RuntimeError("oops"))
    session = NavigationSession(
        command_id="c1", target_id="CHARGER",
        start=Pose2D(x=0, y=0), goal=Pose2D(x=1, y=1),
        total_dist_m=1.4, min_remaining_dist_m=0,
    )
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_target_name="CHARGER",
        charger_station_name="charger",
    )):
        await charger_workflow.maybe_activate_after_arrival(mock_client, session)  # no raise


# ── maybe_deactivate_on_drive_mode ───────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_on_drive_mode_disabled(mock_client):
    from services import charger_workflow
    with patch.object(charger_workflow, "settings", MagicMock(charger_activation_enabled=False)):
        await charger_workflow.maybe_deactivate_on_drive_mode(mock_client)
    mock_client.set_charging_station_enabled_by_name.assert_not_called()


@pytest.mark.asyncio
async def test_deactivate_on_drive_mode_happy(mock_client):
    from services import charger_workflow
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_station_name="dock",
    )):
        await charger_workflow.maybe_deactivate_on_drive_mode(mock_client)
    mock_client.set_charging_station_enabled_by_name.assert_awaited_once_with("dock", enabled=False)


@pytest.mark.asyncio
async def test_deactivate_on_drive_mode_exception_nonfatal(mock_client):
    from services import charger_workflow
    mock_client.set_charging_station_enabled_by_name = AsyncMock(side_effect=RuntimeError("fail"))
    with patch.object(charger_workflow, "settings", MagicMock(
        charger_activation_enabled=True,
        charger_station_name="dock",
    )):
        await charger_workflow.maybe_deactivate_on_drive_mode(mock_client)  # no raise


# ── disable_all_before_move ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_disable_all_before_move_happy(mock_client):
    from services import charger_workflow
    await charger_workflow.disable_all_before_move(mock_client)
    mock_client.disable_all_charging_stations.assert_awaited_once()
    mock_client.wait_until_charging_stations_inactive.assert_awaited_once()


@pytest.mark.asyncio
async def test_disable_all_before_move_fails_when_not_inactive(mock_client):
    from services import charger_workflow
    from exceptions import DeviceError
    mock_client.wait_until_charging_stations_inactive = AsyncMock(return_value=False)
    with pytest.raises(DeviceError):
        await charger_workflow.disable_all_before_move(mock_client)
