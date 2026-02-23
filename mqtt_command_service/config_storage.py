"""
Module for storing configuration in database.
Supports multiple backends: SQLite (default), PostgreSQL.
"""
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConfigStorage:
    """Abstraction for configuration storage with versioning support."""

    def __init__(self, db_path: str | None = None):
        """
        Args:
            db_path: Path to SQLite DB.
                     If None, uses CONFIG_DB_PATH from env or config.db in working directory.
        """
        if db_path is None:
            from env_settings import get_env_settings
            db_path = get_env_settings().config_db_path
        if db_path is None:
            db_path = str(Path(__file__).parent / "config.db")

        # Create directory for DB if it doesn't exist
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema with WAL mode for concurrent access."""
        with self._get_connection() as conn:
            # Enable WAL mode for concurrent access
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")  # balance between safety and performance
            conn.execute("PRAGMA busy_timeout=5000")  # 5 second timeout

            conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS config_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    previous_value TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT,
                    reason TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_config_history_key
                ON config_history(key, updated_at DESC)
            """)

            conn.commit()
            logger.info("Config storage initialized at %s (WAL mode enabled)", self.db_path)

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database operations."""
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get configuration value."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT value FROM config WHERE key = ?",
                (key,)
            )
            row = cursor.fetchone()
            return row["value"] if row else default

    def set(
        self,
        key: str,
        value: str,
        updated_by: str | None = None,
        reason: str | None = None
    ) -> bool:
        """
        Set configuration value with history preservation.

        Args:
            key: Configuration key
            value: New value
            updated_by: Who made the change (e.g., "mqtt", "api", "user")
            reason: Reason for change

        Returns:
            True if successful, False otherwise
        """
        try:
            now = datetime.now(UTC).isoformat()

            with self._get_connection() as conn:
                # Get old value for history
                cursor = conn.execute(
                    "SELECT value FROM config WHERE key = ?",
                    (key,)
                )
                old_row = cursor.fetchone()
                previous_value = old_row["value"] if old_row else None

                # Update or insert new value
                conn.execute("""
                    INSERT INTO config (key, value, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                """, (key, value, now, updated_by))

                # Save to history
                conn.execute("""
                    INSERT INTO config_history
                    (key, value, previous_value, updated_at, updated_by, reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (key, value, previous_value, now, updated_by, reason))

                conn.commit()
                self._prune_history(conn)
                logger.info("Config updated: %s = %s (by %s)", key, value, updated_by)
                return True

        except Exception as e:
            logger.error("Failed to set config %s: %s", key, e)
            return False

    def get_all(self) -> dict[str, str]:
        """Get all configuration values."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT key, value FROM config")
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    def get_history(
        self,
        key: str | None = None,
        limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Get configuration change history.

        Args:
            key: Filter by key (None = all keys)
            limit: Maximum number of records
        """
        with self._get_connection() as conn:
            if key:
                cursor = conn.execute("""
                    SELECT key, value, previous_value, updated_at, updated_by, reason
                    FROM config_history
                    WHERE key = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                """, (key, limit))
            else:
                cursor = conn.execute("""
                    SELECT key, value, previous_value, updated_at, updated_by, reason
                    FROM config_history
                    ORDER BY updated_at DESC
                    LIMIT ?
                """, (limit,))

            return [
                {
                    "key": row["key"],
                    "value": row["value"],
                    "previous_value": row["previous_value"],
                    "updated_at": row["updated_at"],
                    "updated_by": row["updated_by"],
                    "reason": row["reason"],
                }
                for row in cursor.fetchall()
            ]

    def get_latest_update_time(self, keys: list) -> str | None:
        """Return the most recent updated_at ISO string for the given keys, or None."""
        if not keys:
            return None
        placeholders = ",".join("?" for _ in keys)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT MAX(updated_at) AS latest FROM config_history WHERE key IN ({placeholders})",
                keys,
            )
            row = cursor.fetchone()
            return row["latest"] if row and row["latest"] else None

    def set_json(
        self,
        key: str,
        value: dict[str, Any],
        updated_by: str | None = None,
        reason: str | None = None
    ) -> bool:
        """
        Set JSON value in storage.

        Args:
            key: Configuration key
            value: Dictionary to store as JSON
            updated_by: Who made the change
            reason: Reason for change

        Returns:
            True if successful, False otherwise
        """
        try:
            json_str = json.dumps(value, ensure_ascii=False)
            return self.set(key, json_str, updated_by=updated_by, reason=reason)
        except (TypeError, ValueError) as e:
            logger.error("Failed to serialize JSON for key %s: %s", key, e)
            return False

    def get_json(
        self,
        key: str,
        default: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """
        Get JSON value from storage.

        Args:
            key: Configuration key
            default: Default value if key not found or invalid JSON

        Returns:
            Dictionary if found and valid, default otherwise
        """
        value = self.get(key)
        if value is None:
            return default

        try:
            result: dict[str, Any] = json.loads(value)
            return result
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse JSON for key %s: %s", key, e)
            return default

    # ---- History pruning --------------------------------------------------
    _MAX_HISTORY_PER_KEY = 100
    _PRUNE_INTERVAL_S = 60  # run at most once per minute
    _last_prune_ts: float = 0.0

    def _prune_history(self, conn: sqlite3.Connection) -> None:
        """Delete old history entries, keeping the last N per key."""
        now = time.monotonic()
        if now - self._last_prune_ts < self._PRUNE_INTERVAL_S:
            return
        self._last_prune_ts = now
        try:
            conn.execute("""
                DELETE FROM config_history
                WHERE rowid NOT IN (
                    SELECT rowid FROM (
                        SELECT rowid, key,
                               ROW_NUMBER() OVER (PARTITION BY key ORDER BY updated_at DESC) AS rn
                        FROM config_history
                    )
                    WHERE rn <= ?
                )
            """, (self._MAX_HISTORY_PER_KEY,))
        except Exception as e:
            logger.debug("History pruning skipped: %s", e)

    def batch_update(
        self,
        updates: dict[str, str],
        updated_by: str | None = None,
        reason: str | None = None
    ) -> bool:
        """
        Atomically update multiple configuration values.

        All updates succeed or all fail (transaction).

        Args:
            updates: Dictionary of key -> value mappings
            updated_by: Who made the change
            reason: Reason for change

        Returns:
            True if all updates successful, False otherwise
        """
        if not updates:
            return True

        try:
            now = datetime.now(UTC).isoformat()

            with self._get_connection() as conn:
                try:
                    # Get old values for history
                    old_values = {}
                    for key in updates:
                        cursor = conn.execute(
                            "SELECT value FROM config WHERE key = ?",
                            (key,)
                        )
                        row = cursor.fetchone()
                        old_values[key] = row["value"] if row else None

                    # Update or insert all values
                    for key, value in updates.items():
                        conn.execute("""
                            INSERT INTO config (key, value, updated_at, updated_by)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(key) DO UPDATE SET
                                value = excluded.value,
                                updated_at = excluded.updated_at,
                                updated_by = excluded.updated_by
                        """, (key, value, now, updated_by))

                        # Save to history
                        conn.execute("""
                            INSERT INTO config_history
                            (key, value, previous_value, updated_at, updated_by, reason)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (key, value, old_values.get(key), now, updated_by, reason))

                    # Commit transaction (sqlite3 module manages BEGIN implicitly)
                    conn.commit()
                    self._prune_history(conn)
                    logger.info("Batch config update: %d keys updated (by %s)", len(updates), updated_by)
                    return True

                except Exception as e:
                    # Rollback on any error
                    try:
                        conn.rollback()
                    except Exception as rollback_error:
                        logger.error("Failed to rollback transaction: %s", rollback_error)
                    logger.error("Batch config update failed, rolled back: %s", e)
                    raise  # Re-raise original exception

        except Exception as e:
            logger.error("Failed to batch update config: %s", e)
            return False


# Global instance (lazy initialization)
_storage: ConfigStorage | None = None
_storage_lock = threading.Lock()


def get_storage() -> ConfigStorage:
    """Get global configuration storage instance (thread-safe)."""
    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                _storage = ConfigStorage()
    return _storage


def save_config_to_db(
    key: str,
    value: str,
    updated_by: str = "system",
    reason: str | None = None
) -> bool:
    """Save configuration to database."""
    return get_storage().set(key, value, updated_by=updated_by, reason=reason)


def get_config_from_db(key: str, default: str | None = None) -> str | None:
    """Get configuration from database."""
    return get_storage().get(key, default)


def get_all_config_from_db() -> dict[str, str]:
    """Get all configuration from database."""
    return get_storage().get_all()


def get_config_history(key: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Get configuration change history."""
    return get_storage().get_history(key, limit)

