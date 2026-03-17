"""Tests for production-readiness fixes (P0-P2).

Covers: validation constraints, SSE connection limiter, client_id validation,
container double-stop guard, persistence eviction, exception error_code,
MQTT password masking, navigate lock timeout.
"""
import asyncio
import math
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# ─── Teleop / AEHub validation constraints ───────────────────────────

from app.teleop_server import MoveSpeedBody
from routes.aehub import MoveSpeedRequest
from pydantic import ValidationError


class TestMoveSpeedBodyValidation:
    """P0-2: MoveSpeedBody must reject out-of-range speed/duration."""

    def test_valid_defaults(self):
        body = MoveSpeedBody()
        assert body.speed is None

    def test_speed_in_range(self):
        body = MoveSpeedBody(speed=1.5, angular_speed=-2.0, duration=1.0)
        assert body.speed == 1.5

    def test_speed_too_high(self):
        with pytest.raises(ValidationError):
            MoveSpeedBody(speed=5.0)

    def test_speed_too_low(self):
        with pytest.raises(ValidationError):
            MoveSpeedBody(speed=-5.0)

    def test_angular_speed_too_high(self):
        with pytest.raises(ValidationError):
            MoveSpeedBody(angular_speed=10.0)

    def test_duration_too_long(self):
        with pytest.raises(ValidationError):
            MoveSpeedBody(duration=100.0)

    def test_duration_too_short(self):
        with pytest.raises(ValidationError):
            MoveSpeedBody(duration=0.001)

    def test_linear_dir_clamped(self):
        with pytest.raises(ValidationError):
            MoveSpeedBody(linear_dir=2)

    def test_angular_dir_valid(self):
        body = MoveSpeedBody(angular_dir=-1)
        assert body.angular_dir == -1


class TestMoveSpeedRequestValidation:
    """Same constraints on AEHub MoveSpeedRequest."""

    def test_valid(self):
        r = MoveSpeedRequest(speed=0.5)
        assert r.speed == 0.5

    def test_speed_inf_rejected(self):
        with pytest.raises(ValidationError):
            MoveSpeedRequest(speed=float("inf"))

    def test_duration_zero_rejected(self):
        with pytest.raises(ValidationError):
            MoveSpeedRequest(duration=0.0)

    def test_negative_huge_angular_rejected(self):
        with pytest.raises(ValidationError):
            MoveSpeedRequest(angular_speed=-100.0)


# ─── SSE connection limiter ──────────────────────────────────────────

import routes.aehub as aehub_mod


class TestSSEConnectionLimiter:
    """P1-5: SSE endpoints must cap concurrent connections."""

    @pytest.mark.asyncio
    async def test_sse_counter_increments_and_decrements(self):
        """Verify the counter goes up and back down after gen() finishes."""
        old = aehub_mod._sse_active

        # Simulate a gen() that immediately disconnects
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=True)

        mock_q = asyncio.Queue()
        from routes.aehub import stream_events
        with patch.object(aehub_mod, "_SSE_MAX_CLIENTS", 1000):
            with patch("routes.aehub.settings") as s, \
                 patch("routes.aehub.event_bus") as mock_bus:
                s.robot_id = "r1"
                mock_bus.subscribe = AsyncMock(return_value=mock_q)
                mock_bus.unsubscribe = AsyncMock()
                resp = await stream_events(request=request, robot_id="r1")
                chunks = []
                async for chunk in resp.body_iterator:
                    chunks.append(chunk)

        # After generator exhausted, counter must be back to original
        assert aehub_mod._sse_active == old

    @pytest.mark.asyncio
    async def test_sse_rejects_when_limit_reached(self):
        """When _sse_active >= max, gen yields error and returns."""
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)

        with patch.object(aehub_mod, "_SSE_MAX_CLIENTS", 0):
            with patch("routes.aehub.settings") as s:
                s.robot_id = "r1"
                resp = await aehub_mod.stream_events(request=request, robot_id="r1")
                chunks = []
                async for chunk in resp.body_iterator:
                    chunks.append(chunk)

        assert any(b"Too many SSE" in c for c in chunks)


# ─── client_id validation ────────────────────────────────────────────

from routes.aehub import _CLIENT_ID_RE, poll_events
from fastapi import HTTPException


class TestClientIdValidation:
    """P1-9/P2-16: client_id must be validated."""

    def test_bad_client_id_regex(self):
        assert not _CLIENT_ID_RE.match("a b c!@#")
        assert not _CLIENT_ID_RE.match("")
        assert not _CLIENT_ID_RE.match("x" * 65)

    def test_good_client_id_regex(self):
        assert _CLIENT_ID_RE.match("my-client_01")
        assert _CLIENT_ID_RE.match("default")
        assert _CLIENT_ID_RE.match("a" * 64)

    @pytest.mark.asyncio
    async def test_poll_rejects_bad_client_id(self):
        """poll_events raises 422 for invalid client_id."""
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r1"
            with pytest.raises(HTTPException) as exc_info:
                await poll_events(robot_id="r1", client_id="a b c!@#")
            assert exc_info.value.status_code == 422


# ─── Container double-stop guard ─────────────────────────────────────

from app.container import AppServices


