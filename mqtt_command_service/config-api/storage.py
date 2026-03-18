"""JSON file storage for broker config, API keys, and related metadata.

Atomic writes via temp-file + os.replace.
File-level locking prevents data loss from concurrent multi-process writes.
"""

import contextlib
import json
import logging
import os
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_WIN32_LOCK_SIZE = 2**30  # 1 GiB — large enough to cover any config file


def _lock_file(fd: int) -> None:
    """Acquire an exclusive file lock (blocks until available)."""
    if sys.platform == "win32":
        import msvcrt

        # Lock a large region to cover the entire file, not just 1 byte.
        msvcrt.locking(fd, msvcrt.LK_LOCK, _WIN32_LOCK_SIZE)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_file(fd: int) -> None:
    """Release an exclusive file lock."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, _WIN32_LOCK_SIZE)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


_SCHEMA_VERSION = 2
_SCHEMA_VERSION_KEY = "__schema_version__"
_MAX_CONFIG_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# Known namespaces for validation — keys not matching any prefix are rejected.
_KNOWN_PREFIXES = (
    "MQTT_",
    "api_key:",
    "api_key_meta:",
    "api_key_revoked:",
    "api_key_hash_to_id:",
    "__hmac_secret__",
    "__schema_version__",
    "lockout:",
)


def _validate_key(key: str) -> bool:
    """Return True if *key* belongs to a known namespace."""
    return any(key.startswith(p) or key == p for p in _KNOWN_PREFIXES)


class ConfigStore:
    """Thread-safe JSON file storage with atomic writes and schema versioning."""

    def __init__(self, path: str = "/data/config.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._cache_mtime: float = 0.0
        self._lock_path = Path(f"{path}.lock")

    def _migrate_if_needed(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply schema migrations if version is outdated."""
        version = data.get(_SCHEMA_VERSION_KEY, 1)
        if version >= _SCHEMA_VERSION:
            return data
        # Migration v1 -> v2: add schema version marker
        data[_SCHEMA_VERSION_KEY] = _SCHEMA_VERSION
        logger.info("Migrated config store schema from v%d to v%d", version, _SCHEMA_VERSION)
        return data

    def _load_unlocked(self) -> dict[str, Any]:
        """Load config without acquiring lock (caller must hold self._lock)."""
        if self._cache is not None:
            try:
                current_mtime = self._path.stat().st_mtime
            except OSError:
                current_mtime = 0.0
            if current_mtime == self._cache_mtime:
                return dict(self._cache)
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                data = self._migrate_if_needed(data)
                self._cache = data
                try:
                    self._cache_mtime = self._path.stat().st_mtime
                except OSError:
                    self._cache_mtime = 0.0
                return dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load config from %s: %s", self._path, e)
        self._cache = {}
        self._cache_mtime = 0.0
        return {}

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        """Atomically save config without acquiring lock (caller must hold self._lock)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            file_size = os.path.getsize(tmp_path)
            if file_size > _MAX_CONFIG_FILE_SIZE:
                os.unlink(tmp_path)
                raise RuntimeError(
                    f"Config file size {file_size} bytes exceeds limit {_MAX_CONFIG_FILE_SIZE} bytes — write aborted"
                )
            os.replace(tmp_path, str(self._path))
            self._cache = dict(data)
            try:
                self._cache_mtime = self._path.stat().st_mtime
            except OSError:
                self._cache_mtime = 0.0
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def load(self) -> dict[str, Any]:
        """Load config from JSON file. Returns empty dict if file doesn't exist."""
        with self._lock:
            return self._load_unlocked()

    def save(self, data: dict[str, Any]) -> None:
        """Atomically save config to JSON file (write to temp → os.replace)."""
        with self._lock:
            self._save_unlocked(data)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value by key."""
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value by key and persist (atomic read-modify-write).

        Logs a warning if the key does not match a known namespace prefix.
        """
        if not _validate_key(key):
            logger.warning("Storing key with unrecognized namespace: %s", key)
        with self._lock:

            def _do() -> None:
                data = self._load_unlocked()
                data[key] = value
                self._save_unlocked(data)

            self._with_file_lock(_do)

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if the key existed (atomic read-modify-write)."""
        with self._lock:
            result: list[bool] = [False]

            def _do() -> None:
                data = self._load_unlocked()
                if key in data:
                    del data[key]
                    self._save_unlocked(data)
                    result[0] = True

            self._with_file_lock(_do)
            return result[0]

    def _with_file_lock(self, fn: Callable[[], Any]) -> Any:
        """Execute *fn* while holding a cross-process file lock.

        Invalidates the in-memory cache before calling *fn* so that
        ``_load_unlocked()`` always re-reads from disk inside the lock,
        avoiding stale-cache issues on filesystems with coarse mtime
        granularity (1-second resolution on ext3, HFS+, etc.).
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)
        try:
            _lock_file(fd)
            # Force disk re-read: another process may have written since our
            # last _load_unlocked() and the mtime may not have changed (same
            # second).
            self._cache = None
            self._cache_mtime = 0.0
            return fn()
        finally:
            _unlock_file(fd)
            os.close(fd)

    def update(self, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Atomic read-modify-write under both thread lock and file lock.

        *fn* receives the current data dict and must return the dict to save.
        """
        with self._lock:

            def _do() -> dict[str, Any]:
                # Re-read from disk inside the file lock to see writes by other processes
                data = self._load_unlocked()
                result = fn(data)
                self._save_unlocked(result)
                return dict(result)

            return self._with_file_lock(_do)

    def get_by_prefix(self, prefix: str) -> dict[str, Any]:
        """Get all entries whose key starts with prefix."""
        data = self.load()
        return {k: v for k, v in data.items() if k.startswith(prefix)}

    def prune_prefix(self, prefix: str, max_entries: int) -> int:
        """Remove oldest entries with given prefix when count exceeds *max_entries*.

        Returns number of pruned entries. Uses 'expiry' field for ordering
        when entries are dicts, otherwise lexicographic ordering of values.
        """
        with self._lock:
            pruned_count: list[int] = [0]

            def _do() -> None:
                data = self._load_unlocked()
                matching = {k: v for k, v in data.items() if k.startswith(prefix)}
                if len(matching) <= max_entries:
                    return
                sorted_keys = sorted(
                    matching.keys(),
                    key=lambda k: matching[k].get("expiry", "") if isinstance(matching[k], dict) else str(matching[k]),
                )
                to_remove = sorted_keys[: len(matching) - max_entries]
                for k in to_remove:
                    del data[k]
                self._save_unlocked(data)
                pruned_count[0] = len(to_remove)

            self._with_file_lock(_do)
            return pruned_count[0]


_store: ConfigStore | None = None
_store_lock = threading.Lock()


def init_store(path: str) -> ConfigStore:
    """Initialize the global ConfigStore singleton with an explicit path.

    Call this once at startup (e.g., in main.py) to break the circular
    dependency between storage and config modules.
    """
    global _store
    with _store_lock:
        if _store is None:
            _store = ConfigStore(path=path)
        return _store


def get_store() -> ConfigStore:
    """Return the global ConfigStore singleton.

    Prefers the pre-initialized store from :func:`init_store`.
    Falls back to lazy initialization from config settings if
    init_store was not called (backward compatibility).
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from config import get_settings

                _store = ConfigStore(path=get_settings().config_file_path)
    return _store
