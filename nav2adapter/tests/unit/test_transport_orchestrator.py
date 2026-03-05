"""Tests for services/transport_orchestrator.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.transport_orchestrator import TransportOrchestrator
from domain.models import NavigationStatusEnum


def _make_orch() -> TransportOrchestrator:
    client = MagicMock()
    client.robot_number = 15
    client.transport_create = AsyncMock(return_value={"id": 1, "state": 0})
    client.transport_move_to_pose = AsyncMock(return_value={"id": 2, "state": 0})
    client.transport_start = AsyncMock(return_value={"ok": True})
    client.transport_stop = AsyncMock(return_value={"ok": True})
    client.delete = AsyncMock()
    orch = TransportOrchestrator(client)
    return orch


class TestCreateTransportToStation:
    @pytest.mark.asyncio
    async def test_basic(self):
        orch = _make_orch()
        result = await orch.create_transport_to_station(5, description="Test")
        assert result == {"id": 1, "state": 0}
        orch.symovo_client.transport_create.assert_called_once()
        payload = orch.symovo_client.transport_create.call_args[0][0]
        assert payload["steps"][0]["station_id"] == 5

    @pytest.mark.asyncio
    async def test_default_description(self):
        orch = _make_orch()
        await orch.create_transport_to_station(3)
        payload = orch.symovo_client.transport_create.call_args[0][0]
        assert "Station 3" in payload["description"]


class TestCreateTransportToPose:
    @pytest.mark.asyncio
    async def test_basic(self):
        orch = _make_orch()
        result = await orch.create_transport_to_pose(1.0, 2.0, theta_rad=0.5)
        assert result == {"id": 2, "state": 0}
        orch.symovo_client.transport_move_to_pose.assert_called_once_with(
            x_m=1.0, y_m=2.0, theta_rad=0.5, map_id=None, max_speed_m_s=None, wait=False,
        )


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start(self):
        orch = _make_orch()
        result = await orch.start_transport("42")
        assert result == {"ok": True}
        orch.symovo_client.transport_start.assert_called_once_with("42")

    @pytest.mark.asyncio
    async def test_stop(self):
        orch = _make_orch()
        result = await orch.stop_transport("42")
        assert result == {"ok": True}
        orch.symovo_client.transport_stop.assert_called_once_with("42")


class TestDeleteTransport:
    @pytest.mark.asyncio
    async def test_success(self):
        orch = _make_orch()
        assert await orch.delete_transport("42") is True
        orch.symovo_client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure(self):
        orch = _make_orch()
        orch.symovo_client.delete = AsyncMock(side_effect=RuntimeError("fail"))
        assert await orch.delete_transport("42") is False


class TestStaticHelpers:
    def test_map_symovo_to_aehub(self):
        assert TransportOrchestrator.map_symovo_to_aehub(8) == NavigationStatusEnum.ARRIVED

    def test_get_transport_state(self):
        assert TransportOrchestrator.get_transport_state({"state": 5}) == 5
        assert TransportOrchestrator.get_transport_state({}) is not None  # defaults to UNKNOWN sentinel

    def test_is_terminal_state(self):
        assert TransportOrchestrator.is_terminal_state({"state": 8}) is True
        assert TransportOrchestrator.is_terminal_state({"state": 1}) is False

    def test_is_active_state(self):
        assert TransportOrchestrator.is_active_state({"state": 5}) is True
        assert TransportOrchestrator.is_active_state({"state": 8}) is False