class TestContainerDoubleStop:
    """P2-17: Calling stop() twice should be safe and idempotent."""

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self):
        svc = AppServices(
            symovo_client=MagicMock(close=AsyncMock()),
            transport_orchestrator=MagicMock(),
            mqtt_adapter=None,
            command_handler=None,
            status_publisher=MagicMock(stop=AsyncMock()),
            event_dispatcher=MagicMock(stop=AsyncMock()),
            navigation_facade=MagicMock(),
            bg_tasks=[],
        )
        await svc.stop()
        await svc.stop()  # second call should be a no-op
        # close only called once
        assert svc.symovo_client.close.await_count == 1


# ─── Persistence max-entries eviction ─────────────────────────────────

from services.persistence_store import JsonPersistenceStore, PersistedCommand
import tempfile, os, json


class TestPersistenceEviction:
    """P2-12: persistence must evict oldest when > _MAX_COMMANDS."""

    @pytest.mark.asyncio
    async def test_evicts_oldest_entries(self, tmp_path):
        store = JsonPersistenceStore(str(tmp_path / "state.json"))
        store._MAX_COMMANDS = 5

        # Insert 7 commands
        for i in range(7):
            cmd = PersistedCommand(
                command_id=f"cmd_{i:02d}",
                transport_id=f"t_{i}",
                created_at=f"2025-01-01T00:00:{i:02d}Z",
                updated_at=f"2025-01-01T00:00:{i:02d}Z",
            )
            await store.upsert(cmd)

        # Should only have 5 entries
        loaded = await store.load()
        assert len(loaded) == 5
        # Oldest (cmd_00, cmd_01) should be evicted
        assert "cmd_00" not in loaded
        assert "cmd_01" not in loaded
        assert "cmd_06" in loaded


# ─── Exception error_code ────────────────────────────────────────────

from exceptions import (
    RobotBaseError, DeviceBusyError, DeviceConnectionError,
    TransportMoveError, InputError, Conflict,
)


class TestExceptionErrorCodes:
    """P2-18: Exceptions have structured error_code field."""

    def test_base_error_default_code(self):
        e = RobotBaseError("test")
        assert e.error_code == "ROBOT_ERROR"

    def test_custom_code(self):
        e = RobotBaseError("test", error_code="CUSTOM")
        assert e.error_code == "CUSTOM"

    def test_device_busy(self):
        assert DeviceBusyError("x").error_code == "DEVICE_BUSY"

    def test_device_connection(self):
        assert DeviceConnectionError("x").error_code == "DEVICE_CONNECTION_ERROR"

    def test_transport_move(self):
        assert TransportMoveError("x").error_code == "TRANSPORT_MOVE_FAILED"

    def test_input_error(self):
        assert InputError("x").error_code == "INPUT_ERROR"

    def test_conflict(self):
        assert Conflict("x").error_code == "CONFLICT"


# ─── MQTT password masking ───────────────────────────────────────────

from services.mqtt_adapter import MqttAdapter


class TestMqttPasswordMasking:
    """P1-8: MQTT password should not appear in repr."""

    def test_repr_no_password(self):
        a = MqttAdapter.__new__(MqttAdapter)
        a.broker_host = "10.0.0.1"
        a.broker_port = 1883
        a.use_tls = False
        a.robot_id = "r1"
        a._password = "s3cr3t"
        r = repr(a)
        assert "s3cr3t" not in r
        assert "10.0.0.1" in r

    def test_password_property(self):
        a = MqttAdapter.__new__(MqttAdapter)
        a._password = "hunter2"
        assert a.password == "hunter2"


# ─── Navigate lock timeout ───────────────────────────────────────────

from services.command_handler import CommandHandler
from domain.models import NavigationCommand, NavigationStatusEnum


class TestNavigateLockTimeout:
    """P2-13: navigate lock acquisition should have a timeout."""

    @pytest.mark.asyncio
    async def test_lock_timeout_returns_error(self):
        handler = CommandHandler.__new__(CommandHandler)
        handler._navigate_lock = asyncio.Lock()
        handler._laser_cancel_event = None

        # Acquire the lock externally to simulate stuck navigation
        await handler._navigate_lock.acquire()

        cmd = NavigationCommand(
            command_id="test_cmd",
            timestamp="2025-01-01T00:00:00Z",
            target_id="position_A",
            target={"x": 0.0, "y": 0.0, "theta": 0.0},
        )

        # Temporarily reduce timeout for fast test
        with patch("services.command_handler.asyncio.timeout") as mock_timeout:
            # Make the timeout context manager raise TimeoutError immediately
            mock_timeout.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError)
            mock_timeout.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await handler.handle_drive_to_position(cmd)

        assert result.status == NavigationStatusEnum.ERROR
        assert result.error_reason == "navigate_lock_timeout"
        handler._navigate_lock.release()


# ─── Error response sanitization ─────────────────────────────────────


class TestErrorSanitization:
    """P1-6: Internal error details must not be leaked to clients."""

    @pytest.mark.asyncio
    async def test_map_png_error_is_generic(self):
        """symovo_agv error responses should not contain exception type or message."""
        from routes.symovo_agv import get_map_png
        from fastapi import HTTPException

        mock_client = AsyncMock()
        mock_client.map_png = AsyncMock(side_effect=ConnectionError("secret: host unreachable"))

        with pytest.raises(HTTPException) as exc_info:
            await get_map_png(client=mock_client, map_id=1)

        detail = exc_info.value.detail
        assert "secret" not in str(detail)
        assert "ConnectionError" not in str(detail)
        assert "controller_error" in str(detail).lower() or "Controller communication error" in str(detail)
