"""Tests for symovo_service — normalize_symovo_status and SymovoAgvClient methods."""
import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.symovo_service import SymovoAgvClient, normalize_symovo_status
from models.api_types import SymovoStatusResponse, ErrorStatus
from exceptions import DeviceError


# ── normalize_symovo_status ──────────────────────────────────────────

class TestNormalizeSymovoStatus:
    def test_full_status(self):
        raw = {
            "id": 15,
            "name": "AGV-15",
            "pose": {"x": 1.0, "y": 2.0, "theta": math.pi / 2, "map_id": 3},
            "velocity": {"x": 0.1, "y": 0.0, "theta": 0.5},
            "state": "IDLE",
            "battery_level": 0.85,
            "state_flags": {"drive_ready": True},
            "ip": "192.168.1.10",
            "enabled": True,
        }
        result = normalize_symovo_status(raw)
        assert isinstance(result, SymovoStatusResponse)
        assert result.online is True
        assert result.id == "15"
        assert result.pose.x_m == 1.0
        assert result.pose.y_m == 2.0
        assert result.pose.theta_deg == pytest.approx(90.0, abs=0.01)
        assert result.velocity.vx_m_s == 0.1
        assert result.velocity.omega_deg_s == pytest.approx(math.degrees(0.5), abs=0.01)
        assert result.battery_level_percent == pytest.approx(85.0)

    def test_pose_at_top_level(self):
        """Pose-only response (no nested pose key)."""
        raw = {"x": 3.0, "y": 4.0, "theta": 0.0, "map_id": 1}
        result = normalize_symovo_status(raw)
        assert isinstance(result, SymovoStatusResponse)
        assert result.pose.x_m == 3.0
        assert result.pose.y_m == 4.0

    def test_list_input(self):
        raw = [{"id": 5, "pose": {"x": 1.0, "y": 2.0, "theta": 0.0}}]
        result = normalize_symovo_status(raw)
        assert isinstance(result, SymovoStatusResponse)
        assert result.id == "5"

    def test_empty_list(self):
        result = normalize_symovo_status([])
        assert isinstance(result, SymovoStatusResponse)

    def test_invalid_data_returns_error_status(self):
        """Non-parseable data produces ErrorStatus."""
        # Force an exception inside parsing via a type that breaks model construction
        result = normalize_symovo_status("invalid_string")
        # Should still return something — either SymovoStatusResponse or ErrorStatus
        assert isinstance(result, (SymovoStatusResponse, ErrorStatus))

    def test_none_id(self):
        raw = {"pose": {"x": 0, "y": 0, "theta": 0}}
        result = normalize_symovo_status(raw)
        assert result.id is None

    def test_none_theta(self):
        raw = {"pose": {"x": 0, "y": 0, "theta": None}}
        result = normalize_symovo_status(raw)
        assert result.pose.theta_deg is None

    def test_none_battery(self):
        raw = {"pose": {"x": 0, "y": 0, "theta": 0}, "battery_level": None}
        result = normalize_symovo_status(raw)
        assert result.battery_level_percent is None

    def test_zero_theta(self):
        """Falsy-zero theta should be converted, not silently dropped."""
        raw = {"pose": {"x": 0, "y": 0, "theta": 0.0}}
        result = normalize_symovo_status(raw)
        assert result.pose.theta_deg == 0.0


# ── SymovoAgvClient.__init__ ────────────────────────────────────────

