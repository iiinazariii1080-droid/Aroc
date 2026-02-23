"""
TextRoom → Robot Joystick Relay (low-latency, backpressure-safe)

Design goals:
- No per-frame http client creation (single persistent httpx.AsyncClient)
- Fast request handler (parse + enqueue only; never waits on robot)
- No backlog: keep ONLY the latest frame (queue size = 1, drop older)
- Optional de-dup by ts (handled in the single forwarder task)
- Minimal hot-path logging (aggregate counters + warnings/errors only)

Run:
  export RELAY_LISTEN_HOST=0.0.0.0
  export RELAY_PORT=9000
  export ROBOT_URL=http://127.0.0.1:8110/joystick/frame
  uvicorn textroom_relay:app --host 0.0.0.0 --port 9000 --workers 1

Notes:
- Use ONE uvicorn worker for strict ordering and simplest concurrency model.
- If you must scale workers, de-dup and ordering become non-deterministic.
"""

from __future__ import annotations

import asyncio
import os
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response
import httpx

# Optional faster JSON
try:
    import orjson  # type: ignore

    def json_loads(b: bytes) -> Any:
        return orjson.loads(b)

    def json_dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)

except Exception:  # pragma: no cover
    import json as _json

    def json_loads(b: bytes) -> Any:
        return _json.loads(b)

    def json_dumps(obj: Any) -> bytes:
        return _json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ---------------------------
# Config
# ---------------------------

ROBOT_URL = os.getenv("ROBOT_URL", "http://127.0.0.1:8110/joystick/frame")

RELAY_LISTEN_HOST = os.getenv("RELAY_LISTEN_HOST", "0.0.0.0")
RELAY_PORT = int(os.getenv("RELAY_PORT", "9000"))

# Forwarding behavior
FORWARD_TIMEOUT_S = float(os.getenv("FORWARD_TIMEOUT_S", "0.25"))
FORWARD_MAX_RETRIES = int(os.getenv("FORWARD_MAX_RETRIES", "0"))  # keep 0 for teleop
DEDUP_ENABLED = os.getenv("DEDUP_ENABLED", "1") not in ("0", "false", "False", "off")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FRAMES = os.getenv("LOG_FRAMES", "0") in ("1", "true", "True", "on")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("textroom_relay")


# ---------------------------
# State
# ---------------------------

@dataclass
class Counters:
    rx_http: int = 0                # received webhook HTTP calls
    rx_textroom_msg: int = 0        # messages of type textroom=message
    rx_frames_parsed: int = 0       # frames parsed successfully
    rx_frames_invalid: int = 0      # frames missing required fields
    q_dropped: int = 0              # dropped because we keep only latest
    fwd_attempts: int = 0
    fwd_ok: int = 0
    fwd_fail: int = 0
    fwd_dedup_skipped: int = 0
    last_rx_ms: int = 0
    last_fwd_ms: int = 0


class LatestFrameQueue:
    """
    Queue that keeps only the most recent item.
    Producer never blocks; consumer awaits items.
    """
    def __init__(self) -> None:
        self._q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=1)

    def put_latest_nowait(self, item: Dict[str, Any]) -> bool:
        """
        Returns True if dropped an older item to insert the new one.
        """
        dropped = False
        try:
            self._q.put_nowait(item)
            return dropped
        except asyncio.QueueFull:
            dropped = True
            try:
                _ = self._q.get_nowait()  # drop old
            except asyncio.QueueEmpty:
                pass
            # Now should fit
            try:
                self._q.put_nowait(item)
            except asyncio.QueueFull:
                # Extremely unlikely; if happens, we just drop.
                pass
            return dropped

    async def get(self) -> Dict[str, Any]:
        return await self._q.get()


# ---------------------------
# App
# ---------------------------

app = FastAPI(title="TextRoom Joystick Relay", version="1.0.0")

queue = LatestFrameQueue()
counters = Counters()

_http: Optional[httpx.AsyncClient] = None
_forwarder_task: Optional[asyncio.Task] = None

