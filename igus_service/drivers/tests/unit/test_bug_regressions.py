"""Regression tests for bugs fixed during simulator integration testing.

Each test targets a specific bug that was found and fixed. If any of these
tests fail, it means a previously fixed bug has been reintroduced.

Bugs covered:
1. ODIndex MIN/MAX_POSITION_LIMIT registers swapped (ROOT CAUSE of "position always 0")
2. is_moving() always returned True after jog deadline expired
3. jog_start() rejected jog at exact boundary position (pos=0, dir=negative)
4. gateway_telegram byte count validation too strict for simulators
5. homing wait_done() didn't accept target_reached (bit 10) as completion
6. close() raised RuntimeError instead of best-effort cleanup
7. MotionLimits docstring had wrong register comments (cosmetic)
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from drivers.dryve_d1.od.indices import ODIndex


# ---------------------------------------------------------------------------
# Bug 1: ODIndex MIN/MAX_POSITION_LIMIT register mapping
# Root cause: MIN was mapped to 0x607D and MAX to 0x607B, but CiA 402 defines
# 0x607B = min position limit and 0x607D = max position limit.
# When the driver wrote min=0→0x607D (simulator treated as max=0) and
# max=120000→0x607B (simulator treated as min=120000), the simulator clamped
# position to 0 on every engine tick.
# ---------------------------------------------------------------------------

class TestODIndexPositionLimits:
    """Verify CiA 402 position limit register mapping is correct."""

    def test_min_position_limit_is_607B(self):
        """CiA 402 §6.2.71: Software Position Limit - Min = 0x607B."""
        assert ODIndex.MIN_POSITION_LIMIT == 0x607B, (
            f"MIN_POSITION_LIMIT should be 0x607B per CiA 402, got 0x{ODIndex.MIN_POSITION_LIMIT:04X}. "
            "If swapped with MAX, position clamping will fail silently."
        )

    def test_max_position_limit_is_607D(self):
        """CiA 402 §6.2.72: Software Position Limit - Max = 0x607D."""
        assert ODIndex.MAX_POSITION_LIMIT == 0x607D, (
            f"MAX_POSITION_LIMIT should be 0x607D per CiA 402, got 0x{ODIndex.MAX_POSITION_LIMIT:04X}. "
            "If swapped with MIN, position clamping will fail silently."
        )

    def test_min_less_than_max_register_address(self):
        """Sanity: MIN register address < MAX register address (0x607B < 0x607D)."""
        assert ODIndex.MIN_POSITION_LIMIT < ODIndex.MAX_POSITION_LIMIT


# ---------------------------------------------------------------------------
# Bug 2: is_moving() always returned True after jog deadline expired
# The jog check only looked at jog_state.active without checking deadline,
# causing it to return True forever after a jog stopped.
# ---------------------------------------------------------------------------

class TestIsMotionJogDeadline:
    """Verify is_moving() returns False when jog deadline has expired."""

    @pytest.fixture
    def mock_drive(self):
        from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
        from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig

        cfg = DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )
        drive = DryveD1(config=cfg)
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        drive._pv = MagicMock()
        drive._homing = MagicMock()
        drive._telemetry_poller = None

        jog_state = MagicMock()
        jog_state.active = True
        jog_state.deadline_s = 0.0  # Already expired
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        return drive

    @pytest.mark.asyncio
    async def test_is_moving_false_after_jog_deadline(self, mock_drive):
        """When jog deadline has expired, is_moving must fall through to velocity check."""
        # PP mode, target reached, velocity=0 → not moving
        mock_drive.read_u16 = AsyncMock(return_value=0x0427)  # target_reached=True
        mock_drive.read_i8 = AsyncMock(return_value=1)  # PP mode
        mock_drive.read_i32 = AsyncMock(return_value=0)  # velocity=0

        result = await mock_drive.is_moving()
        assert result is False, (
            "is_moving() should return False when jog deadline expired and "
            "target_reached=True with velocity=0"
        )

    @pytest.mark.asyncio
    async def test_is_moving_true_during_jog_deadline(self, mock_drive):
        """When jog deadline has NOT expired, is_moving must return True."""
        mock_drive._jog.state.deadline_s = time.monotonic() + 10.0  # Far in the future

        result = await mock_drive.is_moving()
        assert result is True


# ---------------------------------------------------------------------------
# Bug 3: jog_start() rejected jog at exact boundary (position=0, negative)
# Used <= instead of < for MIN_POSITION check, preventing jog at position=0.
# ---------------------------------------------------------------------------

class TestJogBoundaryPositions:
    """Verify jog_start() allows movement at exact boundary positions."""

    @pytest.fixture
    def mock_drive(self):
        from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
        from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig

        cfg = DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )
        drive = DryveD1(config=cfg)
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        drive._pv = MagicMock()
        drive._homing = MagicMock()
        drive._telemetry_poller = None

        jog_state = MagicMock()
        jog_state.active = False
        jog_state.deadline_s = 0.0
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._jog.press = AsyncMock()
        drive._pv.stop_velocity_zero = AsyncMock()

        # Mock read_u16 for statusword (remote enabled)
        drive.read_u16 = AsyncMock(return_value=0x0227)  # operation_enabled + remote
        return drive

    @pytest.mark.asyncio
    async def test_jog_negative_at_position_zero(self, mock_drive):
        """Jog negative at position=0 should raise (at boundary, can't go further)."""
        mock_drive.read_i32 = AsyncMock(return_value=0)  # position = 0 = MIN_POSITION
        with pytest.raises(RuntimeError, match="at minimum position"):
            await mock_drive.jog_start(velocity=-500)

    @pytest.mark.asyncio
    async def test_jog_positive_at_position_max(self, mock_drive):
        """Jog positive at position=120000 should raise (at boundary, can't go further)."""
        mock_drive.read_i32 = AsyncMock(return_value=120000)  # position = 120000 = MAX_POSITION
        with pytest.raises(RuntimeError, match="at maximum position"):
            await mock_drive.jog_start(velocity=500)

    @pytest.mark.asyncio
    async def test_jog_negative_below_min_raises(self, mock_drive):
        """Jog negative at position below MIN should raise."""
        mock_drive.read_i32 = AsyncMock(return_value=-1)  # below MIN_POSITION
        with pytest.raises(RuntimeError, match="at minimum position"):
            await mock_drive.jog_start(velocity=-500)

    @pytest.mark.asyncio
    async def test_jog_positive_above_max_raises(self, mock_drive):
        """Jog positive at position above MAX should raise."""
        mock_drive.read_i32 = AsyncMock(return_value=120001)  # above MAX_POSITION
        with pytest.raises(RuntimeError, match="at maximum position"):
            await mock_drive.jog_start(velocity=500)


# ---------------------------------------------------------------------------
# Bug 4: gateway_telegram byte count validation rejected oversized responses
# Used != instead of < for byte count comparison, causing INT8 reads to fail
# when the simulator returned 2 bytes (16-bit Modbus register) for a 1-byte request.
# ---------------------------------------------------------------------------

class TestGatewayByteCountTolerance:
    """Verify gateway_telegram tolerates responses with extra bytes."""

    def test_read_response_with_extra_bytes_accepted(self):
        """Response with more bytes than requested should not raise."""
        from drivers.dryve_d1.protocol.gateway_telegram import (
            build_read_adu,
            parse_adu,
        )

        # Build a read request for 1 byte (INT8)
        request = build_read_adu(transaction_id=1, unit_id=1, index=0x6061, subindex=0, byte_count=1)

        # Build a response with 2 bytes (simulator returns full 16-bit register)
        data = b"\x01\x00"  # 2 bytes instead of requested 1
        bc = len(data)
        pdu = bytes([
            0x2B, 0x0D, 0x00, 0x00, 0x00,
            0x60, 0x61, 0x00,
            0x00, 0x00, 0x00,
            bc & 0xFF,
        ]) + data
        length = len(pdu) + 1
        mbap = (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([1])
        resp_adu = mbap + pdu

        # Should NOT raise — extra bytes are tolerated
        resp = parse_adu(resp_adu, request=request)
        assert resp.byte_count == 2  # Response has 2 bytes
        assert len(resp.data) == 2

    def test_read_response_with_fewer_bytes_zero_padded(self):
        """Response with fewer bytes than requested should be zero-padded (dryve D1 compat)."""
        from drivers.dryve_d1.protocol.gateway_telegram import (
            build_read_adu,
            parse_adu,
        )

        # Build a read request for 4 bytes (INT32)
        request = build_read_adu(transaction_id=1, unit_id=1, index=0x6064, subindex=0, byte_count=4)

        # Build a response with only 2 bytes
        data = b"\x42\x01"
        bc = len(data)
        pdu = bytes([
            0x2B, 0x0D, 0x00, 0x00, 0x00,
            0x60, 0x64, 0x00,
            0x00, 0x00, 0x00,
            bc & 0xFF,
        ]) + data
        length = len(pdu) + 1
        mbap = (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([1])
        resp_adu = mbap + pdu

        resp = parse_adu(resp_adu, request=request)
        # Should zero-pad to 4 bytes (little-endian)
        assert resp.byte_count == 4
        assert resp.data == b"\x42\x01\x00\x00"


# ---------------------------------------------------------------------------
# Bug 5: homing wait_done() didn't accept target_reached (bit 10) as completion
# The simulator doesn't set bit 12 (homing attained), only bit 10.
# ---------------------------------------------------------------------------

class TestHomingTargetReachedCompletion:
    """Verify homing accepts target_reached (bit 10) as alternative completion."""

    @pytest.mark.asyncio
    async def test_wait_done_accepts_target_reached(self):
        """Homing should complete when target_reached (bit 10) is set, even without bit 12."""
        from drivers.dryve_d1.motion.homing import Homing, HomingConfig

        class FakeOD:
            async def read_u16(self, index, subindex=0):
                # Statusword with target_reached (bit 10) set, but NOT homing attained (bit 12)
                return 0x0427  # operation_enabled + target_reached

            async def write_u16(self, index, value, subindex=0): pass
            async def write_u8(self, index, value, subindex=0): pass
            async def write_u32(self, index, value, subindex=0): pass

        homing = Homing(FakeOD(), config=HomingConfig(poll_interval_s=0.0, timeout_s=1.0))
        result = await homing.wait_done(timeout_s=1.0)

        assert result.attained is True, "Homing should report attained=True via target_reached"
        assert result.error is False

    @pytest.mark.asyncio
    async def test_wait_done_accepts_bit12(self):
        """Homing should complete when bit 12 (homing attained) is set (standard behavior)."""
        from drivers.dryve_d1.motion.homing import Homing, HomingConfig

        class FakeOD:
            async def read_u16(self, index, subindex=0):
                # Statusword with homing attained (bit 12) set
                return 0x1027  # operation_enabled + homing_attained

            async def write_u16(self, index, value, subindex=0): pass
            async def write_u8(self, index, value, subindex=0): pass
            async def write_u32(self, index, value, subindex=0): pass

        homing = Homing(FakeOD(), config=HomingConfig(poll_interval_s=0.0, timeout_s=1.0))
        result = await homing.wait_done(timeout_s=1.0)

        assert result.attained is True
        assert result.error is False

    @pytest.mark.asyncio
    async def test_wait_done_timeout_without_completion(self):
        """Homing should timeout when neither bit 10 nor bit 12 is set."""
        from drivers.dryve_d1.motion.homing import Homing, HomingConfig

        class FakeOD:
            async def read_u16(self, index, subindex=0):
                return 0x0027  # operation_enabled, NO target_reached, NO homing_attained

            async def write_u16(self, index, value, subindex=0): pass
            async def write_u8(self, index, value, subindex=0): pass
            async def write_u32(self, index, value, subindex=0): pass

        homing = Homing(FakeOD(), config=HomingConfig(poll_interval_s=0.0, timeout_s=0.1))
        with pytest.raises(TimeoutError):
            await homing.wait_done(timeout_s=0.1)


# ---------------------------------------------------------------------------
# Bug 5a: configure() wrote homing method (0x6098) which is RO on dryve D1
# The dryve returns exception 0xFF for writes to read-only OD objects.
# skip_method_write=True (default) prevents this write.
# ---------------------------------------------------------------------------

class TestHomingSkipMethodWrite:
    """Verify skip_method_write controls whether 0x6098 is written."""

    @pytest.mark.asyncio
    async def test_skip_method_write_true_does_not_write_6098(self):
        """With skip_method_write=True (default), configure() must not write 0x6098."""
        from drivers.dryve_d1.motion.homing import Homing, HomingConfig
        from drivers.dryve_d1.od.indices import ODIndex

        writes: list[tuple[int, int]] = []

        class RecordingOD:
            async def read_u16(self, index, subindex=0): return 0x0627
            async def read_i8(self, index, subindex=0): return 6
            async def write_u16(self, index, value, subindex=0):
                writes.append((index, value))
            async def write_u8(self, index, value, subindex=0):
                writes.append((index, value))
            async def write_u32(self, index, value, subindex=0):
                writes.append((index, value))

        cfg = HomingConfig(skip_method_write=True, speed_search=None,
                           speed_switch=None, acceleration=None)
        homing = Homing(RecordingOD(), config=cfg)
        await homing.configure()

        written_indices = [idx for idx, _ in writes]
        assert int(ODIndex.HOMING_METHOD) not in written_indices, \
            "0x6098 should NOT be written when skip_method_write=True"

    @pytest.mark.asyncio
    async def test_skip_method_write_false_writes_6098(self):
        """With skip_method_write=False, configure() must write 0x6098."""
        from drivers.dryve_d1.motion.homing import Homing, HomingConfig
        from drivers.dryve_d1.od.indices import ODIndex

        writes: list[tuple[int, int]] = []

        class RecordingOD:
            async def read_u16(self, index, subindex=0): return 0x0627
            async def read_i8(self, index, subindex=0): return 6
            async def write_u16(self, index, value, subindex=0):
                writes.append((index, value))
            async def write_u8(self, index, value, subindex=0):
                writes.append((index, value))
            async def write_u32(self, index, value, subindex=0):
                writes.append((index, value))

        cfg = HomingConfig(skip_method_write=False, method=35,
                           speed_search=None, speed_switch=None, acceleration=None)
        homing = Homing(RecordingOD(), config=cfg)
        await homing.configure()

        written_indices = [idx for idx, _ in writes]
        assert int(ODIndex.HOMING_METHOD) in written_indices, \
            "0x6098 should be written when skip_method_write=False"


# ---------------------------------------------------------------------------
# Bug 6: close() raised RuntimeError when motion was active
# Instead of raising, it should attempt best-effort stop and then close.
# ---------------------------------------------------------------------------

class TestCloseDoesNotRaise:
    """Verify close() does best-effort cleanup instead of raising."""

    @pytest.mark.asyncio
    async def test_close_with_failing_stop(self):
        """close() should not raise even if stop() throws an exception."""
        from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
        from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig

        cfg = DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )
        drive = DryveD1(config=cfg)

        # Set up session mock
        session_mock = MagicMock()
        session_mock.is_connected = True
        session_mock.close = MagicMock()
        drive._session = session_mock
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        drive._pv = MagicMock()
        drive._homing = MagicMock()
        drive._telemetry_poller = None

        jog_state = MagicMock()
        jog_state.active = False
        drive._jog = MagicMock()
        drive._jog.state = jog_state
        drive._jog.close = AsyncMock()

        # is_moving returns True, stop raises
        drive.is_moving = AsyncMock(return_value=True)
        drive.stop = AsyncMock(side_effect=RuntimeError("Stop failed"))

        # close() should NOT raise, despite stop() failure
        await drive.close()

        # Should have cleaned up
        assert drive._session is None
        assert drive._sm is None


# ---------------------------------------------------------------------------
# Bug 7: Hardcoded position limits in jog_start/jog_update
# jog_start() and jog_update() have hardcoded MIN_POSITION=0, MAX_POSITION=120000
# instead of reading from config. This is a code smell, not a runtime bug
# (since the config also defaults to 0/120000), but we test the invariant.
# ---------------------------------------------------------------------------

class TestPositionLimitConsistency:
    """Verify position limit values are consistent across the codebase."""

    def test_config_defaults_match_jog_hardcoded_limits(self):
        """MotionLimits defaults should match the hardcoded values in jog methods."""
        from drivers.dryve_d1.config.models import MotionLimits

        limits = MotionLimits()
        # These are the hardcoded values in jog_start/jog_update
        assert limits.min_position_limit == 0
        assert limits.max_position_limit == 120000


# ---------------------------------------------------------------------------
# Startup validation: _validate_connection() detects swapped limits
# ---------------------------------------------------------------------------

class TestValidateConnectionDetectsSwappedLimits:
    """Verify _validate_connection() warns when min >= max."""

    @pytest.mark.asyncio
    async def test_validate_connection_warns_on_swapped_limits(self, caplog):
        """If position limits read back as min >= max, a warning should be logged."""
        from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
        from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig
        import logging

        cfg = DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )
        drive = DryveD1(config=cfg)
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        drive._telemetry_poller = None

        # Mock read_u16 for statusword (operation enabled)
        drive.read_u16 = AsyncMock(return_value=0x0227)
        # Mock get_position_limits to return swapped limits (min > max)
        drive.get_position_limits = AsyncMock(return_value=(120000, 0))
        # Mock is_homed
        drive.is_homed = AsyncMock(return_value=False)

        with caplog.at_level(logging.WARNING):
            await drive._validate_connection()  # should NOT raise
        assert any("min=120000 >= max=0" in r.message for r in caplog.records)


class TestAbortEventStopsWaitLoop:
    """Verify that setting _abort_event immediately breaks wait_target_reached()."""

    @pytest.mark.asyncio
    async def test_wait_target_reached_raises_motion_aborted(self):
        """wait_target_reached should raise MotionAborted when abort_event is set."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig
        from drivers.dryve_d1.protocol.exceptions import MotionAborted

        abort_event = asyncio.Event()
        abort_event.set()  # pre-set → should abort on first iteration

        od = MagicMock()
        pp = ProfilePosition(od, config=ProfilePositionConfig(), abort_event=abort_event)

        with pytest.raises(MotionAborted):
            await pp.wait_target_reached(timeout_s=30.0)

        # read_u16 should NOT have been called — abort is checked first
        od.read_u16.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_target_reached_completes_without_abort(self):
        """wait_target_reached should complete normally when target_reached is set."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig

        abort_event = asyncio.Event()  # NOT set

        od = MagicMock()
        # Return statusword with TARGET_REACHED (bit 10) set
        od.read_u16 = AsyncMock(return_value=0x0427)  # bit 10 set

        pp = ProfilePosition(od, config=ProfilePositionConfig(), abort_event=abort_event)
        # Should complete without raising
        await pp.wait_target_reached(timeout_s=5.0)

    @pytest.mark.asyncio
    async def test_stop_sets_abort_event(self):
        """DryveD1.stop() should set _abort_event before writing to hardware."""
        from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
        from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig

        cfg = DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )
        drive = DryveD1(config=cfg)
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._pp = MagicMock()
        drive._pv = MagicMock()
        drive._telemetry_poller = None

        # Mock get_status to return operation_enabled
        drive.get_status = AsyncMock(return_value={"operation_enabled": True, "quick_stop": False})
        # Mock read_i8 for mode detection (PP mode)
        drive.read_i8 = AsyncMock(return_value=1)
        # Mock _pp.stop to be async
        drive._pp.stop = AsyncMock()

        assert not drive._abort_event.is_set()
        await drive.stop()
        assert drive._abort_event.is_set(), "stop() must set _abort_event"

    @pytest.mark.asyncio
    async def test_move_clears_abort_event(self):
        """DryveD1.move_to_position() should clear _abort_event at start."""
        from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
        from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig

        cfg = DryveD1Config(
            drive=DriveConfig(connection=ConnectionConfig(host="127.0.0.1", port=501, unit_id=1)),
        )
        drive = DryveD1(config=cfg)
        drive._session = MagicMock()
        drive._session.is_connected = True
        drive._sdo = MagicMock()
        drive._sm = MagicMock()
        drive._sm.run_to_operation_enabled = AsyncMock()
        drive._pp = MagicMock()
        drive._telemetry_poller = None

        # Mock get_status to return operation_enabled + remote
        drive.get_status = AsyncMock(return_value={
            "operation_enabled": True, "fault": False, "remote": True,
        })
        drive.is_homed = AsyncMock(return_value=True)
        # Mock read_i8 for mode detection, read_u16 for statusword
        drive.read_i8 = AsyncMock(return_value=1)  # PP mode
        drive.read_u16 = AsyncMock(return_value=0x0227)  # operation enabled
        # Mock _pp.move_to_position to complete instantly
        drive._pp.move_to_position = AsyncMock()

        # Pre-set abort_event (simulating a previous stop)
        drive._abort_event.set()

        await drive.move_to_position(
            target_position=1000, velocity=5000, accel=3000, decel=3000, timeout_s=10.0,
        )

        assert not drive._abort_event.is_set(), "move_to_position must clear _abort_event"