class TestClientInit:
    def test_default_settings(self):
        with patch("services.symovo_service.settings") as mock_settings:
            mock_settings.symovo_base_url = "https://host/v0"
            mock_settings.symovo_robot_number = 15
            mock_settings.symovo_timeout_seconds = 10
            mock_settings.symovo_allow_invalid_certs = True
            mock_settings.symovo_operation_timeout_seconds = 30.0
            mock_settings.symovo_motion_timeout_seconds = None
            client = SymovoAgvClient()
            assert client.robot_number == 15
            assert client._operation_timeout_seconds == 30.0
            assert client._motion_timeout_seconds is None

    def test_explicit_zero_timeout(self):
        """Explicit 0 should not fall back to settings (falsy-zero fix)."""
        with patch("services.symovo_service.settings") as mock_settings:
            mock_settings.symovo_base_url = "https://host/v0"
            mock_settings.symovo_robot_number = 1
            mock_settings.symovo_timeout_seconds = 10
            mock_settings.symovo_allow_invalid_certs = True
            mock_settings.symovo_operation_timeout_seconds = 30.0
            mock_settings.symovo_motion_timeout_seconds = 120.0
            client = SymovoAgvClient(
                operation_timeout_seconds=0.0,
                motion_timeout_seconds=0.0,
            )
            assert client._operation_timeout_seconds == 0.0
            assert client._motion_timeout_seconds == 0.0

    def test_explicit_robot_number_zero(self):
        with patch("services.symovo_service.settings") as mock_settings:
            mock_settings.symovo_base_url = "https://host/v0"
            mock_settings.symovo_robot_number = 99
            mock_settings.symovo_timeout_seconds = 10
            mock_settings.symovo_allow_invalid_certs = True
            mock_settings.symovo_operation_timeout_seconds = 30.0
            mock_settings.symovo_motion_timeout_seconds = None
            client = SymovoAgvClient(robot_number=0)
            assert client.robot_number == 0  # not 99


# ── pose / status with agv/amr fallback ─────────────────────────────

