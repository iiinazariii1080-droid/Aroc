import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationCommand
from services.command_handler import CommandHandler


@pytest.mark.asyncio
async def test_navigate_to_db_name_creates_transport(event_bus_instance):
    # Mock symovo readiness status
    symovo_client = MagicMock()
    symovo_client.status = AsyncMock(return_value={"state_flags": {"drive_ready": True, "safety_cleared": True}})
    symovo_client.status_uncached = AsyncMock(return_value={"state_flags": {"drive_ready": True, "safety_cleared": True}})
    symovo_client.transport_get = AsyncMock(return_value={"state": 1})
    symovo_client.pose_uncached = AsyncMock(return_value={"x": 0.0, "y": 0.0, "theta": 0.0})

    # Mock orchestrator
    orchestrator = MagicMock()
    orchestrator.create_transport_to_pose = AsyncMock(return_value={"id": "t1", "state": 1})
    orchestrator.start_transport = AsyncMock(return_value={"ok": True})

    # Patch DB lookup to return pose in params.location
    with patch("services.command_handler.get_robot_position_by_name") as mock_get_by_name, \
         patch("services.command_handler.state_store") as mock_state_store:
        mock_get_by_name.return_value = {
            "id": "abcd1234",
            "name": "FirstTestPose",
            "params": {
                "location": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 90.0, "map_id": 0}
            },
        }

        # Minimal state_store behavior used by handler
        mock_state_store.get_active_transport = AsyncMock(return_value=None)
        mock_state_store.get_last_navigation_status = AsyncMock(return_value=None)
        mock_state_store.get_all_active_commands = AsyncMock(return_value={})
        mock_state_store.register_command = AsyncMock()
        mock_state_store.set_last_navigation_status = AsyncMock()

        handler = CommandHandler(
            symovo_client=symovo_client,
            transport_orchestrator=orchestrator,
            mqtt_adapter=None,
            event_bus=event_bus_instance,
        )

        cmd = NavigationCommand(
            command_id="11111111-1111-1111-1111-111111111111",
            timestamp="2026-01-22T00:00:00Z",
            target_id="FirstTestPose",
        )

        status = await handler.handle_navigate_to(cmd)
        assert status.status.value == "navigating"

        orchestrator.create_transport_to_pose.assert_called_once()
        args = orchestrator.create_transport_to_pose.call_args.kwargs
        assert args["x_m"] == 1.0
        assert args["y_m"] == 2.0
        # theta_deg=90 => pi/2 rad
        assert abs(args["theta_rad"] - 1.5707963) < 1e-3

