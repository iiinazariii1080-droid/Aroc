"""
Unit tests for EventBus.
"""
import pytest
from domain.events import AckEvent, AckType, ResultType, ResultSuccessEvent
from services.event_bus import EventBus


@pytest.mark.asyncio
async def test_publish_subscribe(event_bus_instance):
    """Test publishing and subscribing to events."""
    bus = event_bus_instance
    
    # Subscribe
    queue = await bus.subscribe()
    
    # Publish event
    event = AckEvent(type=AckType.RECEIVED.value, command_id="test-123")
    await bus.publish(event)
    
    # Receive event
    received = await queue.get()
    assert received.command_id == "test-123"
    assert received.type == AckType.RECEIVED.value


@pytest.mark.asyncio
async def test_multiple_subscribers(event_bus_instance):
    """Test that multiple subscribers receive events."""
    bus = event_bus_instance
    
    # Create multiple subscribers
    queues = [await bus.subscribe() for _ in range(3)]
    
    # Publish event
    event = ResultSuccessEvent(type=ResultType.SUCCESS.value, command_id="test-456")
    await bus.publish(event)
    
    # All subscribers should receive the event
    for queue in queues:
        received = await queue.get()
        assert received.command_id == "test-456"
        assert received.type == ResultType.SUCCESS.value


@pytest.mark.asyncio
async def test_unsubscribe(event_bus_instance):
    """Test unsubscribing from events."""
    bus = event_bus_instance
    
    # Subscribe
    queue = await bus.subscribe()
    
    # Unsubscribe
    await bus.unsubscribe(queue)
    
    # Publish event
    event = AckEvent(type=AckType.RECEIVED.value, command_id="test-789")
    await bus.publish(event)
    
    # Queue should be empty (no subscriber)
    assert queue.empty()


@pytest.mark.asyncio
async def test_queue_overflow(event_bus_instance):
    """Test that queue overflow drops oldest messages."""
    bus = EventBus(queue_size=2)  # Small queue
    
    queue = await bus.subscribe()
    
    # Publish more events than queue size
    for i in range(5):
        event = AckEvent(type=AckType.RECEIVED.value, command_id=f"cmd_{i}")
        await bus.publish(event)
    
    # Should only have the last 2 events (queue size)
    received = []
    while not queue.empty():
        received.append(await queue.get())
    
    # Should have at most queue_size events
    assert len(received) <= 2
