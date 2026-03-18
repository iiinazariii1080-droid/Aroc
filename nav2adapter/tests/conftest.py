"""
Pytest configuration and shared fixtures.
"""
import pytest
import asyncio
import tempfile
import os
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from app.config import Settings, settings
from services.state_store import StateStore
from services.event_bus import EventBus
from services.persistence_store import JsonPersistenceStore
from services.symovo_service import SymovoAgvClient
from main import app


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings with overrides."""
    return Settings(
        persistence_enabled=True,
        persistence_path="test_data/state.json",
        position_status_hz=2.0,
        navigation_status_hz=1.0,
        robot_id="test_robot",
    )


@pytest.fixture
async def state_store(test_settings: Settings) -> AsyncGenerator[StateStore, None]:
    """Create StateStore instance for testing."""
    with patch("services.state_store.settings", test_settings):
        store = StateStore()
        yield store
        if store._persistence:
            try:
                os.remove(test_settings.persistence_path)
            except:
                pass


@pytest.fixture
async def event_bus_instance() -> AsyncGenerator[EventBus, None]:
    """Create EventBus instance for testing."""
    bus = EventBus(queue_size=100)
    yield bus
    async with bus._lock:
        bus._subscribers.clear()


@pytest.fixture
async def persistence_store(temp_dir: Path) -> AsyncGenerator[JsonPersistenceStore, None]:
    """Create JsonPersistenceStore instance for testing."""
    store_path = temp_dir / "test_state.json"
    store = JsonPersistenceStore(str(store_path))
    yield store
    try:
        if store_path.exists():
            os.remove(store_path)
    except:
        pass


@pytest.fixture
def mock_symovo_client() -> MagicMock:
    """Create mock SymovoAgvClient."""
    client = MagicMock(spec=SymovoAgvClient)
    client.pose = AsyncMock(return_value={"x": 1.0, "y": 2.0, "theta": 0.5})
    client.status = AsyncMock(return_value={"pose": {"x": 1.0, "y": 2.0, "theta": 0.5}})
    client.pose_uncached = AsyncMock(return_value={"x": 1.0, "y": 2.0, "theta": 0.5})
    client.status_uncached = AsyncMock(return_value={"pose": {"x": 1.0, "y": 2.0, "theta": 0.5}})
    client.transport_get = AsyncMock(return_value={"state": 8, "id": "transport_1"})
    client.transport_move_to_pose = AsyncMock(return_value={"transport_id": "transport_1"})
    client.close = AsyncMock()
    return client


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """Create FastAPI test client."""

    async def _fake_startup(app):  # noqa: ANN001
        from services.event_stream_service import EventStreamService
        from services.safety_state_tracker import SafetyStateTracker

        mock_status_publisher = MagicMock()
        mock_status_publisher.is_running = True
        mock_status_publisher._running = True
        app.state.status_publisher = mock_status_publisher

        mock_bus = EventBus(queue_size=100)
        mock_store = StateStore()
        mock_event_stream = EventStreamService(mock_bus)
        mock_symovo = MagicMock(spec=SymovoAgvClient)
        mock_symovo.move_speed = AsyncMock(return_value={"result": None})
        mock_symovo.close = AsyncMock()

        services = MagicMock()
        services.event_bus = mock_bus
        services.state_store = mock_store
        services.event_stream = mock_event_stream
        services.command_handler = MagicMock()
        services.symovo_client = mock_symovo
        services.status_publisher = mock_status_publisher

        app.state.services = services
        app.state.symovo_client = mock_symovo
        app.state.safety_tracker = SafetyStateTracker()

    async def _fake_shutdown(app):  # noqa: ANN001
        return None

    with patch("main.startup", new=_fake_startup), \
         patch("main.shutdown", new=_fake_shutdown), \
         patch.object(settings, "teleop_enabled", False), \
         patch.object(settings, "command_api_key_disabled", True):
        with TestClient(app) as client:
            yield client


@pytest.fixture
def sample_command_id() -> str:
    """Sample command ID for testing."""
    return "test-command-123"


@pytest.fixture
def sample_transport_id() -> str:
    """Sample transport ID for testing."""
    return "transport-456"


@pytest.fixture
def sample_position_status():
    """Sample PositionStatus for testing."""
    from domain.models import PositionStatus
    return PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")


@pytest.fixture
def sample_navigation_status():
    """Sample NavigationStatus for testing."""
    from domain.models import NavigationStatus, NavigationStatusEnum
    return NavigationStatus(
        status=NavigationStatusEnum.IDLE,
        goal_id=None,
        progress_percent=0,
        eta_seconds=None,
        error_reason=None,
    )


@pytest.fixture
def sample_active_transport():
    """Sample ActiveTransport for testing."""
    from domain.models import ActiveTransport
    from datetime import datetime
    return ActiveTransport(
        command_id="test-command-123",
        transport_id="transport-456",
        state=1,
        created_at=datetime.now(),
        target_id="position_A",
    )
