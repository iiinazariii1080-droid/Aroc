"""Tests for routes/decorators.py — safe_getter error mapping for all exception types."""
import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException

from routes.decorators import safe_getter
from exceptions import (
    DeviceBusyError,
    RobotError,
    Conflict,
    DeviceError,
    DeviceReadyError,
    DeviceConnectionError,
    InputError,
)
from models.api_types import GenericResponse


# ── Helper: apply the decorator to a controllable handler ────────────

def _make_endpoint(side_effect=None, return_value=None):
    """Create a decorated async handler that either raises or returns."""
    @safe_getter(GenericResponse)
    async def handler():
        if side_effect:
            raise side_effect
        return return_value
    return handler


class TestSafeGetter:
    @pytest.mark.asyncio
    async def test_success_passthrough(self):
        resp = GenericResponse(status="ok")
        handler = _make_endpoint(return_value=resp)
        result = await handler()
        assert isinstance(result, GenericResponse)

    @pytest.mark.asyncio
    async def test_dict_converted_to_response_model(self):
        handler = _make_endpoint(return_value={"status": "ok"})
        result = await handler()
        assert isinstance(result, GenericResponse)

    @pytest.mark.asyncio
    async def test_device_busy_429(self):
        handler = _make_endpoint(side_effect=DeviceBusyError("busy"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_robot_error_422(self):
        handler = _make_endpoint(side_effect=RobotError("bad"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_conflict_409(self):
        handler = _make_endpoint(side_effect=Conflict("conflict"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_device_error_409(self):
        handler = _make_endpoint(side_effect=DeviceError("dev err"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_device_ready_error_409(self):
        handler = _make_endpoint(side_effect=DeviceReadyError("not ready"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_device_connection_error_503(self):
        handler = _make_endpoint(side_effect=DeviceConnectionError("timeout"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_input_error_404(self):
        handler = _make_endpoint(side_effect=InputError("no such"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_http_exception_passthrough(self):
        handler = _make_endpoint(side_effect=HTTPException(status_code=418, detail="teapot"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 418

    @pytest.mark.asyncio
    async def test_generic_exception_500(self):
        handler = _make_endpoint(side_effect=ValueError("unexpected"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_malformed_result_503(self):
        """Non-response_model that can't be coerced raises 503."""
        handler = _make_endpoint(return_value=object())
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_error_envelope_shape(self):
        handler = _make_endpoint(side_effect=DeviceError("test msg"))
        with pytest.raises(HTTPException) as exc_info:
            await handler()
        detail = exc_info.value.detail
        assert "error" in detail
        assert detail["error"]["type"] == "DeviceError"
        assert detail["error"]["msg"] == "test msg"
