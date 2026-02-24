"""Tests for app/state — shutdown cleanup paths."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI

from app.state import shutdown


@pytest.mark.asyncio
async def test_shutdown_safety_and_cm():
    """Shutdown closes safety + connection manager."""
    app = FastAPI()
    app.state.xarm_safety = MagicMock(stop=AsyncMock())
    app.state.xarm_cm = MagicMock(close=AsyncMock())
    await shutdown(app)
    app.state.xarm_safety.stop.assert_awaited_once()
    app.state.xarm_cm.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_joystick_ingress():
    """Shutdown stops joystick ingress and scheduler."""
    app = FastAPI()
    app.state.joystick_ingress = MagicMock(stop=AsyncMock())
    app.state.joystick_scheduler = MagicMock(stop=AsyncMock())
    app.state.joystick_pipeline = MagicMock(stop=AsyncMock())
    await shutdown(app)
    app.state.joystick_ingress.stop.assert_awaited_once()
    app.state.joystick_scheduler.stop.assert_awaited_once()
    app.state.joystick_pipeline.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_igus_http():
    """Shutdown closes igus HTTP client."""
    app = FastAPI()
    app.state.igus_http = MagicMock(aclose=AsyncMock())
    await shutdown(app)
    app.state.igus_http.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_safety_error():
    """Error in safety.stop doesn't propagate."""
    app = FastAPI()
    app.state.xarm_safety = MagicMock(stop=AsyncMock(side_effect=RuntimeError("err")))
    app.state.xarm_cm = MagicMock(close=AsyncMock())
    await shutdown(app)  # no exception
    app.state.xarm_cm.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_empty_state():
    """Empty state just completes."""
    app = FastAPI()
    await shutdown(app)  # no error


@pytest.mark.asyncio
async def test_shutdown_all_components_error():
    """All component shutdowns fail → outer handler catches."""
    app = FastAPI()
    app.state.xarm_safety = MagicMock(stop=AsyncMock(side_effect=RuntimeError))
    app.state.xarm_cm = MagicMock(close=AsyncMock(side_effect=RuntimeError))
    app.state.joystick_ingress = MagicMock(stop=AsyncMock(side_effect=RuntimeError))
    app.state.joystick_scheduler = MagicMock(stop=AsyncMock(side_effect=RuntimeError))
    app.state.joystick_pipeline = MagicMock(stop=AsyncMock(side_effect=RuntimeError))
    app.state.igus_http = MagicMock(aclose=AsyncMock(side_effect=RuntimeError))
    await shutdown(app)  # handles all errors
