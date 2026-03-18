"""
State store for command registry and status tracking.
"""
import asyncio
import logging
import threading
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import json
import time
from domain.models import ActiveTransport, NavigationStatus, PositionStatus, NavigationSession
from app.config import settings
from services.persistence_store import JsonPersistenceStore, PersistedCommand, PersistedSession
from services.reliability_metrics import reliability_metrics

_LOGGER = logging.getLogger(__name__)


class StateStore:
    """In-memory state store for command registry and status tracking."""

    def __init__(self):
        self._owner_thread = threading.current_thread()
        self._command_registry: Dict[str, ActiveTransport] = {}
        self._sessions: Dict[str, NavigationSession] = {}
        # Backend/UI policy: treat navigation as a single active command stream.
        # We keep one "current" command id that drives status publishing.
        self._current_command_id: Optional[str] = None
        self._last_navigation_status: Optional[NavigationStatus] = None
        self._last_navigation_status_ts: float = 0.0
        self._last_position_status: Optional[PositionStatus] = None
        # Raw cached data from Symovo controller (background-fed)
        self._last_raw_pose: Optional[Dict[str, Any]] = None
        self._last_raw_pose_ts: float = 0.0
        self._last_raw_status: Optional[Dict[str, Any]] = None
        self._last_raw_status_ts: float = 0.0
        self._lock = asyncio.Lock()
        self._persistence: Optional[JsonPersistenceStore] = None
        if settings.persistence_enabled and settings.persistence_path:
            self._persistence = JsonPersistenceStore(settings.persistence_path)
        
        # Background writer task for persistence (best-effort, non-blocking)
        self._persistence_queue: Optional[asyncio.Queue] = None
        self._persistence_task: Optional[asyncio.Task] = None
        self._persistence_running = False

    def _assert_owner_thread(self) -> None:
        """Raise if called from a thread other than the one that created the store.

        asyncio.Lock is NOT cross-thread safe, so every public async method must
        run in the same thread (the main event-loop thread).
        """
        current = threading.current_thread()
        if current is not self._owner_thread:
            raise RuntimeError(
                f"StateStore accessed from thread {current.name!r} "
                f"but was created in {self._owner_thread.name!r}. "
                "asyncio.Lock does not protect across threads."
            )

    def _iso_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    async def start_persistence(self) -> None:
        """Start background persistence writer task."""
        self._assert_owner_thread()
        if not self._persistence or self._persistence_running:
            return
        self._persistence_running = True
        self._persistence_queue = asyncio.Queue(maxsize=1000)
        self._persistence_task = asyncio.create_task(self._persistence_writer_loop())

    async def stop_persistence(self) -> None:
        """Stop background persistence writer task, draining any pending writes first."""
        self._persistence_running = False
        if self._persistence_task and self._persistence_queue:
            # Drain remaining operations (best-effort, bounded by timeout).
            try:
                deadline = 3.0  # seconds
                import time as _time
                t0 = _time.monotonic()
                while not self._persistence_queue.empty() and (_time.monotonic() - t0) < deadline:
                    try:
                        op = self._persistence_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    try:
                        op_type = op.get("type")
                        if op_type == "upsert" and self._persistence:
                            await self._persistence.upsert(op["data"])
                        elif op_type == "upsert_session" and self._persistence:
                            await self._persistence.upsert_session(op["data"])
                        elif op_type == "delete" and self._persistence:
                            await self._persistence.delete(op["command_id"])
                        elif op_type == "delete_session" and self._persistence:
                            await self._persistence.delete_session(op["command_id"])
                    except Exception:
                        _LOGGER.debug("Persistence drain: failed to flush op", exc_info=True)
                remaining = self._persistence_queue.qsize()
                if remaining:
                    _LOGGER.warning("Persistence shutdown: %d ops could not be flushed", remaining)
            except Exception:
                _LOGGER.debug("Persistence drain failed", exc_info=True)
        if self._persistence_task:
            self._persistence_task.cancel()
            try:
                await self._persistence_task
            except asyncio.CancelledError:
                pass
            self._persistence_task = None
        self._persistence_queue = None

    async def _persistence_writer_loop(self) -> None:
        """Background loop to write persistence operations asynchronously."""
        if not self._persistence_queue:
            return
        try:
            while self._persistence_running:
                try:
                    op = await asyncio.wait_for(self._persistence_queue.get(), timeout=1.0)
                    op_type = op.get("type")
                    reliability_metrics.inc("persistence.writer.dequeue")
                    started = time.perf_counter()
                    if op_type == "upsert":
                        await self._persistence.upsert(op["data"])
                        reliability_metrics.inc("persistence.writer.upsert.ok")
                        reliability_metrics.observe_duration(
                            "persistence.writer.upsert.latency_s",
                            time.perf_counter() - started,
                        )
                    elif op_type == "upsert_session":
                        await self._persistence.upsert_session(op["data"])
                        reliability_metrics.inc("persistence.writer.upsert_session.ok")
                        reliability_metrics.observe_duration(
                            "persistence.writer.upsert_session.latency_s",
                            time.perf_counter() - started,
                        )
                    elif op_type == "delete":
                        await self._persistence.delete(op["command_id"])
                        reliability_metrics.inc("persistence.writer.delete.ok")
                        reliability_metrics.observe_duration(
                            "persistence.writer.delete.latency_s",
                            time.perf_counter() - started,
                        )
                    elif op_type == "delete_session":
                        await self._persistence.delete_session(op["command_id"])
                        reliability_metrics.inc("persistence.writer.delete_session.ok")
                        reliability_metrics.observe_duration(
                            "persistence.writer.delete_session.latency_s",
                            time.perf_counter() - started,
                        )
                    else:
                        reliability_metrics.inc("persistence.writer.unknown_op")
                except asyncio.TimeoutError:
                    reliability_metrics.inc("persistence.writer.timeout")
                    continue
                except Exception:
                    # Never allow persistence failures to break runtime
                    reliability_metrics.inc("persistence.writer.failed")
                    _LOGGER.warning("Persistence write failed", exc_info=True)
        except asyncio.CancelledError:
            pass
    
    def _enqueue_persistence(self, op: Dict[str, Any]) -> None:
        """Enqueue a persistence operation (non-blocking)."""
        if not self._persistence_queue:
            reliability_metrics.inc("persistence.enqueue.skipped_no_queue")
            return
        try:
            self._persistence_queue.put_nowait(op)
            reliability_metrics.inc("persistence.enqueue.ok")
        except asyncio.QueueFull:
            # Drop if queue is full - best effort only
            reliability_metrics.inc("persistence.enqueue.drop_queue_full")
            pass

    async def load_from_persistence(self) -> int:
        """
        Load navigation sessions from persistence (for progress tracking).
        Commands are NOT loaded into state_store - they should be restored from the controller during recovery.
        This prevents tracking old/historical commands that are no longer active.
        """
        if not self._persistence:
            return 0
        persisted_sessions = await self._persistence.load_sessions()
        async with self._lock:
            self._command_registry.clear()
            self._sessions.clear()
            self._current_command_id = None
            # Only restore sessions - commands will be restored from controller during state recovery
            # Sessions are restored so that progress tracking can continue for active commands
            for command_id, s in persisted_sessions.items():
                if s and isinstance(s.start, dict) and isinstance(s.goal, dict):
                    try:
                        self._sessions[command_id] = NavigationSession(
                            command_id=command_id,
                            target_id=s.target_id,
                            start=s.start,  # type: ignore[arg-type]
                            goal=s.goal,    # type: ignore[arg-type]
                            total_dist_m=float(s.total_dist_m or 0.0),
                            min_remaining_dist_m=float(s.min_remaining_dist_m or 0.0),
                            progress_percent=int(s.progress_percent or 0),
                            created_at=datetime.fromisoformat(s.created_at) if isinstance(s.created_at, str) else datetime.now(timezone.utc),
                            updated_at=datetime.fromisoformat(s.updated_at) if isinstance(s.updated_at, str) else datetime.now(timezone.utc),
                        )
                    except Exception:
                        # best-effort; ignore corrupt session entries
                        pass
        return len(self._sessions)
    
    async def get_persisted_commands_for_recovery(self) -> Dict[str, Any]:
        """
        Get persisted commands for recovery (without loading them into state_store).
        Used during state recovery to check which commands might be active on the controller.
        """
        if not self._persistence:
            return {}
        persisted = await self._persistence.load()
        return {cmd.command_id: cmd for cmd in persisted.values() if cmd.transport_id}
    
    async def register_command(
        self,
        command_id: str,
        transport_id: str,
        state: int,
        target_id: Optional[str] = None
    ) -> ActiveTransport:
        """Register a new command with its transport.

        Args:
            command_id: Command ID
            transport_id: Symovo transport ID
            state: Initial transport state
            target_id: Target position ID

        Returns:
            ActiveTransport instance
        """
        self._assert_owner_thread()
        async with self._lock:
            # Increment generation if command_id already exists (reuse case)
            existing = self._command_registry.get(command_id)
            generation = (existing.generation + 1) if existing else 0
            
            transport = ActiveTransport(
                command_id=command_id,
                transport_id=transport_id,
                state=state,
                created_at=datetime.now(timezone.utc),
                target_id=target_id,
                generation=generation
            )
            self._command_registry[command_id] = transport
            self._current_command_id = command_id

        # Enqueue persistence operation (non-blocking, best-effort)
        if self._persistence:
            self._enqueue_persistence({
                "type": "upsert",
                "data": PersistedCommand(
                    command_id=command_id,
                    transport_id=transport_id,
                    target_id=target_id,
                    last_state=state,
                    last_result=None,
                    created_at=self._iso_now(),
                    updated_at=self._iso_now(),
                )
            })
        return transport

    async def get_current_command_id(self) -> Optional[str]:
        async with self._lock:
            return self._current_command_id

    async def get_active_commands_for_publishing(self) -> Dict[str, ActiveTransport]:
        """
        Return the set of commands that should drive status publishing.
        Current policy: only the most recently registered command.
        """
        async with self._lock:
            if self._current_command_id and self._current_command_id in self._command_registry:
                t = self._command_registry[self._current_command_id]
                return {self._current_command_id: t}
            # Fallback: pick the most recently created command if current is unset
            if self._command_registry:
                latest = max(self._command_registry.values(), key=lambda x: x.created_at)
                return {latest.command_id: latest}
            return {}
    
    async def get_active_transport(self, command_id: str) -> Optional[ActiveTransport]:
        """Get active transport for a command_id."""
        async with self._lock:
            return self._command_registry.get(command_id)
    
    async def get_active_transport_by_transport_id(self, transport_id: str) -> Optional[ActiveTransport]:
        """Get active transport by Symovo transport_id."""
        async with self._lock:
            for transport in self._command_registry.values():
                if transport.transport_id == transport_id:
                    return transport
            return None
    
    async def update_transport_state(self, command_id: str, state: int) -> Optional[ActiveTransport]:
        """Update transport state for a command_id."""
        self._assert_owner_thread()
        transport: Optional[ActiveTransport] = None
        async with self._lock:
            if command_id in self._command_registry:
                self._command_registry[command_id].state = state
                transport = self._command_registry[command_id]
        # Enqueue persistence operation (non-blocking, best-effort)
        if transport and self._persistence:
            self._enqueue_persistence({
                "type": "upsert",
                "data": PersistedCommand(
                    command_id=command_id,
                    transport_id=transport.transport_id,
                    target_id=transport.target_id,
                    last_state=state,
                    last_result=None,
                    created_at=None,
                    updated_at=self._iso_now(),
                )
            })
        return transport

    async def set_last_result(self, command_id: str, result: Dict[str, Any]) -> None:
        """Persist last terminal result for command_id."""
        self._assert_owner_thread()
        transport = await self.get_active_transport(command_id)
        if not self._persistence or not transport:
            return
        # Enqueue persistence operation (non-blocking, best-effort)
        self._enqueue_persistence({
            "type": "upsert",
            "data": PersistedCommand(
                command_id=command_id,
                transport_id=transport.transport_id,
                target_id=transport.target_id,
                last_state=transport.state,
                last_result=result,
                created_at=None,
                updated_at=self._iso_now(),
            )
        })
    
    async def clear_transport(self, command_id: str) -> bool:
        """Clear transport for a command_id."""
        self._assert_owner_thread()
        async with self._lock:
            if command_id in self._command_registry:
                del self._command_registry[command_id]
                self._sessions.pop(command_id, None)
                if self._current_command_id == command_id:
                    # Pick the next-most-recent command (if any) to keep status publishing stable.
                    if self._command_registry:
                        latest = max(self._command_registry.values(), key=lambda x: x.created_at)
                        self._current_command_id = latest.command_id
                    else:
                        self._current_command_id = None
                cleared = True
            else:
                cleared = False
        # Enqueue persistence operations (non-blocking, best-effort)
        if cleared and self._persistence:
            # delete() already removes both command and session from persistence
            self._enqueue_persistence({"type": "delete", "command_id": command_id})
        return cleared

    async def clear_all_commands(self) -> int:
        """
        Clear ALL tracked commands/sessions from in-memory store (and persistence if enabled).

        This is used to enforce a "single latest command" UX, so old command_ids never "compete" in status publishing.
        """
        async with self._lock:
            command_ids = list(self._command_registry.keys())
            self._command_registry.clear()
            self._sessions.clear()
            self._current_command_id = None
            # Drop last navigation status so UI doesn't keep showing an old goal_id
            self._last_navigation_status = None
            self._last_navigation_status_ts = 0.0

        # Enqueue persistence operations (non-blocking, best-effort)
        if self._persistence:
            for cid in command_ids:
                self._enqueue_persistence({"type": "delete", "command_id": cid})
                self._enqueue_persistence({"type": "delete_session", "command_id": cid})
        return len(command_ids)

    async def upsert_session(self, session: NavigationSession) -> None:
        self._assert_owner_thread()
        async with self._lock:
            self._sessions[session.command_id] = session
        # Enqueue persistence operation (non-blocking, best-effort)
        if self._persistence:
            self._enqueue_persistence({
                "type": "upsert_session",
                "data": PersistedSession(
                    command_id=session.command_id,
                    target_id=session.target_id,
                    start=session.start.model_dump(),
                    goal=session.goal.model_dump(),
                    total_dist_m=session.total_dist_m,
                    min_remaining_dist_m=session.min_remaining_dist_m,
                    progress_percent=session.progress_percent,
                    created_at=session.created_at.isoformat(),
                    updated_at=session.updated_at.isoformat(),
                )
            })

    async def get_session(self, command_id: str) -> Optional[NavigationSession]:
        async with self._lock:
            return self._sessions.get(command_id)

    async def clear_session(self, command_id: str) -> None:
        self._assert_owner_thread()
        async with self._lock:
            self._sessions.pop(command_id, None)
        # Enqueue persistence operation (non-blocking, best-effort)
        if self._persistence:
            self._enqueue_persistence({"type": "delete_session", "command_id": command_id})
    
    async def get_all_active_commands(self) -> Dict[str, ActiveTransport]:
        """Get all active commands."""
        async with self._lock:
            return self._command_registry.copy()
    
    async def set_last_navigation_status(self, status: NavigationStatus) -> None:
        """Store last navigation status for heartbeat."""
        async with self._lock:
            self._last_navigation_status = status
            self._last_navigation_status_ts = time.time()
    
    async def get_last_navigation_status(self) -> Optional[NavigationStatus]:
        """Get last navigation status."""
        async with self._lock:
            return self._last_navigation_status

    async def get_last_navigation_status_with_ts(self) -> tuple[Optional[NavigationStatus], float]:
        """Get last navigation status and the time it was last updated."""
        async with self._lock:
            return self._last_navigation_status, float(self._last_navigation_status_ts or 0.0)
    
    async def set_last_position_status(self, status: PositionStatus) -> None:
        """Store last position status."""
        async with self._lock:
            self._last_position_status = status
    
    async def get_last_position_status(self) -> Optional[PositionStatus]:
        """Get last position status."""
        async with self._lock:
            return self._last_position_status
    
    # ── Raw pose / status cache (background-fed by StatusPublisher) ──

    async def set_last_raw_pose(self, raw: Dict[str, Any]) -> None:
        """Store full raw pose dict from Symovo controller."""
        async with self._lock:
            self._last_raw_pose = raw
            self._last_raw_pose_ts = time.time()

    async def get_last_raw_pose(self) -> Optional[Dict[str, Any]]:
        """Get cached raw pose dict (or None if not yet available)."""
        async with self._lock:
            return self._last_raw_pose

    async def get_last_raw_pose_age_s(self) -> float:
        """Seconds since last raw pose update. Returns inf if never updated."""
        async with self._lock:
            if self._last_raw_pose_ts == 0.0:
                return float('inf')
            return time.time() - self._last_raw_pose_ts

    async def set_last_raw_status(self, raw: Dict[str, Any]) -> None:
        """Store full raw status dict from Symovo controller."""
        async with self._lock:
            self._last_raw_status = raw
            self._last_raw_status_ts = time.time()

    async def get_last_raw_status(self) -> Optional[Dict[str, Any]]:
        """Get cached raw status dict (or None if not yet available)."""
        async with self._lock:
            return self._last_raw_status

    async def get_last_raw_status_age_s(self) -> float:
        """Seconds since last raw status update. Returns inf if never updated."""
        async with self._lock:
            if self._last_raw_status_ts == 0.0:
                return float('inf')
            return time.time() - self._last_raw_status_ts

    async def clear_all(self) -> None:
        """Clear all stored state (for testing/restart)."""
        async with self._lock:
            self._command_registry.clear()
            self._sessions.clear()
            self._current_command_id = None
            self._last_navigation_status = None
            self._last_navigation_status_ts = 0.0
            self._last_position_status = None
            self._last_raw_pose = None
            self._last_raw_pose_ts = 0.0
            self._last_raw_status = None
            self._last_raw_status_ts = 0.0
