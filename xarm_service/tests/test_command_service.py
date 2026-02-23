"""Unit tests for CommandService (usecases/command_service.py).

Tests readiness gating, idempotency cache, and policy routing
without any real hardware.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from drivers.xarm_driver.usecases.command_service import CommandService
from drivers.xarm_driver.actor.commands import (
    Command,
    CommandResult,
    CommandType,
    ExecutionPolicy,
    ResultStatus,
)


def _make_gate(*, connected=True, faulted=False, busy=False, motion_enabled=True):
    """Create a mock ReadinessGate."""
    gate = MagicMock()
    gate.connected = connected
    gate.faulted = faulted
    gate.busy = busy
    gate.motion_enabled = motion_enabled
    return gate


def _make_svc(gate=None, enqueue_result=None):
    """Create CommandService with fake dependencies."""
    if gate is None:
        gate = _make_gate()
    if enqueue_result is None:
        enqueue_result = CommandResult(
            command_id="test", status=ResultStatus.SUCCEEDED,
        )

    async def fake_enqueue(cmd):
        return CommandResult(
            command_id=cmd.command_id,
            status=enqueue_result.status,
            error_message=enqueue_result.error_message,
        )

    return CommandService(
        readiness_getter=lambda: gate,
        actor_enqueue=fake_enqueue,
        idempotency_cache_size=16,
    )


# ── Readiness Gating ──────────────────────────────────────────────────────

class TestReadinessGating:
    @pytest.mark.asyncio
    async def test_rejects_when_not_connected(self):
        svc = _make_svc(gate=_make_gate(connected=False))
        cmd = Command(command_id="c1", type=CommandType.MOVE_JOINTS, params={})
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.REJECTED
        assert "Not connected" in result.error_message

    @pytest.mark.asyncio
    async def test_rejects_motion_when_faulted(self):
        svc = _make_svc(gate=_make_gate(faulted=True))
        cmd = Command(command_id="c2", type=CommandType.MOVE_JOINTS, params={})
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.REJECTED
        assert "faulted" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_allows_recover_when_faulted(self):
        svc = _make_svc(gate=_make_gate(faulted=True))
        cmd = Command(command_id="c3", type=CommandType.RECOVER_FAULTS, params={})
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_rejects_motion_when_motion_disabled(self):
        svc = _make_svc(gate=_make_gate(motion_enabled=False))
        cmd = Command(command_id="c4", type=CommandType.MOVE_JOINTS, params={})
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.REJECTED
        assert "Motion not enabled" in result.error_message

    @pytest.mark.asyncio
    async def test_allows_stop_when_motion_disabled(self):
        svc = _make_svc(gate=_make_gate(motion_enabled=False))
        cmd = Command(command_id="c5", type=CommandType.STOP, params={})
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_rejects_if_busy_with_reject_policy(self):
        svc = _make_svc(gate=_make_gate(busy=True))
        cmd = Command(
            command_id="c6", type=CommandType.MOVE_JOINTS, params={},
            policy=ExecutionPolicy.REJECT_IF_BUSY,
        )
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.REJECTED
        assert "busy" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_queues_when_busy_with_queue_policy(self):
        svc = _make_svc(gate=_make_gate(busy=True))
        cmd = Command(
            command_id="c7", type=CommandType.MOVE_JOINTS, params={},
            policy=ExecutionPolicy.QUEUE,
        )
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.SUCCEEDED


# ── Idempotency Cache ─────────────────────────────────────────────────────

class TestIdempotencyCache:
    @pytest.mark.asyncio
    async def test_cached_result_returned(self):
        svc = _make_svc()
        cmd = Command(command_id="idem-1", type=CommandType.MOVE_JOINTS, params={})
        r1 = await svc.enqueue(cmd)
        assert r1.status == ResultStatus.SUCCEEDED

        # Same command_id → cached
        r2 = await svc.enqueue(cmd)
        assert r2.status == ResultStatus.SUCCEEDED
        assert r2.command_id == r1.command_id

    @pytest.mark.asyncio
    async def test_cache_eviction(self):
        svc = _make_svc()
        # Fill cache beyond capacity (16)
        for i in range(20):
            cmd = Command(command_id=f"ev-{i}", type=CommandType.STOP, params={})
            await svc.enqueue(cmd)

        # Oldest entries should be evicted
        # ev-0 through ev-3 should be gone, ev-16+ should still be cached
        assert svc._cache_get("ev-0") is None
        assert svc._cache_get("ev-19") is not None


# ── All CommandTypes Accepted ─────────────────────────────────────────────

class TestCommandTypeCoverage:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd_type", [
        CommandType.GRIP_OPEN,
        CommandType.GRIP_CLOSE,
        CommandType.SMART_GRASP,
        CommandType.DEPTH_SCAN,
        CommandType.GRIPPER_STATUS,
        CommandType.ENABLE_MOTION,
        CommandType.DISABLE_MOTION,
    ])
    async def test_non_motion_commands_pass_readiness(self, cmd_type):
        svc = _make_svc()
        cmd = Command(command_id=f"t-{cmd_type}", type=cmd_type, params={})
        result = await svc.enqueue(cmd)
        assert result.status == ResultStatus.SUCCEEDED
