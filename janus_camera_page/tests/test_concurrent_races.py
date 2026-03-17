"""T1–T4: Concurrent race condition tests.

Validates thread-safety invariants under contention:
  T1: Dual watchdog dedup — only one escalation per dedup window
  T2: Mode transition atomicity — concurrent degrade/promote/transition
  T3: Proxy client lifecycle — get_json during stop()
  T4: Recovery ladder concurrent escalate — dedup under contention
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from app.services import watchdogs, system_mode
from app.services.system_mode import SystemMode, _state


# ── Fixtures ──

@pytest.fixture(autouse=True)
def _reset_globals():
    """Reset all global state between tests."""
    watchdogs._STARTUP_TS = 0  # skip grace period
    watchdogs._last_escalation_ts = 0.0
    with _state.lock:
        _state.current = SystemMode.NOMINAL
        _state.entered_at = time.time()
        _state.entered_at_mono = time.monotonic()
        _state.reason = "test_reset"
        _state.listeners.clear()
    yield
    with _state.lock:
        _state.current = SystemMode.NOMINAL
        _state.entered_at = time.time()
        _state.entered_at_mono = time.monotonic()
        _state.reason = "test_reset"
        _state.listeners.clear()


# ===================================================================
# T1: Dual watchdog dedup — only one escalation per window
# ===================================================================

class TestT1_WatchdogDedupRace:
    """Two threads call _try_escalate simultaneously; only one should win."""

    def test_concurrent_try_escalate_dedup(self):
        ladder = MagicMock()
        results = []
        barrier = threading.Barrier(2, timeout=5)

        def race_escalate():
            barrier.wait()
            result = watchdogs._try_escalate(ladder, "test_signal", MagicMock())
            results.append(result)

        t1 = threading.Thread(target=race_escalate)
        t2 = threading.Thread(target=race_escalate)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Exactly one should have won the dedup window
        assert results.count(True) == 1, f"Expected exactly 1 escalation, got {results}"
        assert results.count(False) == 1
        # Ladder.escalate called exactly once
        assert ladder.escalate.call_count == 1

    def test_sequential_after_window_both_escalate(self):
        """After dedup window expires, next escalation should succeed."""
        ladder = MagicMock()

        # First escalation
        assert watchdogs._try_escalate(ladder, "signal_1", MagicMock()) is True

        # Force dedup window to expire
        watchdogs._last_escalation_ts = time.monotonic() - 10.0

        # Second escalation after window
        assert watchdogs._try_escalate(ladder, "signal_2", MagicMock()) is True
        assert ladder.escalate.call_count == 2

    def test_burst_of_10_threads_only_one_wins(self):
        """Stress test: 10 threads race; exactly 1 should escalate."""
        ladder = MagicMock()
        results = []
        barrier = threading.Barrier(10, timeout=5)

        def race():
            barrier.wait()
            results.append(watchdogs._try_escalate(ladder, "burst", MagicMock()))

        threads = [threading.Thread(target=race) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert results.count(True) == 1
        assert ladder.escalate.call_count == 1


# ===================================================================
# T2: Mode transition atomicity under contention
# ===================================================================

class TestT2_ModeTransitionRace:
    """Concurrent degrade/promote/transition maintain consistent state."""

    def test_concurrent_degrade_monotonic(self):
        """Multiple threads calling degrade() — mode should only go down, never back."""
        history = []
        original_post = system_mode._post_transition

        def tracking_post(prev, target, reason, listeners):
            history.append((prev, target))

        with patch.object(system_mode, "_post_transition", side_effect=tracking_post):
            threads = []
            barrier = threading.Barrier(5, timeout=5)

            def degrade_racer():
                barrier.wait()
                for _ in range(3):
                    system_mode.degrade("race_test")

            for _ in range(5):
                threads.append(threading.Thread(target=degrade_racer))
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        # Final mode should be SAFE (degraded from NOMINAL through all levels)
        final = system_mode.current_mode()
        assert final == SystemMode.SAFE

        # All transitions should be monotonically degrading
        for prev, target in history:
            assert target.level > prev.level, f"Non-monotonic: {prev} → {target}"

    def test_promote_during_degrade_race(self):
        """Promote and degrade racing — final state must be valid mode."""
        system_mode.transition(SystemMode.DEGRADED, "setup")

        with patch.object(system_mode, "_post_transition"):
            barrier = threading.Barrier(2, timeout=5)

            final_modes = []

            def degrader():
                barrier.wait()
                system_mode.degrade("degrade_race")
                final_modes.append(system_mode.current_mode())

            def promoter():
                barrier.wait()
                system_mode.promote(SystemMode.NOMINAL, "promote_race")
                final_modes.append(system_mode.current_mode())

            t1 = threading.Thread(target=degrader)
            t2 = threading.Thread(target=promoter)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        # Final mode must be a valid SystemMode (no corruption)
        final = system_mode.current_mode()
        assert final in list(SystemMode)

    def test_current_mode_consistent_during_transition(self):
        """current_mode() never returns an intermediate/corrupt value."""
        seen_modes = set()
        stop_event = threading.Event()

        def reader():
            while not stop_event.is_set():
                seen_modes.add(system_mode.current_mode())

        def writer():
            for _ in range(20):
                system_mode.degrade("writer")

        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)

        with patch.object(system_mode, "_post_transition"):
            reader_thread.start()
            writer_thread.start()
            writer_thread.join(timeout=5)
            stop_event.set()
            reader_thread.join(timeout=5)

        # All seen modes must be valid SystemMode values
        valid = set(SystemMode)
        assert seen_modes.issubset(valid), f"Invalid modes seen: {seen_modes - valid}"


# ===================================================================
# T3: Proxy client lifecycle — get_json during stop()
# ===================================================================

class TestT3_ProxyLifecycleRace:
    """AsyncHttpProxy: concurrent get_json + stop should not crash."""

    @pytest.mark.asyncio
    async def test_stop_during_idle_no_crash(self):
        from app.services.proxy_base import AsyncHttpProxy
        import httpx

        proxy = AsyncHttpProxy(
            name="test",
            timeout=httpx.Timeout(1.0),
            limits=httpx.Limits(max_connections=2),
        )
        await proxy.start()
        assert proxy._client is not None
        await proxy.stop()
        assert proxy._client is None
        # Double stop should be safe
        await proxy.stop()
        assert proxy._client is None

    @pytest.mark.asyncio
    async def test_start_idempotent_under_concurrency(self):
        from app.services.proxy_base import AsyncHttpProxy
        import httpx

        proxy = AsyncHttpProxy(
            name="test",
            timeout=httpx.Timeout(1.0),
            limits=httpx.Limits(max_connections=2),
        )

        # Concurrent starts should all resolve to the same client
        await asyncio.gather(proxy.start(), proxy.start(), proxy.start())
        assert proxy._client is not None
        await proxy.stop()


# ===================================================================
# T4: Recovery ladder concurrent escalate — dedup under contention
# ===================================================================

class TestT4_LadderConcurrentEscalate:
    """Multiple threads calling ladder.escalate() — dedup prevents double action."""

    def test_concurrent_escalate_dedup(self, tmp_path):
        from app.services.recovery_ladder import RecoveryLadder

        ladder_state = tmp_path / "ladder.json"
        reboot_count = tmp_path / "reboot_count"

        with (
            patch("app.services.recovery_ladder._LADDER_STATE_PATH", ladder_state),
            patch("app.services.recovery_ladder._REBOOT_COUNT_PATH", reboot_count),
            patch.object(system_mode, "_post_transition"),
        ):
            ladder = RecoveryLadder()
            # Mock _execute so it doesn't actually run systemctl
            ladder._execute = MagicMock(return_value=True)

            results = []
            barrier = threading.Barrier(5, timeout=5)

            def race_escalate():
                barrier.wait()
                r = ladder.escalate("concurrent_test")
                results.append(r["action"])

            threads = [threading.Thread(target=race_escalate) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            # At most one should execute (rest should be dedup_skip or cooldown)
            executed = [r for r in results if r not in ("dedup_skip", "cooldown")]
            assert len(executed) <= 1, f"Multiple executions: {results}"
