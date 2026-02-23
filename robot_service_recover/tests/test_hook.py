from fastapi import FastAPI, Request
import json
import logging
import httpx
import time

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("textroom")

app = FastAPI()

# Robot address
ROBOT_URL = "http://127.0.0.1:8110/joystick/frame"

# Simple cache of recent ts to avoid duplicate sends
last_forwarded_ts_ms = 0
last_keepalive_forward_ms = 0
# At 30 Hz cycle ≈33 ms. Round to 35 to avoid extra sends.
KEEPALIVE_FORWARD_MS = 35


def _normalize_ts(raw_ts) -> tuple[float, int]:
    if raw_ts is None:
        now = time.time()
        return now, int(now * 1000)
    if raw_ts > 1e12:
        return raw_ts / 1000.0, int(raw_ts)
    if raw_ts > 1e9:
        return float(raw_ts), int(raw_ts * 1000)
    return float(raw_ts), int(raw_ts * 1000)


def _has_activity(frame: dict) -> bool:
    axes = frame.get("axes") or []
    buttons = frame.get("buttons") or []
    return any(abs(float(a)) > 1e-3 for a in axes) or any(bool(b) for b in buttons)


async def forward_to_robot(frame: dict):
    """Forward joystick frame to robot. Do not raise on errors."""
    global last_forwarded_ts_ms, last_keepalive_forward_ms

    ts_sec, ts_ms = _normalize_ts(frame.get("ts"))
    frame["ts"] = ts_sec
    ttl = frame.get("ttl", 200)
    frame["ttl"] = max(50, min(500, ttl))

    if ts_ms <= last_forwarded_ts_ms:
        log.debug("Skip duplicate/old frame ts=%s (last=%s)", ts_ms, last_forwarded_ts_ms)
        return

    if not _has_activity(frame):
        if ts_ms - last_keepalive_forward_ms < KEEPALIVE_FORWARD_MS:
            return
        last_keepalive_forward_ms = ts_ms

    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.post(ROBOT_URL, json=frame)
            if resp.status_code == 200:
                log.info("Forwarded to robot: axes=%s buttons=%s ttl=%s",
                         frame.get("axes"), frame.get("buttons"), frame.get("ttl"))
                last_forwarded_ts_ms = ts_ms
            else:
                log.warning("Robot responded %d: %s", resp.status_code, resp.text)
    except Exception as e:
        log.error("Failed to forward to robot: %s", e)

@app.post("/textroom-hook")
async def textroom_hook(request: Request):
    body = await request.json()
    # Pretty-print full payload
    log.info("TEXTROOM HOOK:\n%s", json.dumps(body, indent=2, ensure_ascii=False))

    # If message from room — show axes/buttons and forward to robot
    if body.get("textroom") == "message":
        raw = body.get("text") or ""
        try:
            inner = json.loads(raw)
            log.info(
                "JOYSTICK: ts=%s axes=%s buttons=%s",
                inner.get("ts"),
                inner.get("axes"),
                inner.get("buttons"),
            )
            # Forward to robot
            await forward_to_robot(inner)
        except json.JSONDecodeError:
            log.warning("Cannot parse inner JSON: %r", raw)

    return {}  # Janus does not require a specific response


@app.get("/health")
async def health():
    """Simple health-check for keepalive."""
    return {"status": "ok", "robot_url": ROBOT_URL}
