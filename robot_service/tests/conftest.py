"""Shared fixtures for robot_service tests.

Patches startup/shutdown to avoid hitting real hardware (xArm WS, MQTT, aiohttp).
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)


@pytest.fixture
def noop_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable startup/shutdown so TestClient doesn't touch hardware."""
    from app import state as state_mod

    async def _fake_startup(app: Any) -> None:
        # Minimal state stubs so routes don't crash on missing attributes
        app.state.xarm_commands = MagicMock()
        app.state.xarm_http = MagicMock()
        app.state.igus_http = MagicMock()
        app.state.safety_layer = MagicMock()
        app.state.safety_layer.heartbeat = MagicMock()
        app.state.safety_layer.handle_message = AsyncMock()
        app.state.joystick_pipeline = MagicMock()
        app.state.joystick_pipeline.submit = MagicMock(return_value=True)
        app.state.joystick_scheduler = MagicMock()
        app.state.joystick_scheduler.submit = MagicMock(return_value=True)
        app.state.joystick_ingress = MagicMock()
        app.state.joystick_ingress.submit = MagicMock(return_value=True)
        app.state.mqtt_service = None
        app.state.conn_manager = MagicMock()

    async def _fake_shutdown(app: Any) -> None:
        pass

    monkeypatch.setattr(state_mod, "startup", _fake_startup)
    monkeypatch.setattr(state_mod, "shutdown", _fake_shutdown)


@pytest.fixture
def client(noop_startup):
    """Provide a FastAPI test client with hardware patched out."""
    from fastapi.testclient import TestClient
    import main as app_main

    with TestClient(app_main.app) as c:
        yield c
