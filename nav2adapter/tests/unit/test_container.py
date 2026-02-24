"""Tests for app/container.py — AppServices.stop()."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.container import AppServices


@pytest.fixture
def services():
    """Create AppServices with all mock dependencies."""
    return AppServices(
        symovo_client=MagicMock(close=AsyncMock()),
        transport_orchestrator=MagicMock(),
        mqtt_adapter=MagicMock(disconnect=AsyncMock()),
        command_handler=MagicMock(),
        status_publisher=MagicMock(stop=AsyncMock()),
        event_dispatcher=MagicMock(stop=AsyncMock()),
        navigation_facade=MagicMock(),
        bg_tasks=[],
    )


@pytest.mark.asyncio
async def test_stop_happy_path(services):
    await services.stop()
    services.status_publisher.stop.assert_awaited_once()
    services.event_dispatcher.stop.assert_awaited_once()
    services.mqtt_adapter.disconnect.assert_awaited_once()
    services.symovo_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_cancels_bg_tasks(services):
    task = asyncio.create_task(asyncio.sleep(100))
    services.bg_tasks.append(task)
    await services.stop()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_stop_handles_publisher_failure(services):
    services.status_publisher.stop = AsyncMock(side_effect=RuntimeError("fail"))
    await services.stop()  # Should not raise
    services.event_dispatcher.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_handles_mqtt_failure(services):
    services.mqtt_adapter.disconnect = AsyncMock(side_effect=RuntimeError("mqtt fail"))
    await services.stop()  # Should not raise
    services.symovo_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_handles_symovo_failure(services):
    services.symovo_client.close = AsyncMock(side_effect=RuntimeError("close fail"))
    await services.stop()  # Should not raise


@pytest.mark.asyncio
async def test_stop_no_mqtt_adapter(services):
    services.mqtt_adapter = None
    await services.stop()  # Should not raise
    services.symovo_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_bg_task_error(services):
    """Background task that raises on cancel should be handled."""
    async def _fail():
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            raise ValueError("died")

    task = asyncio.create_task(_fail())
    services.bg_tasks.append(task)
    await services.stop()  # Should not raise
