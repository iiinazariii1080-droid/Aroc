"""Unit tests for DryveD1 API methods (without hardware)."""

import asyncio
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig
from drivers.dryve_d1.od.indices import ODIndex


class TestDryveD1API:
    """Unit tests for DryveD1 API methods."""
    
    @pytest.fixture
    def drive_config(self):
        """Create a test drive configuration."""
        return DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )
    
    @pytest.fixture
    def mock_drive(self, drive_config):
        """Create a mock DryveD1 instance."""
        drive = DryveD1(config=drive_config)
        # Mock internal components
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        # Mock jog with proper state structure
        jog_state = MagicMock()
        jog_state.active = False
        jog_state.deadline_s = 0.0  # Set to float, not MagicMock
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._telemetry_poller = None
        drive._homing = MagicMock()
        return drive
    
    def test_is_connected_property(self, mock_drive):
        """Test is_connected property."""
        # Connected
        mock_drive._session.is_connected = True
        assert mock_drive.is_connected is True
        
        # Not connected
        mock_drive._session.is_connected = False
        assert mock_drive.is_connected is False
        
        # No session
        mock_drive._session = None
        assert mock_drive.is_connected is False
    
    @pytest.mark.asyncio
    async def test_get_position(self, mock_drive):
        """Test get_position method."""
        expected_position = 12345
        mock_drive.read_i32 = AsyncMock(return_value=expected_position)
        
        position = await mock_drive.get_position()
        
        assert position == expected_position
        mock_drive.read_i32.assert_called_once_with(int(ODIndex.POSITION_ACTUAL_VALUE))
    
    @pytest.mark.asyncio
    async def test_get_status(self, mock_drive):
        """Test get_status method."""
        from drivers.dryve_d1.od.statusword import decode_statusword
        
        mock_statusword = 0x0027  # Operation enabled
        mock_drive.read_u16 = AsyncMock(return_value=mock_statusword)
        
        status = await mock_drive.get_status()
        
        assert isinstance(status, dict)
        assert status["operation_enabled"] is True
        assert status["fault"] is False
        # 0x0027 has bit 9 (remote) unset
        assert status["remote"] is False
        mock_drive.read_u16.assert_called_once_with(int(ODIndex.STATUSWORD))
    
    @pytest.mark.asyncio
    async def test_is_moving(self, mock_drive):
        """Test is_moving method (mode-aware: PP uses target_reached + velocity, PV uses velocity)."""
        # PP mode (1): motion = target not reached and |velocity| > threshold
        mock_drive.read_u16 = AsyncMock(return_value=0x0023)  # Operation enabled, target not reached
        mock_drive.read_i8 = AsyncMock(return_value=1)  # Profile Position
        mock_drive.read_i32 = AsyncMock(return_value=100)  # velocity > 10
        is_moving = await mock_drive.is_moving()
        assert is_moving is True

        # PP mode: target reached -> not moving
        mock_drive.read_u16 = AsyncMock(return_value=0x0427)  # Operation enabled, target reached
        mock_drive.read_i8 = AsyncMock(return_value=1)
        is_moving = await mock_drive.is_moving()
        assert is_moving is False
    
    @pytest.mark.asyncio
    async def test_is_homed(self, mock_drive):
        """Test is_homed method."""
        # Homed (register = 1)
        mock_drive.read_u16 = AsyncMock(return_value=1)
        is_homed = await mock_drive.is_homed()
        assert is_homed is True
        
        # Not homed (register = 0)
        mock_drive.read_u16 = AsyncMock(return_value=0)
        is_homed = await mock_drive.is_homed()
        assert is_homed is False
        
        # Check that correct register is read
        mock_drive.read_u16.assert_called_with(int(ODIndex.HOMING_STATUS), 0)
    
    @pytest.mark.asyncio
    async def test_is_homed_error_handling(self, mock_drive):
        """Test is_homed error handling."""
        # Simulate register read failure
        mock_drive.read_u16 = AsyncMock(side_effect=Exception("Register not available"))
        
        # Should return False if read fails
        is_homed = await mock_drive.is_homed()
        assert is_homed is False
    
    @pytest.mark.asyncio
    async def test_move_to_position_with_homing_check(self, mock_drive, caplog):
        """Test move_to_position with homing check."""
        # Mock get_status to return operation_enabled state with remote enabled
        mock_drive.get_status = AsyncMock(return_value={
            "operation_enabled": True,
            "fault": False,
            "remote": True,  # Required for motion operations
        })
        # Mock read_u16 for statusword check
        mock_drive.read_u16 = AsyncMock(return_value=0x0237)  # Operation enabled, remote enabled
        # Mock is_homed to return False
        mock_drive.is_homed = AsyncMock(return_value=False)
        mock_drive._pp.move_to_position = AsyncMock()

        # Should log warning when not homed (was warnings.warn, now _LOGGER.warning)
        import logging
        with caplog.at_level(logging.WARNING):
            await mock_drive.move_to_position(
                target_position=100000,
                velocity=5000,
                accel=10000,
                decel=10000,
            )

            assert any("homing" in record.message.lower() for record in caplog.records)

        # Should still call move_to_position with correct params
        mock_drive._pp.move_to_position.assert_called_once_with(
            target_position=100000,
            profile_velocity=5000,
            profile_accel=10000,
            profile_decel=10000,
            timeout_s=20.0,
        )
    
    @pytest.mark.asyncio
    async def test_move_to_position_without_homing_check(self, mock_drive):
        """Test move_to_position without homing check."""
        import warnings

        # Mock get_status to return operation_enabled state with remote enabled
        mock_drive.get_status = AsyncMock(return_value={
            "operation_enabled": True,
            "fault": False,
            "remote": True,  # Required for motion operations
        })
        # Mock read_u16 for statusword check
        mock_drive.read_u16 = AsyncMock(return_value=0x0237)  # Operation enabled, remote enabled
        mock_drive._pp.move_to_position = AsyncMock()

        # Should not check homing if require_homing=False
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await mock_drive.move_to_position(
                target_position=100000,
                velocity=5000,
                accel=10000,
                decel=10000,
                require_homing=False,
            )
            
            # Should not have homing warnings
            homing_warnings = [warning for warning in w if "homing" in str(warning.message).lower()]
            assert len(homing_warnings) == 0

        mock_drive._pp.move_to_position.assert_called_once_with(
            target_position=100000,
            profile_velocity=5000,
            profile_accel=10000,
            profile_decel=10000,
            timeout_s=20.0,
        )
    
    @pytest.mark.asyncio
    async def test_stop_detects_mode(self, mock_drive):
        """Test stop method mode detection."""
        # Mock get_status to return operation_enabled state
        mock_drive.get_status = AsyncMock(return_value={"operation_enabled": True, "fault": False, "quick_stop": True})
        
        # Profile Position mode (mode = 1)
        mock_drive.read_i8 = AsyncMock(return_value=1)  # MODES_OF_OPERATION_DISPLAY = 1
        # Mock _pp.stop() directly
        mock_drive._pp.stop = AsyncMock()
        
        await mock_drive.stop()
        mock_drive._pp.stop.assert_called_once()
        
        # Reset mocks for next test
        mock_drive._pp.stop.reset_mock()
        
        # Profile Velocity mode (mode = 3)
        mock_drive.read_i8 = AsyncMock(return_value=3)  # MODES_OF_OPERATION_DISPLAY = 3
        # Mock _pv.stop() directly
        mock_drive._pv = MagicMock()
        mock_drive._pv.stop = AsyncMock()
        
        await mock_drive.stop()
        mock_drive._pv.stop.assert_called_once()
        
        # Reset mocks for next test
        mock_drive._pv.stop.reset_mock()
        
        # Unknown mode - should fall back to quick_stop
        mock_drive.read_i8 = AsyncMock(return_value=99)  # Unknown mode
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
        """Test _require_sm raises error when not connected."""
        mock_drive._sm = None
        
        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_sm()
    
    def test_require_pp(self, mock_drive):
        """Test _require_pp raises error when not connected."""
        mock_drive._pp = None
        
        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_pp()
    
    def test_require_homing(self, mock_drive):
        """Test _require_homing raises error when not connected."""
        mock_drive._homing = None
        
        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_homing()
    
    def test_require_jog(self, mock_drive):
        """Test _require_jog raises error when not connected."""
        mock_drive._jog = None

        with pytest.raises(RuntimeError, match="Not connected"):
            mock_drive._require_jog()


