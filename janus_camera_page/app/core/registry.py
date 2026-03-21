"""Test-state reset utilities for janus_camera_page.

Centralizes the cleanup of module-level singletons that was previously
scattered across ``conftest._reset_global_state`` (~20 lines).

Production lifecycle is managed by ``events.py:_lifespan`` — this module
exists solely to support test isolation.

Each service module exports its own ``_reset_for_tests()`` function so
that ServiceRegistry does not need to know about private variable names.

Usage in tests (conftest.py):
    ServiceRegistry.reset()
    yield
    ServiceRegistry.teardown()
"""
from __future__ import annotations


class ServiceRegistry:
    """Centralized test-state reset for all module-level singletons."""

    @staticmethod
    def reset() -> None:
        """Reset all module-level singletons for test isolation.

        Delegates to each module's ``_reset_for_tests()`` so internal
        variable names stay private and refactor-safe.
        """
        # Settings cache
        from app.core.settings import get_settings
        get_settings.cache_clear()

        # Janus HTTP client + monitor session
        from app.services.janus import _reset_for_tests as _janus_reset
        _janus_reset()

        # System mode state + lazy metrics
        from app.services.system_mode import _reset_for_tests as _sm_reset
        _sm_reset()

        # Recovery ladder singleton
        from app.services.recovery_ladder import _reset_for_tests as _rl_reset
        _rl_reset()

        # Watchdog module-level state
        from app.services.watchdogs import _reset_for_tests as _wd_reset
        _wd_reset()

        # FDIR event ring buffer
        from app.services.fdir_events import _reset_for_tests as _fe_reset
        _fe_reset()

        # NAT config cache
        from app.services.nat_config import _reset_for_tests as _nc_reset
        _nc_reset()

    @staticmethod
    def teardown() -> None:
        """Post-test cleanup: clear settings cache and cancel watchdog tasks."""
        from app.core.settings import get_settings
        get_settings.cache_clear()

        from app.services.watchdogs import _reset_for_tests as _wd_reset
        _wd_reset()
