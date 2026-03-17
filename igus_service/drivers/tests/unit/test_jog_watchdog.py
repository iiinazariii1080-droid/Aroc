"""Tests for JogController TTL watchdog lifecycle.

Verifies that the watchdog correctly auto-stops jog after TTL expiry,
that keepalive refreshes the deadline, and that abort events force stop.

Uses real asyncio event loop and timing — no mocks for timing-critical paths.
"""

from __future__ import annotations

import asyncio

import pytest

from drivers.dryve_d1.motion.jog import JogConfig, JogController


class FakeOD:
    """Minimal OD accessor for JogController tests."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int, int]] = []

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        # Return OPERATION_ENABLED statusword when asked
        if index == 0x6041:
            return 0x0227  # operation_enabled + quick_stop + remote
        return 0

    async def read_i8(self, index: int, subindex: int = 0) -> int:
        return 3  # PV mode display

    async def read_i32(self, index: int, subindex: int = 0) -> int:
        return 0

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))


def _make_jog(
    *,
    ttl_s: float = 0.1,
    watch_interval_s: float = 0.02,
    abort_event: asyncio.Event | None = None,
) -> tuple[JogController, FakeOD]:
    od = FakeOD()
    cfg = JogConfig(
        ttl_s=ttl_s,
        watch_interval_s=watch_interval_s,
        require_operation_enabled=True,
        mode_settle_s=0.0,  # skip mode settle delay in tests
    )
    jog = JogController(od, config=cfg, abort_event=abort_event)
    return jog, od


@pytest.mark.asyncio
async def test_watchdog_stops_jog_after_ttl_expiry() -> None:
    """Jog must auto-stop when no keepalive arrives within TTL."""
    jog, _ = _make_jog(ttl_s=0.1, watch_interval_s=0.02)

    await jog.press(velocity=100)
    assert jog.state.active is True

    # Wait for TTL to expire (1.5x TTL for margin)
    await asyncio.sleep(0.15)

    assert jog.state.active is False, "Watchdog should have stopped jog after TTL expiry"
    await jog.close()


@pytest.mark.asyncio
async def test_keepalive_refreshes_deadline() -> None:
    """keepalive() must refresh the TTL deadline, preventing auto-stop."""
    jog, _ = _make_jog(ttl_s=0.1, watch_interval_s=0.02)

    await jog.press(velocity=100)
    assert jog.state.active is True

    # Send keepalive before TTL expires, 3 times
    for _ in range(3):
        await asyncio.sleep(0.05)  # half of TTL
        await jog.keepalive()
        assert jog.state.active is True, "Jog should still be active after keepalive"

    # Now stop sending keepalive — should auto-stop
    await asyncio.sleep(0.15)
    assert jog.state.active is False, "Jog should have stopped after keepalive ceased"
    await jog.close()


@pytest.mark.asyncio
async def test_abort_event_forces_stop() -> None:
    """Setting the abort event must force-stop the jog."""
    abort_event = asyncio.Event()
    jog, _ = _make_jog(ttl_s=5.0, watch_interval_s=0.02, abort_event=abort_event)

    await jog.press(velocity=100)
    assert jog.state.active is True

    # Trigger abort
    abort_event.set()
    await asyncio.sleep(0.1)  # give watchdog time to detect

    assert jog.state.active is False, "Abort event should have force-stopped jog"
    await jog.close()


@pytest.mark.asyncio
async def test_release_stops_jog_immediately() -> None:
    """release() must stop jog without waiting for watchdog."""
    jog, _ = _make_jog(ttl_s=5.0)

    await jog.press(velocity=100)
    assert jog.state.active is True

    await jog.release()
    assert jog.state.active is False, "release() should stop jog immediately"
    await jog.close()


# ---------------------------------------------------------------------------
# _mode_ready: skip ensure_mode on re-press, reset on close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_ready_skips_ensure_mode_on_repress() -> None:
    """After first press, _mode_ready=True → re-press skips ensure_mode (no mode write)."""
    jog, od = _make_jog(ttl_s=5.0)

    # First press: writes mode register (0x6060) via ensure_mode
    await jog.press(velocity=100)
    first_press_writes = len(od.writes)
    assert first_press_writes > 0, "First press should write mode/configure"
    assert jog._mode_ready is True

    # Release (keeps _mode_ready=True)
    await jog.release()
    assert jog._mode_ready is True, "_mode_ready must persist after release"

    # Re-press: should skip ensure_mode → fewer writes
    od.writes.clear()
    await jog.press(velocity=200)
    repress_writes = len(od.writes)
    # Only velocity (0x60FF) + latch (2× controlword) writes, no mode/configure
    assert repress_writes < first_press_writes, (
        f"Re-press should skip ensure_mode: {repress_writes} writes vs {first_press_writes} on first"
    )
    await jog.close()


@pytest.mark.asyncio
async def test_mode_ready_reset_on_close() -> None:
    """close() must reset _mode_ready so next session does full init."""
    jog, _ = _make_jog(ttl_s=5.0)

    await jog.press(velocity=100)
    assert jog._mode_ready is True

    await jog.close()
    assert jog._mode_ready is False, "close() must reset _mode_ready"


@pytest.mark.asyncio
async def test_mode_ready_persists_across_release_cycles() -> None:
    """Multiple press→release cycles should all benefit from _mode_ready."""
    jog, od = _make_jog(ttl_s=5.0)

    # First cycle: full init
    await jog.press(velocity=100)
    await jog.release()

    # Second cycle: fast re-press
    od.writes.clear()
    await jog.press(velocity=200)
    writes_second = len(od.writes)
    await jog.release()

    # Third cycle: still fast
    od.writes.clear()
    await jog.press(velocity=300)
    writes_third = len(od.writes)
    await jog.release()

    assert writes_second == writes_third, "All re-presses should have same (small) write count"
    await jog.close()


# ---------------------------------------------------------------------------
# abort_event cleared before jog_start prevents stale abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_abort_does_not_kill_new_jog() -> None:
    """If abort_event is set from a prior stop(), new press() should not be killed.

    The caller (jog_start) must clear abort_event before calling press().
    This test verifies the JogController behavior when abort is properly cleared.
    """
    abort_event = asyncio.Event()
    jog, _ = _make_jog(ttl_s=5.0, watch_interval_s=0.02, abort_event=abort_event)

    # Simulate stop() setting abort
    abort_event.set()

    # Caller clears abort before new jog (as jog_start does)
    abort_event.clear()

    await jog.press(velocity=100)
    assert jog.state.active is True

    # Wait for several watchdog ticks — jog should remain active
    await asyncio.sleep(0.1)
    assert jog.state.active is True, "Jog must stay active when abort_event was cleared"
    await jog.close()