class TestReconnectSafety:
    """Tests for safety requirement: stop motion on reconnect."""

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
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._telemetry_poller = None
        drive._homing = MagicMock()
        return drive

    @pytest.mark.asyncio
    async def test_reconnect_stop_calls_stop_when_jog_inactive(self, mock_drive):
        """When jog is not active, reconnect safety handler calls stop()."""
        mock_drive._jog.state.active = False
        mock_drive.stop = AsyncMock()

        await mock_drive._stop_motion_on_reconnect()

        mock_drive.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_stop_calls_jog_release_when_jog_active(self, mock_drive):
        """When jog is active, reconnect safety handler calls jog.release()."""
        mock_drive._jog.state.active = True
        mock_drive._jog.release = AsyncMock()

        await mock_drive._stop_motion_on_reconnect()

        mock_drive._jog.release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_stop_calls_stop_when_jog_is_none(self, mock_drive):
        """When jog controller is None, falls through to stop()."""
        mock_drive._jog = None
        mock_drive.stop = AsyncMock()

        await mock_drive._stop_motion_on_reconnect()

        mock_drive.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_stop_logs_error_on_failure(self, mock_drive, caplog):
        """Failures in the safety handler are logged at ERROR, not propagated."""
        mock_drive._jog.state.active = False
        mock_drive.stop = AsyncMock(side_effect=OSError("socket closed"))

        with caplog.at_level(logging.ERROR):
            # Must NOT raise
            await mock_drive._stop_motion_on_reconnect()

        assert any("SAFETY" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_schedule_reconnect_stop_creates_task(self, mock_drive):
        """_schedule_reconnect_stop creates an asyncio task on the running loop."""
        mock_drive.stop = AsyncMock()

        # Must be called from within a running event loop
        mock_drive._schedule_reconnect_stop()

        # Let the created task run
        await asyncio.sleep(0)

        mock_drive.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_stop_retries_on_failure(self, mock_drive, caplog):
        """Reconnect safety handler retries up to 3 times with backoff."""
        mock_drive._jog.state.active = False
        mock_drive.stop = AsyncMock(side_effect=[OSError("fail 1"), OSError("fail 2"), None])

        await mock_drive._stop_motion_on_reconnect()

        assert mock_drive.stop.await_count == 3


# ---------------------------------------------------------------------------
# TEST-02: Abort token race condition tests
# ---------------------------------------------------------------------------

class TestAbortTokenRace:
    """Verify the atomic abort token prevents stop/move race conditions."""

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
        drive._pv = MagicMock()
        jog_state = MagicMock()
        jog_state.active = False
        jog_state.deadline_s = 0.0
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._telemetry_poller = None
        drive._homing = MagicMock()
        return drive

    @pytest.mark.asyncio
    async def test_stop_rotates_abort_token(self, mock_drive):
        """stop() must rotate _abort_token so in-flight moves detect the abort."""
        original_token = mock_drive._abort_token
        mock_drive.get_status = AsyncMock(return_value={"operation_enabled": False})
        await mock_drive.stop()
        assert mock_drive._abort_token != original_token

    @pytest.mark.asyncio
    async def test_move_mints_new_token(self, mock_drive):
        """move_to_position() must mint a new _abort_token before executing."""
        original_token = mock_drive._abort_token
        mock_drive.get_status_live = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "remote": True,
            "target_reached": True,
        })
        mock_drive.read_i8 = AsyncMock(return_value=1)  # PP mode
        mock_drive.read_u16 = AsyncMock(return_value=0x0237)
        mock_drive.is_homed = AsyncMock(return_value=True)
        mock_drive._pp.move_to_position = AsyncMock()

        await mock_drive.move_to_position(
            target_position=1000, velocity=100, accel=100, decel=100,
        )
        # Token was changed by move_to_position
        assert mock_drive._abort_token != original_token

    @pytest.mark.asyncio
    async def test_stop_during_move_detected_via_token(self, mock_drive, caplog):
        """If stop() fires during move, the token mismatch is logged."""
        mock_drive.get_status_live = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "remote": True,
            "target_reached": True,
        })
        mock_drive.read_i8 = AsyncMock(return_value=1)
        mock_drive.read_u16 = AsyncMock(return_value=0x0237)
        mock_drive.is_homed = AsyncMock(return_value=True)

        # Simulate stop() firing during the pp.move_to_position await
        import uuid
        async def _move_and_rotate(**kwargs):
            mock_drive._abort_token = uuid.uuid4().hex  # simulate stop()

        mock_drive._pp.move_to_position = AsyncMock(side_effect=_move_and_rotate)

        with caplog.at_level(logging.WARNING):
            await mock_drive.move_to_position(
                target_position=1000, velocity=100, accel=100, decel=100,
            )

        assert any("abort token mismatch" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_quick_stop_rotates_token(self, mock_drive):
        """quick_stop() must also rotate the abort token."""
        original_token = mock_drive._abort_token
        mock_drive.get_status = AsyncMock(return_value={"operation_enabled": False})
        await mock_drive.quick_stop()
        assert mock_drive._abort_token != original_token


# ---------------------------------------------------------------------------
# TEST-03: Unit tests for decomposed move_to_position helpers
# ---------------------------------------------------------------------------

class TestMoveToPositionHelpers:
    """Tests for the extracted move_to_position helper methods."""

    @pytest.fixture
    def drive_config(self):
        from drivers.dryve_d1.config.models import MotionLimits
        return DryveD1Config(
            drive=DriveConfig(
                connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1),
                limits=MotionLimits(
                    max_abs_velocity=10000,
                    max_abs_accel=5000,
                    max_abs_decel=5000,
                    min_position_limit=0,
                    max_position_limit=120000,
                ),
            ),
        )

    @pytest.fixture
    def mock_drive(self, drive_config):
        drive = DryveD1(config=drive_config)
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        drive._pv = MagicMock()
        jog_state = MagicMock()
        jog_state.active = False
        jog_state.deadline_s = 0.0
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._telemetry_poller = None
        drive._homing = MagicMock()
        return drive

    # -- _validate_motion_params ------------------------------------------------

    def test_validate_params_valid(self, mock_drive):
        """Valid parameters pass without raising."""
        mock_drive._validate_motion_params(
            velocity=1000, accel=2000, decel=2000, timeout_s=10.0,
        )

    def test_validate_params_zero_velocity(self, mock_drive):
        with pytest.raises(ValueError, match="velocity must be != 0"):
            mock_drive._validate_motion_params(
                velocity=0, accel=2000, decel=2000, timeout_s=10.0,
            )

    def test_validate_params_velocity_exceeds_limit(self, mock_drive):
        with pytest.raises(ValueError, match="exceeds max_abs_velocity"):
            mock_drive._validate_motion_params(
                velocity=20000, accel=2000, decel=2000, timeout_s=10.0,
            )

    def test_validate_params_zero_accel(self, mock_drive):
        with pytest.raises(ValueError, match="accel must be > 0"):
            mock_drive._validate_motion_params(
                velocity=1000, accel=0, decel=2000, timeout_s=10.0,
            )

    def test_validate_params_accel_exceeds_limit(self, mock_drive):
        with pytest.raises(ValueError, match="exceeds max_abs_accel"):
            mock_drive._validate_motion_params(
                velocity=1000, accel=99999, decel=2000, timeout_s=10.0,
            )

    def test_validate_params_zero_decel(self, mock_drive):
        with pytest.raises(ValueError, match="decel must be > 0"):
            mock_drive._validate_motion_params(
                velocity=1000, accel=2000, decel=0, timeout_s=10.0,
            )

    def test_validate_params_zero_timeout(self, mock_drive):
        with pytest.raises(ValueError, match="timeout_s must be > 0"):
            mock_drive._validate_motion_params(
                velocity=1000, accel=2000, decel=2000, timeout_s=0,
            )

    # -- _validate_position_limits ---------------------------------------------

    def test_validate_position_within_range(self, mock_drive):
        """No exception when target is within [min, max]."""
        mock_drive._validate_position_limits(60000, "op1")  # should not raise

    def test_validate_position_below_min(self, mock_drive):
        with pytest.raises(ValueError, match="below min_position_limit"):
            mock_drive._validate_position_limits(-100, "op1")

    def test_validate_position_above_max(self, mock_drive):
        with pytest.raises(ValueError, match="above max_position_limit"):
            mock_drive._validate_position_limits(999999, "op1")

    # -- _prepare_motion_context ------------------------------------------------

    @pytest.mark.asyncio
    async def test_prepare_context_not_connected(self, mock_drive):
        mock_drive._session.is_connected = False
        mock_drive._session = None
        with pytest.raises(RuntimeError, match="Not connected"):
            await mock_drive._prepare_motion_context("op1")

    @pytest.mark.asyncio
    async def test_prepare_context_fault_passes_through(self, mock_drive):
        """Fault check is now owned by the app layer — driver does NOT gate on fault."""
        mock_drive.get_status_live = AsyncMock(return_value={
            "operation_enabled": True, "fault": True, "remote": True,
        })
        status = await mock_drive._prepare_motion_context("op1")
        assert status["fault"] is True

    @pytest.mark.asyncio
    async def test_prepare_context_releases_active_jog(self, mock_drive):
        mock_drive.get_status_live = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "remote": True,
        })
        mock_drive._jog.state.active = True
        mock_drive._jog.release = AsyncMock()

        await mock_drive._prepare_motion_context("op1")

        mock_drive._jog.release.assert_awaited_once()

    # -- _ensure_mode_pp --------------------------------------------------------

    @pytest.mark.asyncio
    async def test_ensure_mode_pp_already_in_pp(self, mock_drive):
        """No SM cycle needed when already enabled in PP mode."""
        status = {"operation_enabled": True, "fault": False, "remote": True}
        mock_drive.read_i8 = AsyncMock(return_value=1)  # already PP

        result = await mock_drive._ensure_mode_pp(status, "op1")

        # SM shutdown should NOT be called
        mock_drive._sm.shutdown.assert_not_called()
        assert result is status  # unchanged

    @pytest.mark.asyncio
    async def test_ensure_mode_pp_wrong_mode_cycles_sm(self, mock_drive):
        """When in wrong mode, SM is cycled down and mode is written."""
        status = {"operation_enabled": True, "fault": False, "remote": True}
        mock_drive.read_i8 = AsyncMock(return_value=3)  # PV mode, not PP
        mock_drive._sm.shutdown = AsyncMock()
        mock_drive.write_u8 = AsyncMock()
        mock_drive.enable_operation = AsyncMock()
        mock_drive.get_status_live = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "remote": True,
        })

        await mock_drive._ensure_mode_pp(status, "op1")

        mock_drive._sm.shutdown.assert_awaited_once()
        mock_drive.write_u8.assert_awaited_once()


