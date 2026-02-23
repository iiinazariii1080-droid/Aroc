import sqlite3
from models.base_types import JsonDict, JsonList

DB_PATH = "database.db"

def init_regals_table():
    """Initialize the regals table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS regals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def get_all_regals() -> JsonList:
    """Get all regals from the database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, data FROM regals")
    rows = c.fetchall()
    conn.close()
    return [{"id": row[0], "data": row[1]} for row in rows]

def get_regal(regal_id: int) -> JsonDict:
    """Get a specific regal by ID"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT data FROM regals WHERE id = ?", (regal_id,))
    row = c.fetchone()
    conn.close()
    return {"id": regal_id, "data": row[0]} if row else None

def save_regal(data: str) -> int:
    """Save a new regal and return its ID"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO regals (data) VALUES (?)", (data,))
    regal_id = c.lastrowid
    conn.commit()
    conn.close()
    return regal_id

def update_regal(regal_id: int, data: str) -> bool:
    """Update an existing regal"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE regals SET data = ? WHERE id = ?", (data, regal_id))
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        return success
    except Exception:
        return False

def delete_regal(regal_id: int) -> bool:
    """Delete a regal"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM regals WHERE id = ?", (regal_id,))
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        return success
    except Exception:
        return False
