"""Centralized safety enforcement for robot_service.

Every command that causes physical motion MUST pass through the SafetyKernel.
This replaces scattered safety checks across decorators, robot_scripts, and
joystick_pipeline with a single, auditable enforcement point.

Design:
  - Fail-closed: if nav2adapter is unreachable, motion is blocked.
  - E-stop is persistent: survives until /safety/recover clears it.
  - Rate-limited re-checks avoid hammering nav2adapter during long ops.
  - time.monotonic() everywhere — immune to NTP jumps.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import aiohttp

from exceptions import SafetyLockoutError

_log = logging.getLogger("robot_service.safety")

_RECHECK_INTERVAL_S = 2.0
_SAFETY_FAIL_OPEN = os.environ.get("SAFETY_FAIL_OPEN", "false").lower() in ("1", "true", "yes")

# Module-level singleton
_kernel: Optional[SafetyKernel] = None


class SafetyKernel:
    """Single enforcement point for all motion safety checks.

    Usage::

        kernel = get_safety_kernel()
        await kernel.authorize_motion("arm")    # raises SafetyLockoutError if unsafe
    """

    def __init__(self, safety_state_url: str) -> None:
        self._safety_state_url = safety_state_url
        self._estop_active: bool = False
        self._last_check_ts: float = 0.0
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # E-stop state management
    # ------------------------------------------------------------------

    @property
    def estop_active(self) -> bool:
        return self._estop_active

    def set_estop(self, active: bool) -> None:
        prev = self._estop_active
        self._estop_active = active
        if prev != active:
            _log.warning("safety_kernel: estop %s → %s", prev, active)

    # ------------------------------------------------------------------
    # Core enforcement
    # ------------------------------------------------------------------

    async def authorize_motion(self, subsystem: str = "unknown") -> None:
        """Authorize a motion command. Raises SafetyLockoutError if unsafe.

        Args:
            subsystem: label for logging ("arm", "agv", "lift", "joystick").
        """
        # 1. Persistent E-stop check (no network call)
        if self._estop_active:
            raise SafetyLockoutError("E-stop active — call /safety/recover first")

        # 2. Rate-limited nav2adapter safety state check
        now = time.monotonic()
        if now - self._last_check_ts < _RECHECK_INTERVAL_S:
            return  # recently checked, still OK
        self._last_check_ts = now

        await self._query_nav2adapter_safety(subsystem)

    async def authorize_motion_force(self, subsystem: str = "unknown") -> None:
        """Authorize motion, bypassing rate limiter (always queries nav2adapter)."""
        if self._estop_active:
            raise SafetyLockoutError("E-stop active — call /safety/recover first")
        self._last_check_ts = time.monotonic()
        await self._query_nav2adapter_safety(subsystem)

    async def check_quick(self) -> None:
        """Fast local-only check (E-stop flag only, no network)."""
        if self._estop_active:
            raise SafetyLockoutError("E-stop active — call /safety/recover first")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _query_nav2adapter_safety(self, subsystem: str) -> None:
        """Query nav2adapter /safety/state and raise if locked."""
        try:
            session = self._get_session()
            async with session.get(
                self._safety_state_url,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status != 200:
                    msg = f"Cannot verify safety: nav2adapter HTTP {resp.status}"
                    _log.warning(msg)
                    if not _SAFETY_FAIL_OPEN:
                        raise SafetyLockoutError(msg)
                    return
                data = await resp.json()
        except SafetyLockoutError:
            raise
        except Exception as exc:
            msg = f"Cannot verify safety: {exc}"
            _log.warning(msg)
            if not _SAFETY_FAIL_OPEN:
                raise SafetyLockoutError(msg) from exc
            return

        if data.get("safety_lockout"):
            reason = data.get("reason", "safety lockout active")
            _log.warning("Safety lockout active (subsystem=%s): %s", subsystem, reason)
            raise SafetyLockoutError(f"Safety lockout: {reason}")

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None


def get_safety_kernel() -> SafetyKernel:
    """Return the module-level SafetyKernel singleton.

    Lazily created on first access using SAFETY_STATE_URL from config.
    """
    global _kernel
    if _kernel is None:
        from app.config import SAFETY_STATE_URL
        _kernel = SafetyKernel(SAFETY_STATE_URL)
    return _kernel