# ---------------------------------------------------------------------------
# _execute_stop() never-raise contract
# ---------------------------------------------------------------------------


class TestExecuteStopContract:
    """_execute_stop() must NEVER propagate exceptions from sub-calls."""

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
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        drive._pv = MagicMock()
        jog_state = MagicMock()
        jog_state.active = False
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._telemetry_poller = None
        drive._homing = MagicMock()
        return drive

    @pytest.mark.asyncio
    async def test_survives_halt_motor_failure(self, mock_drive):
        """_execute_stop must not raise when _halt_motor fails."""
        mock_drive.write_u16 = AsyncMock(side_effect=OSError("Modbus write failed"))
        mock_drive.get_status = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "quick_stop": False,
        })
        mock_drive._sm.quick_stop = AsyncMock()

        # Must not raise
        await mock_drive._execute_stop(mode="normal")
        assert mock_drive._abort_event.is_set()

    @pytest.mark.asyncio
    async def test_survives_get_status_failure(self, mock_drive):
        """_execute_stop must not raise when get_status fails."""
        mock_drive.write_u16 = AsyncMock()
        mock_drive.get_status = AsyncMock(side_effect=ConnectionResetError("TCP reset"))

        await mock_drive._execute_stop(mode="normal")
        assert mock_drive._abort_event.is_set()

    @pytest.mark.asyncio
    async def test_survives_sm_quick_stop_failure(self, mock_drive):
        """_execute_stop must not raise when sm.quick_stop fails."""
        mock_drive.write_u16 = AsyncMock()
        mock_drive.get_status = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "quick_stop": False,
        })
        mock_drive._sm.quick_stop = AsyncMock(side_effect=RuntimeError("SM failure"))

        await mock_drive._execute_stop(mode="quick")
        assert mock_drive._abort_event.is_set()

    @pytest.mark.asyncio
    async def test_skips_when_drive_not_enabled(self, mock_drive):
        """_execute_stop returns early when drive is not enabled."""
        mock_drive.write_u16 = AsyncMock()
        mock_drive.get_status = AsyncMock(return_value={
            "operation_enabled": False, "fault": False, "quick_stop": False,
        })
        mock_drive._sm.quick_stop = AsyncMock()

        await mock_drive._execute_stop(mode="normal")
        mock_drive._sm.quick_stop.assert_not_awaited()
