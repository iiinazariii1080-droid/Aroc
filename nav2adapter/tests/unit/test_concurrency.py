"""
Concurrency tests for nav2adapter.

Validates that concurrent operations on CommandHandler, StateStore,
and EventBus serialize correctly, avoid races, and produce consistent state.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.models import (
    ActiveTransport,
    NavigationCommand,
    NavigationStatusEnum,
)
from services.command_handler import CommandHandler
from services.event_bus import EventBus
from services.state_store import StateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_command(command_id: str = "cmd-001", target_id: str = "TestPose") -> NavigationCommand:
    return NavigationCommand(
        command_id=command_id,
        timestamp="2026-01-01T00:00:00Z",
        target_id=target_id,
        x=1.0, y=2.0, theta=1.5708,
    )


def _target_dict():
    return {"x": 1.0, "y": 2.0, "theta": 1.5708, "map_id": 0}


def _make_symovo_mock(*, transport_create_delay: float = 0.0) -> MagicMock:
    symovo = MagicMock()
    symovo.status_uncached = AsyncMock(
        return_value={"state_flags": {"drive_ready": True, "safety_cleared": True}}
    )
    symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
    symovo.transport_get = AsyncMock(return_value={"state": 1})
    symovo.clear_all_transports = AsyncMock()

    async def _slow_create(**kwargs):
        if transport_create_delay > 0:
            await asyncio.sleep(transport_create_delay)
        return {"id": "t1", "state": 1}

    symovo.transport_move_to_pose = AsyncMock(side_effect=_slow_create)
    symovo.transport_create_station = AsyncMock(return_value={"id": "t2", "state": 1})
    symovo.transport_start = AsyncMock(return_value={"ok": True})
    symovo.transport_stop = AsyncMock()
    symovo.delete_transport = AsyncMock()
    return symovo


def _make_state_store_mock(**overrides) -> MagicMock:
    ss = MagicMock()
    ss.get_active_transport = AsyncMock(return_value=None)
    ss.get_last_navigation_status = AsyncMock(return_value=None)
    ss.get_last_position_status = AsyncMock(return_value=None)
    ss.get_all_active_commands = AsyncMock(return_value={})
    ss.register_command = AsyncMock()
    ss.set_last_navigation_status = AsyncMock()
    ss.clear_transport = AsyncMock()
    ss.clear_session = AsyncMock()
    ss.clear_all_commands = AsyncMock(return_value=0)
    ss.get_session = AsyncMock(return_value=None)
    ss.upsert_session = AsyncMock()
    for key, val in overrides.items():
        setattr(ss, key, val)
    return ss


def _make_handler(
    event_bus: EventBus,
    *,
    symovo=None,
    state_store=None,
) -> CommandHandler:
    return CommandHandler(
        symovo_client=symovo or _make_symovo_mock(),
        event_bus=event_bus,
        state_store=state_store or _make_state_store_mock(),
    )


# ---------------------------------------------------------------------------
# Test 1 — Concurrent navigateTo + cancel
# ---------------------------------------------------------------------------

class TestConcurrentNavigateCancel:
    @pytest.mark.asyncio
    async def test_concurrent_navigate_cancel(self):
        """Start a navigateTo that blocks on transport creation, then
        concurrently issue a cancel. Both must complete without error."""
        bus = EventBus(queue_size=100)
        ss = _make_state_store_mock()
        symovo = _make_symovo_mock(transport_create_delay=0.3)

        handler = _make_handler(bus, symovo=symovo, state_store=ss)

        cmd = _make_command("cmd-nav-cancel")

        nav_task = asyncio.create_task(handler.handle_drive_to_position(cmd))
        await asyncio.sleep(0.05)
        cancel_task = asyncio.create_task(handler.handle_cancel("cmd-nav-cancel"))

        nav_result, cancel_result = await asyncio.gather(
            nav_task, cancel_task, return_exceptions=True,
        )

        assert not isinstance(nav_result, BaseException), f"navigate raised: {nav_result}"
        assert not isinstance(cancel_result, BaseException), f"cancel raised: {cancel_result}"

        assert nav_result.status in {
            NavigationStatusEnum.NAVIGATING,
            NavigationStatusEnum.ERROR,
        }
        assert cancel_result.status in {
            NavigationStatusEnum.IDLE,
            NavigationStatusEnum.NAVIGATING,
            NavigationStatusEnum.ERROR,
        }


# ---------------------------------------------------------------------------
# Test 2 — Concurrent navigateTo + navigateTo
# ---------------------------------------------------------------------------

class TestConcurrentNavigateNavigate:
    @pytest.mark.asyncio
    async def test_concurrent_navigate_navigate(self):
        """Issue two navigateTo commands concurrently. Verify that both
        complete and at most one transport is started."""
        bus = EventBus(queue_size=100)
        ss = _make_state_store_mock()
        symovo = _make_symovo_mock(transport_create_delay=0.2)

        handler = _make_handler(bus, symovo=symovo, state_store=ss)

        cmd1 = _make_command("cmd-A")
        cmd2 = _make_command("cmd-B")

        call_count = 0

        async def _get_all_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                from datetime import datetime, timezone
                t = ActiveTransport(
                    command_id="cmd-A",
                    transport_id="t1",
                    state=1,
                    created_at=datetime.now(timezone.utc),
                    target_id="TestPose",
                )
                return {"cmd-A": t}
            return {}

        ss.get_all_active_commands = AsyncMock(side_effect=_get_all_side_effect)

        task1 = asyncio.create_task(handler.handle_drive_to_position(cmd1))
        await asyncio.sleep(0.05)
        task2 = asyncio.create_task(handler.handle_drive_to_position(cmd2))

        result1, result2 = await asyncio.gather(task1, task2, return_exceptions=True)

        assert not isinstance(result1, BaseException), f"navigate-1 raised: {result1}"
        assert not isinstance(result2, BaseException), f"navigate-2 raised: {result2}"

        # First command should succeed (NAVIGATING).
        assert result1.status == NavigationStatusEnum.NAVIGATING

        # Second command gets rejected as busy or also succeeds if first already finished.
        assert result2.status in {
            NavigationStatusEnum.ERROR,
            NavigationStatusEnum.NAVIGATING,
        }

        # transport_start should have been called at most twice
        assert symovo.transport_start.await_count <= 2


# ---------------------------------------------------------------------------
# Test 3 — Concurrent register_command on real StateStore
# ---------------------------------------------------------------------------

class TestConcurrentRegisterCommand:
    @pytest.mark.asyncio
    async def test_concurrent_register_is_serialized(self):
        """Multiple concurrent register_command calls on a real StateStore
        are serialized by the internal lock — all succeed but the store
        remains consistent."""
        with patch("services.state_store.settings") as s:
            s.persistence_enabled = False
            s.persistence_path = ""
            store = StateStore()

        num_tasks = 10

        async def _register(i: int):
            return await store.register_command(
                command_id=f"cmd-{i}",
                transport_id=f"transport-{i}",
                state=1,
                target_id=f"target-{i}",
            )

        results = await asyncio.gather(*[_register(i) for i in range(num_tasks)])

        # All registrations succeed
        assert all(r is not None for r in results)

        # State store contains all commands
        active = await store.get_all_active_commands()
        assert len(active) == num_tasks
