"""
Integration tests for StatusPublisher.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from services.status_publisher import StatusPublisher
from domain.models import PositionStatus, NavigationStatus, NavigationStatusEnum


@pytest.mark.asyncio
async def test_status_publisher_start_stop(mock_symovo_client, mock_mqtt_adapter, event_bus_instance):
    """Test starting and stopping StatusPublisher."""
    publisher = StatusPublisher(
        symovo_client=mock_symovo_client,
        mqtt_adapter=mock_mqtt_adapter,
        bus=event_bus_instance,
    )
    
    # Start
    await publisher.start()
    assert publisher._running is True
    assert len(publisher._tasks) == 3  # transport_manager + position_watcher + status_cache
    
    # Stop
    await publisher.stop()
    assert publisher._running is False
    assert len(publisher._tasks) == 0


@pytest.mark.asyncio
async def test_publish_position_status(mock_symovo_client, mock_mqtt_adapter, event_bus_instance):
    """Test publishing position status."""
    from services.state_store import state_store
    
    publisher = StatusPublisher(
        symovo_client=mock_symovo_client,
        mqtt_adapter=mock_mqtt_adapter,
        bus=event_bus_instance,
    )
    
    position = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
    
    await publisher._publish_position_status(position)
    
    # Verify it was saved to state_store
    saved_position = await state_store.get_last_position_status()
    assert saved_position is not None
    assert saved_position.x == 1.0
    assert saved_position.y == 2.0
    
    # Verify it was published to MQTT
    mock_mqtt_adapter.publish_position_status.assert_called_once()


@pytest.mark.asyncio
async def test_publish_navigation_status(mock_symovo_client, mock_mqtt_adapter, event_bus_instance):
    """Test publishing navigation status."""
    from services.state_store import state_store
    
    publisher = StatusPublisher(
        symovo_client=mock_symovo_client,
        mqtt_adapter=mock_mqtt_adapter,
        bus=event_bus_instance,
    )
    
    nav_status = NavigationStatus(
        status=NavigationStatusEnum.NAVIGATING,
        goal_id="cmd_123",
        progress_percent=50,
        eta_seconds=10.0,
        error_reason=None,
    )
    
    await publisher._publish_navigation_status(nav_status)
    
    # Verify it was saved to state_store
    saved_status = await state_store.get_last_navigation_status()
    assert saved_status is not None
    assert saved_status.status == NavigationStatusEnum.NAVIGATING
    assert saved_status.goal_id == "cmd_123"
    
    # Verify it was published to MQTT
    mock_mqtt_adapter.publish_navigation_status.assert_called_once()
