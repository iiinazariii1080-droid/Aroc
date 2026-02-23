import os
import sqlite3
import json
from typing import Dict, Any, Optional, List

# Allow overriding DB path in Docker (volume mount)
# Convert to absolute path to ensure directory creation works correctly
_db_path_raw = os.getenv("ROBOT_POSITIONS_DB_PATH", "database.db")
DB_PATH = os.path.abspath(_db_path_raw)


def init_robot_positions_table() -> None:
    """Create the robot_positions table if it does not exist."""
    # Ensure the directory for the database file exists and is writable
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, mode=0o755, exist_ok=True)
            except OSError as e:
                raise RuntimeError(f"Failed to create database directory '{db_dir}': {e}") from e
        # Check if directory is writable
        if not os.access(db_dir, os.W_OK):
            raise RuntimeError(
                f"Database directory '{db_dir}' is not writable. "
                f"Please check permissions or ensure the directory is owned by the correct user."
            )
    
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS robot_positions (
                id TEXT PRIMARY KEY,
                name TEXT,
                params TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Ensure names are unique (case-insensitive) for addressing by name.
        # NOTE: if there are already duplicates in an existing DB, this may fail to create.
        # We still keep the app running; validation in save_robot_position will enforce uniqueness going forward.
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_robot_positions_name_ci ON robot_positions(LOWER(name))")
        except Exception:
            # best-effort
            pass
        conn.commit()
    finally:
        conn.close()


def save_robot_position(new_position: Dict[str, Any]) -> bool:
    """
    Insert or replace robot position.

    Expected payload format:
        {
            "id": "unique_id",            # required
            "name": "Human name",         # optional
            "params": { ... }              # required, RobotMoveRequest-compatible dict
        }
    """
    if not isinstance(new_position, dict):
        raise ValueError("new_position must be a dict")

    position_id: Optional[str] = new_position.get("id")
    params: Optional[Dict[str, Any]] = new_position.get("params")
    name: Optional[str] = new_position.get("name")

    if not position_id or not isinstance(position_id, str):
        raise ValueError("field 'id' (str) is required")
    if not isinstance(name, str) or not name.strip():
        # IMPORTANT: name is used as a stable external identifier (target_id)
        raise ValueError("field 'name' (non-empty str) is required and must be unique")
    if not isinstance(params, dict):
        raise ValueError("field 'params' (dict) is required")

    name = name.strip()
    params_json = json.dumps(params, ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        # Enforce unique name (case-insensitive) at write time.
        c.execute(
            "SELECT id FROM robot_positions WHERE LOWER(name) = LOWER(?) AND id <> ? LIMIT 1",
            (name, position_id),
        )
        row = c.fetchone()
        if row:
            raise ValueError(f"Duplicate name '{name}' is not allowed")

        c.execute(
            """
            INSERT INTO robot_positions (id, name, params)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                params=excluded.params
            """,
            (position_id, name, params_json),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_robot_positions_list() -> List[Dict[str, Any]]:
    """Return all saved positions as a list of objects {id, name, params}."""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("SELECT id, name, params FROM robot_positions ORDER BY created_at DESC")
        rows = c.fetchall()
        result_list: List[Dict[str, Any]] = []
        for row in rows:
            pid, name, params_json = row
            try:
                params = json.loads(params_json) if params_json else None
            except Exception as exc:
                import logging
                logging.getLogger(__name__).debug("Failed to parse params JSON for position %s: %s", pid, exc)
                params = None
            result_list.append({"id": pid, "name": name, "params": params})
        return result_list
    finally:
        conn.close()


def get_robot_position(position_id: str) -> Optional[Dict[str, Any]]:
    """Return single position by id or None if not found."""
    if not position_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("SELECT id, name, params FROM robot_positions WHERE id = ?", (position_id,))
        row = c.fetchone()
        if not row:
            return None
        pid, name, params_json = row
        try:
            params = json.loads(params_json) if params_json else None
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Failed to parse params JSON for position %s: %s", pid, exc)
            params = None
        return {"id": pid, "name": name, "params": params}
    finally:
        conn.close()


def get_robot_position_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Return single position by unique name (case-insensitive) or None if not found."""
    if not isinstance(name, str) or not name.strip():
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, name, params FROM robot_positions WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (name.strip(),),
        )
        row = c.fetchone()
        if not row:
            return None
        pid, name_db, params_json = row
        try:
            params = json.loads(params_json) if params_json else None
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Failed to parse params JSON for position %s: %s", pid, exc)
            params = None
        return {"id": pid, "name": name_db, "params": params}
    finally:
        conn.close()


def delete_robot_position(position_id: str) -> bool:
    """Delete position by id."""
    if not position_id:
        raise ValueError("position_id is required")
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("DELETE FROM robot_positions WHERE id = ?", (position_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


# Ensure table exists on import
init_robot_positions_table()


