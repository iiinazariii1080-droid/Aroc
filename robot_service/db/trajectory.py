# db/trajectory.py
import sqlite3
import json

DB_PATH = "database.db"

def calibrate_distance(raw_distance):
    """
    Corrects measured distance using an empirical linear model.
    Formula from calibration: true distance ≈ 0.937 * measured + 45.2
    """
    a = 0.957
    b = 45.2
    return a * raw_distance + b
    
def init_trajectory_table():
    """Create the trajectory table if it does not exist and insert a default row."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trajectory (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT NOT NULL
        )
    """)
    c.execute("SELECT COUNT(*) FROM trajectory")
    if c.fetchone()[0] == 0:
        default_config = json.dumps({
            "prefix": {"active": False, "posX": 0, "posY": 0, "posZ": 0, "speed": 100},
            "postfix": {"active": False, "posX": 0, "posY": 0, "posZ": 0, "speed": 100},
            "gripper": {"active": False},
            "return": {"active": False}
        })
        c.execute("INSERT INTO trajectory (id, data) VALUES (1, ?)", (default_config,))
    conn.commit()
    conn.close()

def get_trajectory():
    """Return the trajectory config as a dict."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT data FROM trajectory WHERE id = 1")
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    else:
        return None

def save_trajectory(config: dict):
    """Update or create the trajectory record."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    config_json = json.dumps(config, ensure_ascii=False)
    c.execute("UPDATE trajectory SET data = ? WHERE id = 1", (config_json,))
    if c.rowcount == 0:
        c.execute("INSERT INTO trajectory (id, data) VALUES (1, ?)", (config_json,))
    conn.commit()
    conn.close()
    return True
