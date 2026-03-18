from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .config import BASE_DIR, settings
from .utils import atomic_write, iso_now

logger = logging.getLogger(__name__)


def _default_state() -> Dict[str, Any]:
    hub_config: Dict[str, Any] | None = None
    if settings.default_hub_base_url:
        hub_config = {
            "base_url": settings.default_hub_base_url.rstrip("/"),
        }

    robot_identity: Dict[str, Any] | None = None
    robot_credentials_meta: Dict[str, Any] | None = None
    if settings.default_robot_id:
        robot_identity = {
            "robot_id": settings.default_robot_id,
            "display_name": settings.default_robot_display_name or settings.default_robot_id,
        }
    if settings.default_robot_id and settings.default_robot_api_key:
        robot_credentials_meta = {
            "robot_id": settings.default_robot_id,
        }
    return {
        "hub_config": hub_config,
        "robot_identity": robot_identity,
        "robot_credentials_meta": robot_credentials_meta,
    }


class HubStateStore:
    """
    Minimal persistence layer for hub configuration and robot identity data.
    Stores a single JSON file with relaxed requirements (no DB needed yet).

    State is loaded once during startup via ``load()``.  All subsequent
    reads are from memory — no lazy I/O on the hot path.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        default_path = BASE_DIR / "state" / "hub_state.json"
        self._path = Path(path or os.getenv("HUB_STATE_PATH", default_path))
        self._lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._state: Dict[str, Any] = _default_state()
        self._loaded = False

    async def load(self) -> None:
        """Load state from disk.  Called once during lifespan startup."""
        async with self._lock:
            if self._loaded:
                return
            if self._path.exists():
                try:
                    raw = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
                    self._state = json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.error(
                        "Hub state file corrupt (%s), falling back to defaults: %s",
                        self._path, exc,
                    )
                    self._state = _default_state()
                except OSError as exc:
                    logger.error(
                        "Failed to read hub state file (%s), falling back to defaults: %s",
                        self._path, exc,
                    )
                    self._state = _default_state()
            else:
                self._state = _default_state()
            self._loaded = True

    async def _flush(self, snapshot: str) -> None:
        """Write *snapshot* (a pre-serialized JSON string) to disk.

        Called **outside** ``self._lock`` so that slow / stuck I/O cannot
        block readers (get_hub_config, etc.).  The snapshot is captured
        under the lock before this method is called.

        ``_flush_lock`` serializes concurrent writes so that the last
        snapshot always wins (no stale-overwrite race).
        """

        def _write_sync() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists():
                bak = self._path.with_suffix(".bak")
                try:
                    bak.write_bytes(self._path.read_bytes())
                except OSError:
                    logger.warning("Failed to create backup %s", bak)
            atomic_write(self._path, snapshot)

        async with self._flush_lock:
            await asyncio.to_thread(_write_sync)

    def _snapshot_locked(self) -> str:
        """Serialize current state while holding the lock (pure CPU, no I/O)."""
        return json.dumps(self._state, indent=2, ensure_ascii=False, default=str)

    async def get_hub_config(self) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._state.get("hub_config")

    async def set_hub_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            **data,
            "updated_at": iso_now(),
        }
        async with self._lock:
            self._state["hub_config"] = record
            snapshot = self._snapshot_locked()
        await self._flush(snapshot)
        return record

    async def get_robot_identity(self) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._state.get("robot_identity")

    async def set_robot_identity(self, data: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            **data,
            "updated_at": iso_now(),
        }
        async with self._lock:
            self._state["robot_identity"] = record
            snapshot = self._snapshot_locked()
        await self._flush(snapshot)
        return record

    async def get_robot_credentials_meta(self) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._state.get("robot_credentials_meta")

    async def set_robot_credentials_meta(self, data: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            **data,
            "updated_at": iso_now(),
        }
        async with self._lock:
            self._state["robot_credentials_meta"] = record
            snapshot = self._snapshot_locked()
        await self._flush(snapshot)
        return record

    async def clear_robot_credentials_meta(self) -> None:
        """Remove robot credentials meta (sets to None and flushes)."""
        async with self._lock:
            self._state["robot_credentials_meta"] = None
            snapshot = self._snapshot_locked()
        await self._flush(snapshot)


hub_state_store = HubStateStore()

