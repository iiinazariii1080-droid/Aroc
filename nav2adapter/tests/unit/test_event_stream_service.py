"""Unit tests for EventStreamService."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.event_bus import EventBus
from services.event_stream_service import EventStreamService


@pytest.fixture
async def event_bus():
    bus = EventBus(queue_size=100)
    yield bus
    async with bus._lock:
        bus._subscribers.clear()


@pytest.fixture
async def stream_service(event_bus):
    svc = EventStreamService(event_bus)
    yield svc
    await svc.stop()


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_cleanup_task(self, stream_service):
        await stream_service.start()
        assert stream_service._cleanup_task is not None
        assert not stream_service._cleanup_task.done()

    @pytest.mark.asyncio
    async def test_stop_cancels_cleanup_task(self, stream_service):
        await stream_service.start()
        task = stream_service._cleanup_task
        await stream_service.stop()
        assert task.done()
        assert stream_service._cleanup_task is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self, stream_service):
        await stream_service.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self, stream_service):
        await stream_service.start()
        first_task = stream_service._cleanup_task
        await stream_service.start()  # should reuse or recreate
        assert stream_service._cleanup_task is not None


class TestSSECounter:
    @pytest.mark.asyncio
    async def test_sse_increment_and_decrement(self, stream_service):
        assert await stream_service.sse_try_increment() is True
        assert stream_service._sse_active == 1
        await stream_service.sse_decrement()
        assert stream_service._sse_active == 0

    @pytest.mark.asyncio
    async def test_sse_limit_reached(self, stream_service):
        for _ in range(EventStreamService.SSE_MAX_CLIENTS):
            assert await stream_service.sse_try_increment() is True
        assert await stream_service.sse_try_increment() is False


class TestPollQueue:
    @pytest.mark.asyncio
    async def test_get_poll_queue_creates_queue(self, stream_service):
        q = await stream_service.get_poll_queue("client-1")
        assert isinstance(q, asyncio.Queue)

    @pytest.mark.asyncio
    async def test_same_client_gets_same_queue(self, stream_service):
        q1 = await stream_service.get_poll_queue("client-1")
        q2 = await stream_service.get_poll_queue("client-1")
        assert q1 is q2

    @pytest.mark.asyncio
    async def test_different_clients_get_different_queues(self, stream_service):
        q1 = await stream_service.get_poll_queue("client-1")
        q2 = await stream_service.get_poll_queue("client-2")
        assert q1 is not q2

    @pytest.mark.asyncio
    async def test_poll_max_clients_raises(self, stream_service):
        for i in range(EventStreamService.POLL_MAX_CLIENTS):
            await stream_service.get_poll_queue(f"client-{i}")
        with pytest.raises(RuntimeError, match="Too many polling clients"):
            await stream_service.get_poll_queue("overflow")


class TestEventDelivery:
    @pytest.mark.asyncio
    async def test_events_delivered_to_poll_subscriber(self, stream_service, event_bus):
        q = await stream_service.get_poll_queue("client-1")
        event = MagicMock()
        event.type = "test"
        await event_bus.publish(event)
        received = q.get_nowait()
        assert received is event

    @pytest.mark.asyncio
    async def test_multiple_subscribers_get_same_event(self, stream_service, event_bus):
        q1 = await stream_service.get_poll_queue("client-1")
        q2 = await stream_service.get_poll_queue("client-2")
        event = MagicMock()
        event.type = "test"
        await event_bus.publish(event)
        assert q1.get_nowait() is event
        assert q2.get_nowait() is event


class TestStopCleansUpQueues:
    @pytest.mark.asyncio
    async def test_stop_clears_poll_queues(self, stream_service, event_bus):
        await stream_service.get_poll_queue("client-1")
        await stream_service.get_poll_queue("client-2")
        assert len(stream_service._poll_queues) == 2
        await stream_service.stop()
        assert len(stream_service._poll_queues) == 0
