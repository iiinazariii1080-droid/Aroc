from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from .config import ROBOT_API_KEY_FILE

logger = logging.getLogger(__name__)


class RobotSecretStore:
    """
    Keeps robot API key only in memory.
    Initial value is loaded from environment (ROBOT_API_KEY) or a configured file.
    """

    def __init__(self, env_var: str = "ROBOT_API_KEY", file_path: str | None = None) -> None:
        self._env_var = env_var
        self._lock = asyncio.Lock()
        self._file_path = Path(file_path) if file_path else (Path(ROBOT_API_KEY_FILE) if ROBOT_API_KEY_FILE else None)
        self._api_key = self._load_initial_api_key()

    def _load_initial_api_key(self) -> Optional[str]:
        from_env = os.getenv(self._env_var)
        if from_env:
            return from_env
        return self._read_api_key_file()

    def _read_api_key_file(self) -> Optional[str]:
        if self._file_path is None:
            return None
        try:
            if not self._file_path.is_file():
                return None
            value = self._file_path.read_text(encoding="utf-8").strip()
            return value or None
        except OSError as exc:
            logger.warning("Failed to read API key file %s: %s", self._file_path, exc)
            return None

    async def get_api_key(self) -> Optional[str]:
        async with self._lock:
            return self._api_key

    async def set_api_key(self, value: Optional[str]) -> None:
        async with self._lock:
            self._api_key = value

    async def masked_api_key(self) -> Optional[str]:
        api_key = await self.get_api_key()
        if not api_key:
            return None
        if len(api_key) <= 4:
            return "*" * len(api_key)
        return f"{api_key[:2]}***{api_key[-2:]}"


robot_secret_store = RobotSecretStore()

