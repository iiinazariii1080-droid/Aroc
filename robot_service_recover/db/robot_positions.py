import sqlite3
import json
from typing import Dict, Any, Optional, List

DB_PATH = "database.db"


def init_robot_positions_table() -> None:
    """Create the robot_positions table if it does not exist."""
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
    if not isinstance(params, dict):
        raise ValueError("field 'params' (dict) is required")

    params_json = json.dumps(params, ensure_ascii=False)

    init_robot_positions_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
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
    init_robot_positions_table()
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
            except Exception:
                params = None
            result_list.append({"id": pid, "name": name, "params": params})
        return result_list
    finally:
        conn.close()


def get_robot_position(position_id: str) -> Optional[Dict[str, Any]]:
    """Return single position by id or None if not found."""
    if not position_id:
        return None
    init_robot_positions_table()
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
        except Exception:
            params = None
        return {"id": pid, "name": name, "params": params}
    finally:
        conn.close()


def delete_robot_position(position_id: str) -> bool:
    """Delete position by id."""
    if not position_id:
        raise ValueError("position_id is required")
    init_robot_positions_table()
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


