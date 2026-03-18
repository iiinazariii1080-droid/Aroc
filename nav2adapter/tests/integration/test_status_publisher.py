"""
Integration tests for StatusPublisher and extracted publishing helpers.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from services.status_publisher import StatusPublisher
from services.status_publishing import publish_nav_status
from domain.models import PositionStatus, NavigationStatus, NavigationStatusEnum


@pytest.mark.asyncio
async def test_status_publisher_start_stop(mock_symovo_client, event_bus_instance, state_store):
    """Test starting and stopping StatusPublisher."""
    publisher = StatusPublisher(
        symovo_client=mock_symovo_client,
        bus=event_bus_instance,
        state_store=state_store,
    )

    # Start
    await publisher.start()
    assert publisher._running_flag.is_set()
    assert len(publisher._tasks) == 3  # transport_manager + position_poller + status_poller

    # Stop
    await publisher.stop()
    assert not publisher._running_flag.is_set()
    assert len(publisher._tasks) == 0


@pytest.mark.asyncio
async def test_publish_position_status(mock_symovo_client, event_bus_instance, state_store):
    """Test publishing position status via state_store."""
    position = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")

    # Directly test the state_store publish pattern
    await state_store.set_last_position_status(position)

    # Verify it was saved to state_store
    saved_position = await state_store.get_last_position_status()
    assert saved_position is not None
    assert saved_position.x == 1.0
    assert saved_position.y == 2.0


@pytest.mark.asyncio
async def test_publish_navigation_status(mock_symovo_client, event_bus_instance, state_store):
    """Test publishing navigation status via publish_nav_status helper."""
    nav_status = NavigationStatus(
        status=NavigationStatusEnum.NAVIGATING,
        goal_id="cmd_123",
        progress_percent=50,
        eta_seconds=10.0,
        error_reason=None,
    )

    await publish_nav_status(nav_status, state_store)

    # Verify it was saved to state_store
    saved_status = await state_store.get_last_navigation_status()
    assert saved_status is not None
    assert saved_status.status == NavigationStatusEnum.NAVIGATING
    assert saved_status.goal_id == "cmd_123"