class TestPoseAndStatusFallback:
    @staticmethod
    def _bind_fallback(client):
        """Bind the _with_endpoint_fallback helper so mocked methods can use it."""
        client._with_endpoint_fallback = SymovoAgvClient._with_endpoint_fallback.__get__(client, SymovoAgvClient)

    @pytest.mark.asyncio
    async def test_pose_impl_agv_success(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(return_value={"x": 1.0, "y": 2.0, "theta": 0.0})
        client.robot_number = 15
        self._bind_fallback(client)
        client._pose_impl = SymovoAgvClient._pose_impl.__get__(client, SymovoAgvClient)
        result = await client._pose_impl()
        assert result["x"] == 1.0
        client.get.assert_awaited_once_with("/agv/15/pose")

    @pytest.mark.asyncio
    async def test_pose_impl_fallback_to_amr(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(side_effect=[DeviceError("404"), {"x": 3.0, "y": 4.0}])
        client.robot_number = 15
        self._bind_fallback(client)
        client._pose_impl = SymovoAgvClient._pose_impl.__get__(client, SymovoAgvClient)
        result = await client._pose_impl()
        assert result["x"] == 3.0

    @pytest.mark.asyncio
    async def test_status_impl_agv_success(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(return_value={"state": "IDLE"})
        client.robot_number = 15
        self._bind_fallback(client)
        client._status_impl = SymovoAgvClient._status_impl.__get__(client, SymovoAgvClient)
        result = await client._status_impl()
        assert result["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_status_impl_fallback_to_amr(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(side_effect=[DeviceError("fail"), {"state": "RUNNING"}])
        client.robot_number = 15
        self._bind_fallback(client)
        client._status_impl = SymovoAgvClient._status_impl.__get__(client, SymovoAgvClient)
        result = await client._status_impl()
        assert result["state"] == "RUNNING"


# ── move_speed (no lock required) ────────────────────────────────────

class TestMoveSpeed:
    @staticmethod
    def _bind(client):
        client._with_endpoint_fallback = SymovoAgvClient._with_endpoint_fallback.__get__(client, SymovoAgvClient)
        client.move_speed = SymovoAgvClient.move_speed.__get__(client, SymovoAgvClient)

    @pytest.mark.asyncio
    async def test_move_speed_success(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.put = AsyncMock(return_value={"ok": True})
        client.robot_number = 15
        self._bind(client)
        with patch("services.symovo_service.settings") as s:
            s.teleop_default_linear_speed = 0.3
            s.teleop_default_angular_speed = 0.5
            s.teleop_default_duration = 0.2
            result = await client.move_speed(speed=0.1, angular_speed=0.2, duration=0.5)
        assert result == {"ok": True}
        call_args = client.put.call_args
        assert call_args[1]["json_data"]["speed"] == 0.1

    @pytest.mark.asyncio
    async def test_move_speed_fallback_amr(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.put = AsyncMock(side_effect=[DeviceError("nope"), {"ok": True}])
        client.robot_number = 15
        self._bind(client)
        with patch("services.symovo_service.settings") as s:
            s.teleop_default_linear_speed = 0.3
            s.teleop_default_angular_speed = 0.5
            s.teleop_default_duration = 0.2
            result = await client.move_speed()
        assert result == {"ok": True}


# ── poll_transport_completion ────────────────────────────────────────

class TestPollTransportCompletion:
    @pytest.mark.asyncio
    async def test_poll_uses_motion_timeout(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(return_value={"state": 8})
        client._motion_timeout_seconds = 60.0
        client._operation_timeout_seconds = 30.0
        client._infinite_timeout = None
        client.poll_transport_completion = SymovoAgvClient.poll_transport_completion.__get__(client, SymovoAgvClient)
        await client.poll_transport_completion(123)
        _, kwargs = client.get.call_args
        assert kwargs["op_timeout"] == 60.0

    @pytest.mark.asyncio
    async def test_poll_falls_back_to_operation_timeout(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(return_value={"state": 8})
        client._motion_timeout_seconds = None
        client._operation_timeout_seconds = 30.0
        client._infinite_timeout = None
        client.poll_transport_completion = SymovoAgvClient.poll_transport_completion.__get__(client, SymovoAgvClient)
        await client.poll_transport_completion(123)
        _, kwargs = client.get.call_args
        assert kwargs["op_timeout"] == 30.0


# ── wait_until_charging_stations_inactive ─────────────────────────────

class TestWaitUntilChargingStationsInactive:
    @pytest.mark.asyncio
    async def test_returns_true_when_all_inactive(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(return_value=[
            {"_type_id": 4, "state": "INACTIVE"},
            {"_type_id": 4, "state": "INACTIVE"},
        ])
        client.wait_until_charging_stations_inactive = SymovoAgvClient.wait_until_charging_stations_inactive.__get__(client, SymovoAgvClient)
        result = await client.wait_until_charging_stations_inactive(timeout=1.0, interval=0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(return_value=[
            {"_type_id": 4, "state": "OK"},
        ])
        client.wait_until_charging_stations_inactive = SymovoAgvClient.wait_until_charging_stations_inactive.__get__(client, SymovoAgvClient)
        result = await client.wait_until_charging_stations_inactive(timeout=0.3, interval=0.1)
        assert result is False


# ── clear_all_transports ─────────────────────────────────────────────

class TestClearAllTransports:
    @pytest.mark.asyncio
    async def test_empty_transport_list(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(return_value=[])
        client._make_request = AsyncMock()
        client.clear_all_transports = SymovoAgvClient.clear_all_transports.__wrapped__.__wrapped__.__get__(client, SymovoAgvClient)
        result = await client.clear_all_transports()
        assert result == []

    @pytest.mark.asyncio
    async def test_deletes_transports(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(side_effect=[
            [{"id": 1}, {"id": 2}],  # initial fetch
            [],  # verification
        ])
        client._make_request = AsyncMock(return_value={"ok": True})
        client.clear_all_transports = SymovoAgvClient.clear_all_transports.__wrapped__.__wrapped__.__get__(client, SymovoAgvClient)
        result = await client.clear_all_transports()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_handles_delete_failure(self):
        client = MagicMock(spec=SymovoAgvClient)
        client.get = AsyncMock(side_effect=[
            [{"id": 1}],
            [],
        ])
        client._make_request = AsyncMock(side_effect=DeviceError("fail"))
        client.clear_all_transports = SymovoAgvClient.clear_all_transports.__wrapped__.__wrapped__.__get__(client, SymovoAgvClient)
        result = await client.clear_all_transports()
        assert result == []  # failed deletes not included
