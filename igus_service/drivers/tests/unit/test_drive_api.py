"""Unit tests for DryveD1 API methods (without hardware)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig
from drivers.dryve_d1.od.indices import ODIndex


class TestDryveD1API:
    """Unit tests for DryveD1 API methods."""

    @pytest.fixture
    def drive_config(self):
        return DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )

    @pytest.fixture
    def mock_drive(self, drive_config):
        drive = DryveD1(config=drive_config)
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        jog_state = MagicMock()
        jog_state.active = False
        jog_state.deadline_s = 0.0
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._telemetry_poller = None
        drive._homing = MagicMock()
        return drive

    def test_is_connected_property(self, mock_drive):
        mock_drive._session.is_connected = True
        assert mock_drive.is_connected is True
        mock_drive._session.is_connected = False
        assert mock_drive.is_connected is False
        mock_drive._session = None
        assert mock_drive.is_connected is False

    @pytest.mark.asyncio
    async def test_get_position(self, mock_drive):
        expected_position = 12345
        mock_drive.read_i32 = AsyncMock(return_value=expected_position)
        position = await mock_drive.get_position()
        assert position == expected_position
        mock_drive.read_i32.assert_called_once_with(int(ODIndex.POSITION_ACTUAL_VALUE))

    @pytest.mark.asyncio
    async def test_get_status(self, mock_drive):
        mock_statusword = 0x0027
        mock_drive.read_u16 = AsyncMock(return_value=mock_statusword)
        status = await mock_drive.get_status()
        assert isinstance(status, dict)
        assert "operation_enabled" in status
        assert status["operation_enabled"] is True
        mock_drive.read_u16.assert_called_once_with(int(ODIndex.STATUSWORD))

    @pytest.mark.asyncio
    async def test_is_motion(self, mock_drive):
        # PP mode: motion = target not reached and |velocity| > threshold
        mock_drive.read_u16 = AsyncMock(return_value=0x0023)
        mock_drive.read_i8 = AsyncMock(return_value=1)
        mock_drive.read_i32 = AsyncMock(return_value=100)
        is_moving = await mock_drive.is_motion()
        assert is_moving is True

        # PP mode: target reached -> not moving
        mock_drive.read_u16 = AsyncMock(return_value=0x0427)
        mock_drive.read_i8 = AsyncMock(return_value=1)
        is_moving = await mock_drive.is_motion()
        assert is_moving is False

    @pytest.mark.asyncio
    async def test_is_homed(self, mock_drive):
        mock_drive.read_u16 = AsyncMock(return_value=1)
        is_homed = await mock_drive.is_homed()
        assert is_homed is True
        mock_drive.read_u16 = AsyncMock(return_value=0)
        is_homed = await mock_drive.is_homed()
        assert is_homed is False
        mock_drive.read_u16.assert_called_with(int(ODIndex.HOMING_STATUS), 0)

    @pytest.mark.asyncio
    async def test_is_homed_error_handling(self, mock_drive):
        mock_drive.read_u16 = AsyncMock(side_effect=Exception("Register not available"))
        is_homed = await mock_drive.is_homed()
        assert is_homed is False

    @pytest.mark.asyncio
    async def test_move_to_position_with_homing_check(self, mock_drive):
        import warnings
        mock_drive.get_status = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "remote": True,
        })
        mock_drive.read_u16 = AsyncMock(return_value=0x0237)
        mock_drive.is_homed = AsyncMock(return_value=False)
        mock_drive._pp.move_to_position = AsyncMock()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await mock_drive.move_to_position(
                target_position=100000, velocity=5000, accel=10000, decel=10000,
            )
            assert len(w) > 0
            assert any("homing" in str(warning.message).lower() for warning in w)
        mock_drive._pp.move_to_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_detects_mode(self, mock_drive):
        mock_drive.get_status = AsyncMock(return_value={"operation_enabled": True, "fault": False, "quick_stop": True})
        # Profile Position mode
        mock_drive.read_i8 = AsyncMock(return_value=1)
        mock_drive._pp.stop = AsyncMock()
        await mock_drive.stop()
        mock_drive._pp.stop.assert_called_once()
        mock_drive._pp.stop.reset_mock()
        # Profile Velocity mode
        mock_drive.read_i8 = AsyncMock(return_value=3)
        mock_drive._pv = MagicMock()
        mock_drive._pv.stop = AsyncMock()
        await mock_drive.stop()
        mock_drive._pv.stop.assert_called_once()
        mock_drive._pv.stop.reset_mock()
        # Unknown mode -> quick_stop
        mock_drive.read_i8 = AsyncMock(return_value=99)
        mock_drive._sm.quick_stop = AsyncMock()
        await mock_drive.stop()
        mock_drive._sm.quick_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_error_handling(self, mock_drive):
        """When stop() encounters socket errors, it swallows them silently.
        
        The abort event + HALT bit are the reliable stop mechanism;
        CiA402 transitions are best-effort only.
        """
        mock_drive.get_status = AsyncMock(return_value={"operation_enabled": True, "fault": False, "quick_stop": True})
        mock_drive.read_i8 = AsyncMock(side_effect=Exception("Read failed"))
        # stop() should NOT raise even when read_i8 fails
        await mock_drive.stop()
        # Abort event should be set regardless of socket errors
        assert mock_drive._abort_event.is_set()

    def test_require_sm(self, mock_drive):
        mock_drive._sm = None
        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_sm()

    def test_require_pp(self, mock_drive):
        mock_drive._pp = None
        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_pp()

    def test_require_homing(self, mock_drive):
        mock_drive._homing = None
        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_homing()

    def test_require_jog(self, mock_drive):
        mock_drive._jog = None
        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_jog()
