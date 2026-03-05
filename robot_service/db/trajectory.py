# db/trajectory.py
import sqlite3
import json

DB_PATH = "database.db"

def calibrate_distance(raw_distance):
    """
    Corrects measured distance using an empirical linear model.
    Calibrated from two reference points:
      raw=397 -> real=425,  raw=530 -> real=577
    Formula: true distance ≈ 1.1429 * measured - 28.7
    """
    a = 1.1429
    b = -28.7
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
            "baseMove": {"active": True, "posX": -30, "posY": 52, "posZ": -10, "speed": 100},
            "gripper": {"active": True},
            "gripperVerify": {"samples": 3, "required": 2, "intervalMs": 120, "maxReadErrors": 3},
            "gripperVerifySecondChance": {
                "enabled": True,
                "delayMs": 180,
                "samples": 2,
                "required": 1,
                "intervalMs": 90,
                "maxReadErrors": 2,
                "minPcs": 0.30
            },
            "gripperApproach": {
                "stepMm": 2.0,
                "fineStepMm": 1.0,
                "detectConsecutive": 2,
                "sampleIntervalMs": 80,
                "maxReadErrors": 3,
                "enableAtStage2": True,
                "microSettleMm": 1.5,
                "maxStage3TravelMm": 20.0,
                "primaryBudgetRatio": 0.7
            },
            "xySearch": {
                "enabled": True,
                "amplitudeMm": 2.0,
                "maxProbes": 4,
                "zProbeMm": 1.5,
                "velocityPercent": 15
            },
            "liftTest": {
                "enabled": True,
                "liftMm": 8.0,
                "holdMs": 220,
                "samples": 2,
                "required": 2,
                "intervalMs": 100,
                "maxReadErrors": 2,
                "velocityPercent": 20
            },
            "return": {"active": True}
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