_last_forwarded_ts: int = 0  # de-dup strictly inside the forwarder task (single-threaded)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _extract_inner_frame(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Janus TextRoom webhook body example:
      {"textroom":"message", "text":"{...json...}", ...}

    We accept:
      - body["text"] as str JSON
      - body["text"] as dict
    """
    if body.get("textroom") != "message":
        return None

    text = body.get("text")
    if text is None:
        return None

    if isinstance(text, dict):
        return text

    if isinstance(text, str):
        text_s = text.strip()
        if not text_s:
            return None
        try:
            # inner is expected to be JSON of the joystick frame
            import json as _json
            return _json.loads(text_s)
        except Exception:
            # fall back: maybe it's already a plain string (invalid)
            return None

    return None


def _is_valid_frame(frame: Dict[str, Any]) -> bool:
    # Minimal contract (don’t over-validate on hot path)
    if "axes" not in frame or "buttons" not in frame:
        return False
    # ts/ttl are recommended but we can tolerate missing (robot may handle)
    return True


def _normalize_ts_to_int(frame: Dict[str, Any]) -> int:
    """
    We prefer ts in ms since epoch, but we only use it for de-dup.
    If missing or bad, return 0 (disables de-dup for that frame).
    """
    ts = frame.get("ts", 0)
    try:
        ts_i = int(ts)
    except Exception:
        return 0

    # Heuristics:
    # - seconds epoch ~ 10 digits (e.g., 1769462806)
    # - milliseconds epoch ~ 13 digits (e.g., 1769462810525)
    # If seconds, convert to ms to keep monotonicity comparable.
    if 1_000_000_000 <= ts_i < 10_000_000_000:
        ts_i *= 1000
    return ts_i


async def _forward_loop() -> None:
    global _last_forwarded_ts
    assert _http is not None

    while True:
        frame = await queue.get()

        counters.fwd_attempts += 1

        # De-dup in the forwarder task (single serialization point)
        if DEDUP_ENABLED:
            ts_i = _normalize_ts_to_int(frame)
            if ts_i and ts_i <= _last_forwarded_ts:
                counters.fwd_dedup_skipped += 1
                continue

        # Forward with minimal overhead; no hot-path info logs by default.
        ok = False
        last_exc: Optional[Exception] = None

        for attempt in range(FORWARD_MAX_RETRIES + 1):
            try:
                resp = await _http.post(ROBOT_URL, json=frame)
                if 200 <= resp.status_code < 300:
                    ok = True
                    break
                else:
                    # Non-2xx: do not retry aggressively in teleop unless you explicitly want it
                    log.warning("Robot HTTP %s (attempt=%d)", resp.status_code, attempt + 1)
            except Exception as e:
                last_exc = e
                log.warning("Forward error (attempt=%d): %s", attempt + 1, e)

        if ok:
            counters.fwd_ok += 1
            counters.last_fwd_ms = _now_ms()
            if DEDUP_ENABLED:
                ts_i = _normalize_ts_to_int(frame)
                if ts_i:
                    _last_forwarded_ts = ts_i

            if LOG_FRAMES:
                log.info("FWD ok ts=%s axes=%s buttons=%s ttl=%s",
                         frame.get("ts"), frame.get("axes"), frame.get("buttons"), frame.get("ttl"))
        else:
            counters.fwd_fail += 1
            if last_exc:
                log.error("Forward failed: %s", last_exc)
            else:
                log.error("Forward failed: robot returned non-2xx")


@app.on_event("startup")
async def on_startup() -> None:
    global _http, _forwarder_task

    limits = httpx.Limits(max_connections=20, max_keepalive_connections=20, keepalive_expiry=30.0)
    _http = httpx.AsyncClient(
        timeout=httpx.Timeout(FORWARD_TIMEOUT_S),
        limits=limits,
        headers={"Connection": "keep-alive"},
    )

    _forwarder_task = asyncio.create_task(_forward_loop())
    log.info("Relay started. ROBOT_URL=%s timeout=%.3fs dedup=%s", ROBOT_URL, FORWARD_TIMEOUT_S, DEDUP_ENABLED)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _http, _forwarder_task
    if _forwarder_task:
        _forwarder_task.cancel()
        try:
            await _forwarder_task
        except Exception:
            pass
        _forwarder_task = None

    if _http:
        await _http.aclose()
        _http = None

    log.info("Relay stopped.")


@app.post("/textroom-hook")
async def textroom_hook(request: Request) -> Response:
    """
    Fast path:
      - parse webhook JSON
      - extract inner frame
      - enqueue latest (drop older if needed)
      - return immediately

    No awaiting on robot I/O here.
    """
    counters.rx_http += 1
    counters.last_rx_ms = _now_ms()

    raw = await request.body()
    try:
        body = json_loads(raw)
        if not isinstance(body, dict):
            counters.rx_frames_invalid += 1
            return Response(status_code=200)
    except Exception:
        counters.rx_frames_invalid += 1
        return Response(status_code=200)

    inner = _extract_inner_frame(body)
    if inner is None:
        return Response(status_code=200)

    counters.rx_textroom_msg += 1

    if not isinstance(inner, dict) or not _is_valid_frame(inner):
        counters.rx_frames_invalid += 1
        return Response(status_code=200)

    counters.rx_frames_parsed += 1

    # Add server-side receive timestamp (optional, does not break consumer)
    # Keep it lightweight: do not touch axes/buttons arrays.
    inner.setdefault("server_rx_ms", counters.last_rx_ms)

    dropped = queue.put_latest_nowait(inner)
    if dropped:
        counters.q_dropped += 1

    # Do not log full payload; optional per-frame logging is controlled by LOG_FRAMES.
    return Response(status_code=200)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "robot_url": ROBOT_URL,
        "dedup": DEDUP_ENABLED,
        "timeout_s": FORWARD_TIMEOUT_S,
    }


@app.get("/status")
async def status() -> Dict[str, Any]:
    return {
        "counters": counters.__dict__,
        "robot_url": ROBOT_URL,
        "dedup": DEDUP_ENABLED,
        "queue_keep_latest": True,
        "time_ms": _now_ms(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("textroom_relay:app", host=RELAY_LISTEN_HOST, port=RELAY_PORT, log_level=LOG_LEVEL.lower())