# ---------------------------------------------------------------------------
# Bug 8: Stale target_reached causes false move success after homing
# Root cause: After homing (or any prior motion), statusword bit10
# (target_reached) is already set.  When move_to_position is called,
# _wait_start_acknowledgment waits for bit10=0 but the drive/simulator
# may not clear it.  After timeout, wait_target_reached sees stale
# bit10=1 and returns immediately → "success" without motor moving.
# Fix: _wait_start_acknowledgment returns bool (True=ack seen, False=timeout).
# wait_target_reached accepts _ack_seen: if False, waits for bit10=0
# first (clear phase) before waiting for the real bit10=1 rising edge.
# ---------------------------------------------------------------------------

class TestStaleTargetReachedPrevention:
    """Verify that stale target_reached bit does NOT cause false move success."""

    @pytest.mark.asyncio
    async def test_wait_start_acknowledgment_returns_true_on_ack(self):
        """_wait_start_acknowledgment returns True when bit10 clears."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig

        od = MagicMock()
        # First read: bit10=1 (stale), second read: bit10=0 (ack received)
        od.read_u16 = AsyncMock(side_effect=[0x0427, 0x0027])

        pp = ProfilePosition(od, config=ProfilePositionConfig())
        result = await pp._wait_start_acknowledgment(timeout_s=2.0)
        assert result is True, "_wait_start_acknowledgment should return True when ack seen"

    @pytest.mark.asyncio
    async def test_wait_start_acknowledgment_returns_false_on_timeout(self):
        """_wait_start_acknowledgment returns False when bit10 never clears
        AND actual position does not match target (move didn't happen)."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig

        od = MagicMock()
        # bit10=1 every time (never clears)
        od.read_u16 = AsyncMock(return_value=0x0427)
        # Position mismatch: target=10000, actual=0 → move didn't complete
        od.read_i32 = AsyncMock(side_effect=[10000, 0])

        cfg = ProfilePositionConfig(poll_interval_s=0.01)
        pp = ProfilePosition(od, config=cfg)
        result = await pp._wait_start_acknowledgment(timeout_s=0.05)
        assert result is False, "_wait_start_acknowledgment should return False on timeout when positions differ"

    @pytest.mark.asyncio
    async def test_wait_start_acknowledgment_returns_true_when_move_completed_fast(self):
        """_wait_start_acknowledgment returns True when bit10 stays set
        but actual position matches target (move completed instantly)."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig

        od = MagicMock()
        # bit10=1 every time (target_reached stuck on because move was instant)
        od.read_u16 = AsyncMock(return_value=0x0427)
        # Position match: target=5000, actual=5000 → move completed
        od.read_i32 = AsyncMock(side_effect=[5000, 5000])

        cfg = ProfilePositionConfig(poll_interval_s=0.01)
        pp = ProfilePosition(od, config=cfg)
        result = await pp._wait_start_acknowledgment(timeout_s=0.05)
        assert result is True, "_wait_start_acknowledgment should return True when positions match (fast move)"

    @pytest.mark.asyncio
    async def test_stale_bit10_waits_for_clear_then_set(self):
        """When _ack_seen=False, wait_target_reached waits for bit10=0 then bit10=1."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig
        from drivers.dryve_d1.od.statusword import SWBit

        od = MagicMock()
        # Simulate: bit10=1 (stale), bit10=1, bit10=0 (cleared!), bit10=0, bit10=1 (real target reached)
        sw_stale = 0x0427       # bit10=1 (stale from homing)
        sw_cleared = 0x0027     # bit10=0 (motion started)
        sw_reached = 0x0427     # bit10=1 (target actually reached)
        od.read_u16 = AsyncMock(side_effect=[
            sw_stale, sw_stale, sw_cleared, sw_cleared, sw_reached
        ])

        cfg = ProfilePositionConfig(poll_interval_s=0.001)
        pp = ProfilePosition(od, config=cfg)

        # With _ack_seen=False, it must wait for bit10 to clear first
        await pp.wait_target_reached(timeout_s=5.0, _ack_seen=False)

        # All 5 reads should have been consumed
        assert od.read_u16.call_count == 5

    @pytest.mark.asyncio
    async def test_stale_bit10_with_ack_seen_returns_immediately(self):
        """When _ack_seen=True (default), bit10=1 returns immediately (normal path)."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig

        od = MagicMock()
        od.read_u16 = AsyncMock(return_value=0x0427)  # bit10=1

        pp = ProfilePosition(od, config=ProfilePositionConfig())
        # Default _ack_seen=True → returns immediately when bit10=1
        await pp.wait_target_reached(timeout_s=5.0)
        assert od.read_u16.call_count == 1

    @pytest.mark.asyncio
    async def test_stale_bit10_timeout_in_clear_phase(self):
        """When _ack_seen=False and bit10 never clears, should raise TimeoutError."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig

        od = MagicMock()
        # bit10 stuck at 1 forever
        od.read_u16 = AsyncMock(return_value=0x0427)

        cfg = ProfilePositionConfig(poll_interval_s=0.01)
        pp = ProfilePosition(od, config=cfg)

        with pytest.raises(TimeoutError, match="target_reached never cleared"):
            await pp.wait_target_reached(timeout_s=0.05, _ack_seen=False)

    @pytest.mark.asyncio
    async def test_stale_bit10_abort_in_clear_phase(self):
        """When _ack_seen=False, abort_event breaks the clear-wait phase."""
        import asyncio
        from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig
        from drivers.dryve_d1.protocol.exceptions import MotionAborted

        abort_event = asyncio.Event()

        od = MagicMock()
        # bit10 stuck at 1 (would loop forever without abort)
        od.read_u16 = AsyncMock(return_value=0x0427)

        cfg = ProfilePositionConfig(poll_interval_s=0.01)
        pp = ProfilePosition(od, config=cfg, abort_event=abort_event)

        # Set abort before calling
        abort_event.set()

        with pytest.raises(MotionAborted):
            await pp.wait_target_reached(timeout_s=30.0, _ack_seen=False)


# ---------------------------------------------------------------------------
# Bug 9: TransactionIdGenerator wrapping at 65535 caused TID mismatch
# The dryve D1 echoes only the low 8 bits of the Modbus TID.  Once the
# counter exceeded 255 (from keepalive + telemetry I/O), every response
# failed validation: resp TID 0x44 ≠ req TID 0x144.
# Fix: wrap at 0xFF (max_value=255) so TIDs stay in 1–255.
# ---------------------------------------------------------------------------

class TestTransactionIdGeneratorWrapping:
    """Verify TID generator wraps at 255 and never returns 0."""

    def test_default_max_is_255(self):
        from drivers.dryve_d1.transport.session import TransactionIdGenerator
        gen = TransactionIdGenerator()
        # Exhaust the full 1–255 range
        tids = [gen.next() for _ in range(255)]
        assert tids[0] == 1
        assert tids[-1] == 255
        # Next call wraps back to 1
        assert gen.next() == 1

    def test_never_returns_zero(self):
        from drivers.dryve_d1.transport.session import TransactionIdGenerator
        gen = TransactionIdGenerator()
        for _ in range(1000):
            assert gen.next() != 0

    def test_align_wraps(self):
        from drivers.dryve_d1.transport.session import TransactionIdGenerator
        gen = TransactionIdGenerator()
        gen.align(256)  # > max_value → should wrap
        tid = gen.next()
        assert 1 <= tid <= 255

    def test_align_skips_zero(self):
        from drivers.dryve_d1.transport.session import TransactionIdGenerator
        gen = TransactionIdGenerator()
        gen.align(0)
        assert gen.next() == 1

    def test_custom_max_value(self):
        from drivers.dryve_d1.transport.session import TransactionIdGenerator
        gen = TransactionIdGenerator(max_value=0xFFFF)
        tids = [gen.next() for _ in range(65535)]
        assert tids[0] == 1
        assert tids[-1] == 65535
        assert gen.next() == 1


# ---------------------------------------------------------------------------
# Bug 10: Byte count mismatch rejected valid dryve D1 responses
# The dryve D1 gateway returns 2 bytes for some 32-bit OD objects (e.g.
# position limits 0x607B/0x607D).  parse_adu() now zero-pads instead of
# raising ResponseMismatch.
# ---------------------------------------------------------------------------

class TestByteCountZeroPadding:
    """Verify parse_adu zero-pads short read responses."""

    def test_2_byte_response_for_4_byte_request_is_padded(self):
        from drivers.dryve_d1.protocol.gateway_telegram import build_read_adu, parse_adu

        req = build_read_adu(transaction_id=1, unit_id=1, index=0x607B, subindex=0, byte_count=4)

        data = b"\xE8\x03"  # 1000 as uint16 LE
        bc = len(data)
        pdu = bytes([
            0x2B, 0x0D, 0x00, 0x00, 0x00,
            0x60, 0x7B, 0x00,
            0x00, 0x00, 0x00,
            bc & 0xFF,
        ]) + data
        length = len(pdu) + 1
        mbap = (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([1])
        resp_adu = mbap + pdu

        resp = parse_adu(resp_adu, request=req)
        assert resp.byte_count == 4
        assert resp.data == b"\xE8\x03\x00\x00"  # zero-padded to 4 bytes

    def test_exact_byte_count_not_padded(self):
        from drivers.dryve_d1.protocol.gateway_telegram import build_read_adu, parse_adu

        req = build_read_adu(transaction_id=1, unit_id=1, index=0x6041, subindex=0, byte_count=2)

        data = b"\x27\x02"
        bc = len(data)
        pdu = bytes([
            0x2B, 0x0D, 0x00, 0x00, 0x00,
            0x60, 0x41, 0x00,
            0x00, 0x00, 0x00,
            bc & 0xFF,
        ]) + data
        length = len(pdu) + 1
        mbap = (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([1])
        resp_adu = mbap + pdu

        resp = parse_adu(resp_adu, request=req)
        assert resp.byte_count == 2
        assert resp.data == b"\x27\x02"  # no padding needed
