from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import (
    BASE_DIR,
    DEFAULT_HUB_BASE_URL,
    DEFAULT_ROBOT_API_KEY,
    DEFAULT_ROBOT_DISPLAY_NAME,
    DEFAULT_ROBOT_ID,
)


def _now_iso() -> str:
    """
    Returns a compact ISO8601 UTC timestamp (e.g. 2025-11-28T10:15:30Z).
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_state() -> Dict[str, Any]:
    hub_config: Dict[str, Any] | None = None
    if DEFAULT_HUB_BASE_URL:
        hub_config = {
            "base_url": DEFAULT_HUB_BASE_URL.rstrip("/"),
            "notes": "Pre-configured test hub endpoint",
        }

    robot_identity: Dict[str, Any] | None = None
    robot_credentials_meta: Dict[str, Any] | None = None
    if DEFAULT_ROBOT_ID:
        robot_identity = {
            "robot_id": DEFAULT_ROBOT_ID,
            "display_name": DEFAULT_ROBOT_DISPLAY_NAME or DEFAULT_ROBOT_ID,
            "notes": "Pre-configured demo robot identity",
        }
    if DEFAULT_ROBOT_ID and DEFAULT_ROBOT_API_KEY:
        robot_credentials_meta = {
            "robot_id": DEFAULT_ROBOT_ID,
            "notes": "Pre-configured demo robot credentials metadata",
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
    """

    def __init__(self, path: str | Path | None = None) -> None:
        default_path = BASE_DIR / "state" / "hub_state.json"
        self._path = Path(path or os.getenv("HUB_STATE_PATH", default_path))
        self._lock = asyncio.Lock()
        self._state: Dict[str, Any] = _default_state()
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            if self._path.exists():
                try:
                    raw = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
                    self._state = json.loads(raw)
                except (json.JSONDecodeError, OSError):
                    self._state = _default_state()
            else:
                self._state = _default_state()
            self._loaded = True

    async def _flush_locked(self) -> None:
        def _write_sync() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._state, indent=2, ensure_ascii=False)
            tmp_path = self._path.with_suffix(".tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self._path)

        await asyncio.to_thread(_write_sync)

    async def get_hub_config(self) -> Optional[Dict[str, Any]]:
        await self._ensure_loaded()
        return self._state.get("hub_config")

    async def set_hub_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_loaded()
        record = {
            **data,
            "updated_at": _now_iso(),
        }
        async with self._lock:
            self._state["hub_config"] = record
            await self._flush_locked()
        return record

    async def get_robot_identity(self) -> Optional[Dict[str, Any]]:
        await self._ensure_loaded()
        return self._state.get("robot_identity")

    async def set_robot_identity(self, data: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_loaded()
        record = {
            **data,
            "updated_at": _now_iso(),
        }
        async with self._lock:
            self._state["robot_identity"] = record
            await self._flush_locked()
        return record

    async def get_robot_credentials_meta(self) -> Optional[Dict[str, Any]]:
        await self._ensure_loaded()
        return self._state.get("robot_credentials_meta")

    async def set_robot_credentials_meta(self, data: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_loaded()
        record = {
            **data,
            "updated_at": _now_iso(),
        }
        async with self._lock:
            self._state["robot_credentials_meta"] = record
            await self._flush_locked()
        return record


hub_state_store = HubStateStore()

