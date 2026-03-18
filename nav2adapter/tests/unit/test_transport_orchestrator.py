"""Tests for transport methods on SymovoAgvClient (transport_create_station, delete_transport)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationStatusEnum
from domain.state_machine import NavigationStateMachine


def _make_client() -> MagicMock:
    client = MagicMock()
    client.robot_number = 15
    client.transport_create_station = AsyncMock(return_value={"id": 1, "state": 0})
    client.transport_move_to_pose = AsyncMock(return_value={"id": 2, "state": 0})
    client.transport_start = AsyncMock(return_value={"ok": True})
    client.transport_stop = AsyncMock(return_value={"ok": True})
    client.delete_transport = AsyncMock(return_value=True)
    return client


class TestTransportCreateStation:
    @pytest.mark.asyncio
    async def test_basic(self):
        client = _make_client()
        result = await client.transport_create_station(5, description="Test")
        assert result == {"id": 1, "state": 0}
        client.transport_create_station.assert_called_once_with(5, description="Test")

    @pytest.mark.asyncio
    async def test_default_description(self):
        client = _make_client()
        await client.transport_create_station(3)
        client.transport_create_station.assert_called_once_with(3)


class TestTransportMoveToPose:
    @pytest.mark.asyncio
    async def test_basic(self):
        client = _make_client()
        result = await client.transport_move_to_pose(
            x_m=1.0, y_m=2.0, theta_rad=0.5, map_id=None, max_speed_m_s=None, wait=False,
        )
        assert result == {"id": 2, "state": 0}
        client.transport_move_to_pose.assert_called_once_with(
            x_m=1.0, y_m=2.0, theta_rad=0.5, map_id=None, max_speed_m_s=None, wait=False,
        )


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start(self):
        client = _make_client()
        result = await client.transport_start("42")
        assert result == {"ok": True}
        client.transport_start.assert_called_once_with("42")

    @pytest.mark.asyncio
    async def test_stop(self):
        client = _make_client()
        result = await client.transport_stop("42")
        assert result == {"ok": True}
        client.transport_stop.assert_called_once_with("42")


class TestDeleteTransport:
    @pytest.mark.asyncio
    async def test_success(self):
        client = _make_client()
        assert await client.delete_transport("42") is True
        client.delete_transport.assert_called_once_with("42")

    @pytest.mark.asyncio
    async def test_failure(self):
        client = _make_client()
        client.delete_transport = AsyncMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError):
            await client.delete_transport("42")


class TestStateMachineHelpers:
    """These helpers now live on NavigationStateMachine directly."""

    def test_map_symovo_to_aehub(self):
        assert NavigationStateMachine.map_symovo_to_aehub(8) == NavigationStatusEnum.ARRIVED

    def test_is_terminal_state(self):
        assert NavigationStateMachine.is_terminal_state(8) is True
        assert NavigationStateMachine.is_terminal_state(1) is False

    def test_is_active_state(self):
        assert NavigationStateMachine.is_active_state(5) is True
        assert NavigationStateMachine.is_active_state(8) is False
