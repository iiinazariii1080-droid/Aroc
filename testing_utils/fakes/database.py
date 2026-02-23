"""Fake in-memory database helpers for testing services that use SQLite.

Services that use SQLite (nav2adapter, robot_service) can use these
helpers to test database operations without touching the file system.

Usage::

    from testing_utils.fakes.database import in_memory_db, patch_db_path

    @pytest.fixture
    def db(tmp_path, monkeypatch):
        return patch_db_path(monkeypatch, "nav2adapter.db.robot_positions", tmp_path / "test.db")
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest


def in_memory_db() -> sqlite3.Connection:
    """Create a fresh in-memory SQLite database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def patch_db_path(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    db_file: Path | str,
) -> Path:
    """Override the DB_PATH constant in a module to point at a temp file.

    Args:
        monkeypatch: pytest monkeypatch fixture
        module_path: dotted module path (e.g., ``nav2adapter.db.robot_positions``)
        db_file: path to the temp database file

    Returns:
        The db_file path for assertions.
    """
    db_path = Path(db_file)
    monkeypatch.setattr(f"{module_path}.DB_PATH", str(db_path))
    return db_path


def create_robot_positions_table(conn: sqlite3.Connection) -> None:
    """Create the robot_positions table schema (nav2adapter)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_positions (
            id TEXT PRIMARY KEY,
            name TEXT,
            params TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_robot_positions_name_ci "
        "ON robot_positions(LOWER(name))"
    )
    conn.commit()


def seed_robot_positions(
    conn: sqlite3.Connection,
    positions: list[dict[str, Any]],
) -> None:
    """Insert test positions into the robot_positions table."""
    import json

    for pos in positions:
        conn.execute(
            "INSERT OR REPLACE INTO robot_positions (id, name, params) VALUES (?, ?, ?)",
            (pos["id"], pos.get("name", ""), json.dumps(pos.get("params", {}))),
        )
    conn.commit()
