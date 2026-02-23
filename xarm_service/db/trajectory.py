# db/trajectory.py
"""
Trajectory config for the smart‑grasp pipeline.

Stores a single‑row JSON config in SQLite with named motion phases:
  prefix   — camera→gripper offset (applied before approach)
  baseMove — main descent vector (posZ added to measured depth)
  postfix  — correction after gripping / lifting
  gripper  — whether to activate vacuum automatically
  return   — whether to return to the saved joint position
"""
import json
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "database.db"

# ── Default config ─────────────────────────────────────────────────────────

_DEFAULT_CONFIG = {
    "prefix":   {"active": False, "posX": 0, "posY": 0, "posZ": 0, "speed": 100},
    "baseMove": {"active": True,  "posX": 0, "posY": 0, "posZ": 0, "speed": 15},
    "postfix":  {"active": False, "posX": 0, "posY": 0, "posZ": 0, "speed": 100},
    "gripper":  {"active": True},
    "return":   {"active": False},
}


def calibrate_distance(raw_distance: float) -> float:
    """
    Corrects measured distance using an empirical linear model.
    Formula from calibration: true distance ≈ 0.957 * measured + 45.2
    """
    a = 0.957
    b = 45.2
    return a * raw_distance + b


def init_trajectory_table() -> None:
    """Create the trajectory table if it does not exist and insert a default row."""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS trajectory (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL
            )
        """)
        c.execute("SELECT COUNT(*) FROM trajectory")
        if c.fetchone()[0] == 0:
            c.execute(
                "INSERT INTO trajectory (id, data) VALUES (1, ?)",
                (json.dumps(_DEFAULT_CONFIG),),
            )
        conn.commit()
    finally:
        conn.close()


def get_trajectory() -> Optional[dict]:
    """Return the trajectory config as a dict (or None)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT data FROM trajectory WHERE id = 1")
        row = c.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception:
        logger.exception("Failed to read trajectory config")
    return None


def save_trajectory(config: dict) -> bool:
    """Update or create the trajectory record."""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        config_json = json.dumps(config, ensure_ascii=False)
        c.execute("UPDATE trajectory SET data = ? WHERE id = 1", (config_json,))
        if c.rowcount == 0:
            c.execute(
                "INSERT INTO trajectory (id, data) VALUES (1, ?)", (config_json,),
            )
        conn.commit()
        return True
    except Exception:
        logger.exception("Failed to save trajectory config")
        return False
    finally:
        conn.close()
