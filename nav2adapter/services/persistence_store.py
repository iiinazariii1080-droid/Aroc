"""
Persistence layer for command_id -> transport_id mapping and last state/result.

SRS requires recovery after restarts. For MVP+ we use a single JSON file.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Optional


import logging

_LOGGER = logging.getLogger(__name__)


@dataclass
class PersistedCommand:
    command_id: str
    transport_id: str
    target_id: Optional[str] = None
    last_state: Optional[int] = None
    last_result: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class PersistedSession:
    command_id: str
    target_id: Optional[str] = None
    start: Optional[Dict[str, Any]] = None
    goal: Optional[Dict[str, Any]] = None
    total_dist_m: Optional[float] = None
    min_remaining_dist_m: Optional[float] = None
    progress_percent: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class JsonPersistenceStore:
    _MAX_COMMANDS = 200  # evict oldest entries above this threshold

    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()
        self._cleanup_orphan_tmp_files()

    def _cleanup_orphan_tmp_files(self) -> None:
        """Remove orphan .tmp files left by SIGKILL during atomic writes."""
        dir_name = os.path.dirname(self.path) or "."
        base_name = os.path.basename(self.path) or "state.json"
        prefix = f".{base_name}."
        try:
            for entry in os.listdir(dir_name):
                if entry.startswith(prefix) and entry.endswith(".tmp"):
                    tmp_path = os.path.join(dir_name, entry)
                    try:
                        os.remove(tmp_path)
                        _LOGGER.info("Removed orphan persistence tmp file: %s", tmp_path)
                    except OSError:
                        pass
        except OSError:
            pass

    async def _read_file(self) -> Dict[str, Any]:
        def _sync_read() -> Dict[str, Any]:
            if not os.path.exists(self.path):
                return {"commands": {}, "sessions": {}}
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
                if not isinstance(raw, dict):
                    raw = {}
                if "commands" not in raw or not isinstance(raw.get("commands"), dict):
                    raw["commands"] = {}
                if "sessions" not in raw or not isinstance(raw.get("sessions"), dict):
                    raw["sessions"] = {}
                return raw

        try:
            return await asyncio.to_thread(_sync_read)
        except Exception:
            # Corrupt JSON should not permanently brick the service.
            # Quarantine and start fresh.
            try:
                await self._quarantine_corrupt_file()
            except Exception:
                pass
            return {"commands": {}, "sessions": {}}

    async def _quarantine_corrupt_file(self) -> None:
        """Best-effort move corrupt file aside to allow fresh persistence."""
        def _sync_quarantine() -> None:
            if not os.path.exists(self.path):
                return
            dir_name = os.path.dirname(self.path) or "."
            base_name = os.path.basename(self.path) or "state.json"
            bad_path = os.path.join(dir_name, f"{base_name}.corrupt")
            try:
                os.replace(self.path, bad_path)
            except Exception:
                # If replace fails (e.g. target exists), just leave it.
                pass

        await asyncio.to_thread(_sync_quarantine)

    async def _atomic_write(self, data: Dict[str, Any]) -> None:
        def _sync_write() -> None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            dir_name = os.path.dirname(self.path) or "."
            base_name = os.path.basename(self.path) or "state.json"
            fd, tmp_path = tempfile.mkstemp(prefix=f".{base_name}.", suffix=".tmp", dir=dir_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.path)
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

        await asyncio.to_thread(_sync_write)

    async def load(self) -> Dict[str, PersistedCommand]:
        async with self._lock:
            raw = await self._read_file()

        commands: Dict[str, PersistedCommand] = {}
        for command_id, payload in (raw.get("commands") or {}).items():
            if not isinstance(payload, dict):
                continue
            commands[command_id] = PersistedCommand(
                command_id=command_id,
                transport_id=str(payload.get("transport_id", "")),
                target_id=payload.get("target_id"),
                last_state=payload.get("last_state"),
                last_result=payload.get("last_result"),
                created_at=payload.get("created_at"),
                updated_at=payload.get("updated_at"),
            )
        return commands

    async def load_sessions(self) -> Dict[str, PersistedSession]:
        async with self._lock:
            raw = await self._read_file()

        sessions: Dict[str, PersistedSession] = {}
        for command_id, payload in (raw.get("sessions") or {}).items():
            if not isinstance(payload, dict):
                continue
            sessions[command_id] = PersistedSession(
                command_id=command_id,
                target_id=payload.get("target_id"),
                start=payload.get("start") if isinstance(payload.get("start"), dict) else None,
                goal=payload.get("goal") if isinstance(payload.get("goal"), dict) else None,
                total_dist_m=payload.get("total_dist_m"),
                min_remaining_dist_m=payload.get("min_remaining_dist_m"),
                progress_percent=payload.get("progress_percent"),
                created_at=payload.get("created_at"),
                updated_at=payload.get("updated_at"),
            )
        return sessions

    async def upsert(self, cmd: PersistedCommand) -> None:
        async with self._lock:
            data = await self._read_file()

            if "commands" not in data or not isinstance(data["commands"], dict):
                data["commands"] = {}

            prev = data["commands"].get(cmd.command_id) if isinstance(data["commands"].get(cmd.command_id), dict) else {}
            data["commands"][cmd.command_id] = {
                "transport_id": cmd.transport_id or prev.get("transport_id"),
                "target_id": cmd.target_id if cmd.target_id is not None else prev.get("target_id"),
                "last_state": cmd.last_state if cmd.last_state is not None else prev.get("last_state"),
                "last_result": cmd.last_result if cmd.last_result is not None else prev.get("last_result"),
                "created_at": cmd.created_at if cmd.created_at is not None else prev.get("created_at"),
                "updated_at": cmd.updated_at if cmd.updated_at is not None else prev.get("updated_at"),
            }

            # Evict oldest entries when exceeding max capacity
            cmds = data["commands"]
            if len(cmds) > self._MAX_COMMANDS:
                # Sort by updated_at (or created_at) ascending; evict oldest
                sorted_ids = sorted(
                    cmds.keys(),
                    key=lambda k: cmds[k].get("updated_at") or cmds[k].get("created_at") or "",
                )
                to_remove = sorted_ids[: len(cmds) - self._MAX_COMMANDS]
                for rid in to_remove:
                    cmds.pop(rid, None)
                    # Also remove matching sessions
                    if isinstance(data.get("sessions"), dict):
                        data["sessions"].pop(rid, None)

            await self._atomic_write(data)

    async def upsert_session(self, s: PersistedSession) -> None:
        async with self._lock:
            data = await self._read_file()

            if "sessions" not in data or not isinstance(data["sessions"], dict):
                data["sessions"] = {}

            prev = data["sessions"].get(s.command_id) if isinstance(data["sessions"].get(s.command_id), dict) else {}
            data["sessions"][s.command_id] = {
                "target_id": s.target_id if s.target_id is not None else prev.get("target_id"),
                "start": s.start if s.start is not None else prev.get("start"),
                "goal": s.goal if s.goal is not None else prev.get("goal"),
                "total_dist_m": s.total_dist_m if s.total_dist_m is not None else prev.get("total_dist_m"),
                "min_remaining_dist_m": s.min_remaining_dist_m if s.min_remaining_dist_m is not None else prev.get("min_remaining_dist_m"),
                "progress_percent": s.progress_percent if s.progress_percent is not None else prev.get("progress_percent"),
                "created_at": s.created_at if s.created_at is not None else prev.get("created_at"),
                "updated_at": s.updated_at if s.updated_at is not None else prev.get("updated_at"),
            }

            await self._atomic_write(data)

    async def delete(self, command_id: str) -> None:
        async with self._lock:
            data = await self._read_file()
            if "commands" not in data or not isinstance(data["commands"], dict):
                return
            data["commands"].pop(command_id, None)
            if "sessions" in data and isinstance(data["sessions"], dict):
                data["sessions"].pop(command_id, None)
            await self._atomic_write(data)

    async def delete_session(self, command_id: str) -> None:
        async with self._lock:
            data = await self._read_file()
            if "sessions" not in data or not isinstance(data["sessions"], dict):
                return
            data["sessions"].pop(command_id, None)
            await self._atomic_write(data)

