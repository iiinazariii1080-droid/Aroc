"""Background status cache loop.

Periodically polls the Symovo controller for AGV status and feeds
the SafetyStateTracker for real-time safety monitoring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from exceptions import DeviceError, DeviceConnectionError
from services.safety_state_tracker import SafetyStateTracker
from services.state_store import StateStore
from services.symovo_service import SymovoAgvClient

_LOGGER = logging.getLogger(__name__)


class StatusPoller:
    """Periodically fetches full AGV status and caches it in state_store."""

    def __init__(
        self,
        *,
        symovo_client: SymovoAgvClient,
        state_store: StateStore,
        safety_tracker: SafetyStateTracker,
        running_flag: asyncio.Event,
    ) -> None:
        self._client = symovo_client
        self._store = state_store
        self.safety_tracker = safety_tracker
        self._running = running_flag

    async def run(self) -> None:
        while self._running.is_set():
            rate_hz = max(0.1, min(10.0, settings.status_cache_hz))
            interval = 1.0 / rate_hz
            consecutive_errors = 0
            max_backoff = 30.0

            _LOGGER.info("Status poller started: interval=%.2fs (%.1f Hz)", interval, rate_hz)

            try:
                while self._running.is_set():
                    try:
                        raw_status = await self._client.status_uncached()
                        if isinstance(raw_status, dict):
                            await self._store.set_last_raw_status(raw_status)
                            _LOGGER.debug("Cached raw status from controller")
                            consecutive_errors = 0
                            self.safety_tracker.evaluate(raw_status)
                        else:
                            _LOGGER.warning("status_uncached() returned non-dict: %s", type(raw_status))
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        consecutive_errors += 1
                        is_expected = isinstance(e, DeviceError) and getattr(e, 'http_status', None) in (404, None)
                        if is_expected:
                            _LOGGER.debug("Status endpoint temporarily unavailable (attempt %d): %s", consecutive_errors, str(e)[:120])
                        else:
                            backoff = min(interval * (2 ** min(consecutive_errors, 5)), max_backoff)
                            _LOGGER.warning("Status fetch error (attempt %d), backing off %.1fs: %s", consecutive_errors, backoff, e)
                            await asyncio.sleep(backoff)
                            continue

                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                _LOGGER.info("Status poller cancelled")
                return
            except Exception as e:
                _LOGGER.error("Fatal error in status poller, restarting in 5s: %s", e, exc_info=True)
                await asyncio.sleep(5.0)
