"""Event bus for pub/sub and SSE events."""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Event type."""
    STATUS = "status"
    STATE_CHANGE = "state_change"
    FAULT = "fault"
    COMMAND = "command"
    LOG = "log"


@dataclass
class DriveEvent:
    """Drive event model."""
    seq: int
    ts: int
    type: EventType
    payload: dict[str, Any]


class EventBus:
    """Simple event bus for pub/sub.
    
    Uses asyncio.Queue for internal pub/sub.
    Supports multiple subscribers for SSE streams.
    """
    
    def __init__(self, max_queue_size: int = 1000):
        """Initialize EventBus.
        
        Args:
            max_queue_size: Maximum queue size for events
        """
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._subscribers: list[asyncio.Queue] = []
        self._seq = 0
        self._lock = asyncio.Lock()
    
    async def publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """Publish an event.
        
        Args:
            event_type: Type of event
            payload: Event payload
        """
        async with self._lock:
            self._seq += 1
            event = DriveEvent(
                seq=self._seq,
                ts=int(time.time() * 1000),
                type=event_type,
                payload=payload,
            )
            
            # Add to internal queue
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest event if queue is full
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass
            
            # Broadcast to subscribers
            for sub_queue in self._subscribers[:]:
                try:
                    sub_queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Remove subscriber if queue is full (disconnected)
                    self._subscribers.remove(sub_queue)
                except Exception:
                    # Remove subscriber on error
                    if sub_queue in self._subscribers:
                        self._subscribers.remove(sub_queue)
    
    def subscribe(self) -> asyncio.Queue:
        """Subscribe to events.
        
        Returns:
            Queue that will receive events
        """
        sub_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(sub_queue)
        return sub_queue
    
    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from events.
        
        Args:
            queue: Queue to remove from subscribers
        """
        if queue in self._subscribers:
            self._subscribers.remove(queue)
    
    async def get_recent_events(self, limit: int = 100) -> list[DriveEvent]:
        """Get recent events from internal queue.
        
        Args:
            limit: Maximum number of events to return
        
        Returns:
            List of recent events
        """
        async with self._lock:
            events: list[DriveEvent] = []
            temp_queue: asyncio.Queue[DriveEvent] = asyncio.Queue()
            
            # Drain queue
            while not self._queue.empty():
                try:
                    event = self._queue.get_nowait()
                    events.append(event)
                    temp_queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    break
            
            # Put events back
            while not temp_queue.empty():
                try:
                    event = temp_queue.get_nowait()
                    self._queue.put_nowait(event)
                except asyncio.QueueFull:
                    break
                except asyncio.QueueEmpty:
                    break
            
            # Return most recent events
            return sorted(events, key=lambda e: e.seq, reverse=True)[:limit]
