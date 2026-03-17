import os
import sqlite3

DB_PATH = os.path.abspath(os.environ.get("ROBOT_DB_PATH", "database.db"))
DEFAULT_VELOCITY_PERCENT = 20.0


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def get_velocity_percent() -> float:
    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT value FROM robot_settings WHERE key = 'velocity_percent'"
        ).fetchone()
        return float(row[0]) if row else DEFAULT_VELOCITY_PERCENT
    finally:
        conn.close()


def set_velocity_percent(v: float) -> None:
    v = max(1.0, min(100.0, float(v)))
    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_table(conn)
        conn.execute(
            """
            INSERT INTO robot_settings (key, value) VALUES ('velocity_percent', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(v),),
        )
        conn.commit()
    finally:
        conn.close()
