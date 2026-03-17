"""T1: Concurrent FDIR integration tests.

Validates that the recovery ladder, system mode, and thermal monitor
can operate concurrently without deadlock or state corruption.

These tests use real module state (not mocks) with filesystem paths
redirected to tmp_path. The risk is in the interaction between
components, not in individual logic — so we test them together.

Risk addressed: R01 (Critical — concurrent mode transitions)
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import make_test_settings

# Timeout for deadlock detection — if any concurrent test takes longer
# than this, it's a deadlock (not a slow test).
DEADLOCK_TIMEOUT_SEC = 5


@pytest.fixture
def _concurrent_env(tmp_path, monkeypatch):
    """Set up environment for concurrent FDIR tests.

    Uses real system_mode and recovery_ladder with paths in tmp_path.
    Patches subprocess calls (run_cmd) so no real systemctl runs.
    """
    settings = make_test_settings(
        tmp_path,
        camera_type="depth_camera",  # include USB_RESET level → 5 levels total
        fdir_dedup_sec=0.0,
        watchdog_grace_sec=0,
        mode_listener_timeout_sec=2.0,
        watchdog_reboot_enabled=True,
        max_fdir_reboots=2,
    )

    # Ensure persistence dirs exist
    settings.fdir_persist_dir.mkdir(parents=True, exist_ok=True)
    settings.fps_profile_path.parent.mkdir(parents=True, exist_ok=True)

    with patch("app.core.settings.get_settings", return_value=settings), \
         patch("app.services.recovery_ladder.get_settings", return_value=settings), \
         patch("app.services.system_mode.get_settings", return_value=settings), \
         patch("app.services.thermal.get_settings", return_value=settings), \
         patch("app.services.recovery_ladder.run_cmd", return_value=None), \
         patch("app.services.recovery_ladder.atomic_write_text"):

        # Reset singletons with patched settings
        from app.services import system_mode
        from app.services.system_mode import _reset_for_tests as sm_reset
        from app.services.recovery_ladder import _reset_for_tests as rl_reset
        from app.services.fdir_events import _reset_for_tests as fe_reset
        import app.services.fdir_events as _fe_mod
        sm_reset()
        rl_reset()
        fe_reset()

        yield settings

        # Cleanup — fully reset ring buffer so its maxlen doesn't leak
        # into subsequent tests that expect the default maxlen (500).
        sm_reset()
        rl_reset()
        fe_reset()
        _fe_mod._ring = None


class TestConcurrentFDIR:
    """Concurrent FDIR integration tests — deadlock and invariant validation."""

    @pytest.mark.asyncio
    async def test_concurrent_thermal_and_watchdog_escalation(self, _concurrent_env):
        """Thermal degrade() and watchdog escalate() run simultaneously.

        Validates: no deadlock within DEADLOCK_TIMEOUT_SEC, mode reaches
        at least DEGRADED, no unhandled exception.
        """
        from app.services import system_mode
        from app.services.recovery_ladder import RecoveryLadder
        from app.services.fdir_events import Domain

        ladder = RecoveryLadder()
        errors = []

        def thermal_worker():
            """Simulate thermal thread calling degrade()."""
            try:
                for _ in range(10):
                    system_mode.degrade("thermal_test")
            except Exception as exc:
                errors.append(("thermal", exc))

        async def watchdog_worker():
            """Simulate watchdog calling escalate()."""
            try:
                for _ in range(10):
                    await ladder.escalate("stale_stream", Domain.PIPELINE)
            except Exception as exc:
                errors.append(("watchdog", exc))

        # Run thermal in a thread, watchdog on event loop
        thermal_thread = threading.Thread(target=thermal_worker, daemon=True)
        thermal_thread.start()

        await asyncio.wait_for(watchdog_worker(), timeout=DEADLOCK_TIMEOUT_SEC)
        thermal_thread.join(timeout=DEADLOCK_TIMEOUT_SEC)

        assert not thermal_thread.is_alive(), "Thermal thread deadlocked"
        assert not errors, f"Exceptions during concurrent execution: {errors}"
        assert system_mode.current_mode().level >= system_mode.SystemMode.DEGRADED.level

    def test_concurrent_ladder_escalate_and_reset(self, _concurrent_env):
        """5 threads escalate, 5 threads reset — ladder level stays in valid range.

        Validates: ladder level is always 0..max_levels, no exception.
        """
        from app.services.recovery_ladder import RecoveryLadder
        from app.services.fdir_events import Domain

        ladder = RecoveryLadder()
        max_levels = len(ladder._levels)
        errors = []

        def escalate_worker():
            try:
                loop = asyncio.new_event_loop()
                for _ in range(5):
                    loop.run_until_complete(ladder.escalate("test_signal", Domain.PIPELINE))
                loop.close()
            except Exception as exc:
                errors.append(("escalate", exc))

        def reset_worker():
            try:
                for _ in range(5):
                    ladder.reset()
            except Exception as exc:
                errors.append(("reset", exc))

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for _ in range(5):
                futures.append(pool.submit(escalate_worker))
                futures.append(pool.submit(reset_worker))

            for f in as_completed(futures, timeout=DEADLOCK_TIMEOUT_SEC):
                f.result()  # raises if worker raised

        assert not errors, f"Exceptions: {errors}"
        level = ladder.status()["current_level"]
        assert 0 <= level <= max_levels, f"Ladder level {level} out of range [0, {max_levels}]"

    def test_concurrent_mode_transitions_never_corrupt_state(self, _concurrent_env):
        """20 threads call transition/degrade/promote with random targets.

        Validates: current_mode() is always a valid SystemMode member.
        """
        from app.services import system_mode
        from app.services.system_mode import SystemMode

        errors = []
        all_modes = list(SystemMode)

        def worker(idx):
            try:
                import random
                for _ in range(10):
                    op = random.choice(["transition", "degrade", "promote"])
                    target = random.choice(all_modes)
                    if op == "transition":
                        system_mode.transition(target, f"test_{idx}")
                    elif op == "degrade":
                        system_mode.degrade(f"test_{idx}")
                    else:
                        system_mode.promote(target, f"test_{idx}")
            except Exception as exc:
                errors.append((idx, exc))

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(worker, i) for i in range(20)]
            for f in as_completed(futures, timeout=DEADLOCK_TIMEOUT_SEC):
                f.result()

        assert not errors, f"Exceptions: {errors}"
        mode = system_mode.current_mode()
        assert mode in SystemMode, f"Invalid mode: {mode}"

    @pytest.mark.asyncio
    async def test_ladder_level_never_exceeds_max(self, _concurrent_env):
        """50 concurrent escalate() calls — level never exceeds max.

        Validates invariant: recovery ladder level <= len(levels).
        """
        from app.services.recovery_ladder import RecoveryLadder
        from app.services.fdir_events import Domain

        ladder = RecoveryLadder()
        max_levels = len(ladder._levels)
        errors = []

        async def escalate_many():
            for _ in range(50):
                try:
                    await ladder.escalate("stress_test", Domain.PIPELINE)
                except Exception as exc:
                    errors.append(exc)

        await asyncio.wait_for(escalate_many(), timeout=DEADLOCK_TIMEOUT_SEC)

        assert not errors, f"Exceptions: {errors}"
        level = ladder.status()["current_level"]
        assert level <= max_levels, (
            f"Ladder level {level} exceeds max {max_levels} — invariant violated"
        )

    def test_fps_profile_written_before_listeners_fire(self, _concurrent_env, tmp_path):
        """Mode listener observes fps_profile already written for the new mode.

        Validates invariant: fps_profile written BEFORE system mode listeners fire.
        """
        from app.services import system_mode
        from app.services.system_mode import SystemMode

        settings = _concurrent_env
        fps_path = settings.fps_profile_path
        # Ensure file exists with initial value
        fps_path.parent.mkdir(parents=True, exist_ok=True)
        fps_path.write_text("normal\n")

        observed_profiles = []

        def listener(prev, curr, reason):
            """Listener reads fps_profile — must see the new mode's value."""
            try:
                content = fps_path.read_text().strip()
                observed_profiles.append((curr.value, content))
            except Exception as exc:
                observed_profiles.append((curr.value, f"ERROR: {exc}"))

        system_mode.on_transition(listener)

        # Patch set_fps_profile to actually write to our tmp_path
        from app.services import thermal
        original_set = thermal.set_fps_profile

        def real_set_fps(profile):
            fps_path.write_text(profile + "\n")

        with patch("app.services.thermal.set_fps_profile", side_effect=real_set_fps):
            system_mode.transition(SystemMode.DEGRADED, "test_fps_ordering")

        assert len(observed_profiles) >= 1, "Listener was never called"
        mode_value, profile_content = observed_profiles[0]
        assert mode_value == "degraded"
        assert profile_content == "low", (
            f"fps_profile was '{profile_content}' when listener fired for DEGRADED mode — "
            f"expected 'low'. This means fps_profile was NOT written before listeners."
        )

    @pytest.mark.asyncio
    async def test_no_deadlock_under_escalation_storm(self, _concurrent_env):
        """3 concurrent tasks: thermal degrade + watchdog escalate + promote.

        All must complete within DEADLOCK_TIMEOUT_SEC — deadlock = timeout = fail.
        """
        from app.services import system_mode
        from app.services.system_mode import SystemMode
        from app.services.recovery_ladder import RecoveryLadder
        from app.services.fdir_events import Domain

        ladder = RecoveryLadder()
        errors = []

        def thermal_degrade():
            try:
                for _ in range(5):
                    system_mode.degrade("thermal_storm")
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(("thermal", exc))

        def promote_back():
            try:
                for _ in range(5):
                    system_mode.promote(SystemMode.NOMINAL, "recovery_storm")
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(("promote", exc))

        async def watchdog_escalate():
            try:
                for _ in range(5):
                    await ladder.escalate("storm_signal", Domain.PIPELINE)
            except Exception as exc:
                errors.append(("watchdog", exc))

        # Run all three concurrently
        t1 = threading.Thread(target=thermal_degrade, daemon=True)
        t2 = threading.Thread(target=promote_back, daemon=True)
        t1.start()
        t2.start()

        await asyncio.wait_for(watchdog_escalate(), timeout=DEADLOCK_TIMEOUT_SEC)

        t1.join(timeout=DEADLOCK_TIMEOUT_SEC)
        t2.join(timeout=DEADLOCK_TIMEOUT_SEC)

        assert not t1.is_alive(), "Thermal thread deadlocked"
        assert not t2.is_alive(), "Promote thread deadlocked"
        assert not errors, f"Exceptions during escalation storm: {errors}"
