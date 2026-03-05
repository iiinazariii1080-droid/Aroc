"""Tests for app/state.py — _spawn_bg_task, startup/shutdown helpers."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from fastapi import FastAPI

from app.state import _spawn_bg_task, shutdown


# ── _spawn_bg_task ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_spawn_bg_task_success():
    app = FastAPI()

    async def _ok():
        return "done"

    task = _spawn_bg_task(app, _ok(), "test_ok")
    await task
    assert task.done()
    assert task.result() == "done"
    # Completed tasks are auto-removed from _bg_tasks to avoid memory leaks
    assert task not in app.state._bg_tasks


@pytest.mark.asyncio
async def test_spawn_bg_task_failure():
    """When task fails, done callback logs but doesn't crash."""
    app = FastAPI()

    async def _fail():
        raise ValueError("boom")

    task = _spawn_bg_task(app, _fail(), "test_fail")
    # Wait for completion (should not propagate)
    with pytest.raises(ValueError):
        await task
    assert task.done()


@pytest.mark.asyncio
async def test_spawn_bg_task_cancel():
    app = FastAPI()

    async def _slow():
        await asyncio.sleep(100)

    task = _spawn_bg_task(app, _slow(), "test_cancel")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_spawn_bg_task_creates_list():
    """First call creates _bg_tasks list on app.state."""
    app = FastAPI()
    assert not hasattr(app.state, "_bg_tasks")

    async def _noop():
        pass

    _spawn_bg_task(app, _noop(), "first")
    assert hasattr(app.state, "_bg_tasks")
    assert len(app.state._bg_tasks) == 1


# ── shutdown ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_shutdown_with_services():
    """When app.state.services exists, delegates to container stop."""
    from app.container import AppServices

    mock_services = MagicMock(spec=AppServices)
    mock_services.stop = AsyncMock()

    app = FastAPI()
    app.state.services = mock_services

    with patch("app.state.state_store") as mock_store:
        mock_store.stop_persistence = AsyncMock()
        await shutdown(app)

    mock_services.stop.assert_awaited_once()
    mock_store.stop_persistence.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_legacy_no_services():
    """When no container, falls back to legacy cleanup."""
    app = FastAPI()
    app.state.status_publisher = MagicMock(stop=AsyncMock())
    app.state.event_dispatcher = MagicMock(stop=AsyncMock())
    app.state.mqtt_adapter = MagicMock(disconnect=AsyncMock())
    app.state.symovo_client = MagicMock(close=AsyncMock())

    with patch("app.state.state_store") as mock_store:
        mock_store.stop_persistence = AsyncMock()
        await shutdown(app)

    app.state.status_publisher.stop.assert_awaited_once()
    app.state.event_dispatcher.stop.assert_awaited_once()
    app.state.mqtt_adapter.disconnect.assert_awaited_once()
    app.state.symovo_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_persistence_error():
    """Persistence error during shutdown doesn't propagate."""
    app = FastAPI()
    app.state.services = MagicMock(stop=AsyncMock())

    with patch("app.state.state_store") as mock_store:
        mock_store.stop_persistence = AsyncMock(side_effect=RuntimeError("db fail"))
        await shutdown(app)  # should not raise


@pytest.mark.asyncio
async def test_shutdown_empty_state():
    """When state has nothing, just runs persistence stop."""
    app = FastAPI()

    with patch("app.state.state_store") as mock_store:
        mock_store.stop_persistence = AsyncMock()
        await shutdown(app)

    mock_store.stop_persistence.assert_awaited_once()
