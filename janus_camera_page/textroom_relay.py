import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request, Response

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("textroom_relay")

ROBOT_URL = os.getenv("ROBOT_URL", "http://127.0.0.1:8110/joystick/frame")

# Очередь кадров: ограниченная, чтобы не накапливать лаг.
# Если робот/HTTP тормозит — мы лучше потеряем промежуточные кадры, чем добавим latency.
QUEUE_MAX = int(os.getenv("QUEUE_MAX", "50"))

# Логировать статистику не чаще чем раз в N секунд
STATS_EVERY_S = float(os.getenv("STATS_EVERY_S", "2.0"))

app = FastAPI()

_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX)
_client: Optional[httpx.AsyncClient] = None
_worker_task: Optional[asyncio.Task] = None

_last_ts_forwarded: int = 0  # для защиты от дублей/старых
_seen = 0
_enqueued = 0
_dropped = 0
_forwarded = 0
_errors = 0
_last_stats_t = 0.0

# ── Ping/pong для измерения joystick e2e latency ──
_last_pong: Optional[Dict[str, Any]] = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_ping(frame: Dict[str, Any]) -> bool:
    return isinstance(frame, dict) and frame.get("type") == "ping" and "ts" in frame


def _is_valid_frame(frame: Dict[str, Any]) -> bool:
    """Validate joystick frame structure and value bounds (DEF-08)."""
    if not isinstance(frame, dict):
        return False
    # ping-фреймы валидны без axes/buttons
    if _is_ping(frame):
        return True
    if "ts" not in frame or "axes" not in frame or "buttons" not in frame:
        return False
    ts = frame.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    # Axes: list of floats in [-1.0, 1.0], max 8 (standard gamepad)
    axes = frame.get("axes")
    if not isinstance(axes, list) or len(axes) > 8:
        return False
    for v in axes:
        if not isinstance(v, (int, float)) or v < -1.0 or v > 1.0:
            return False
    # Buttons: list of 0/1, max 20 (standard gamepad)
    buttons = frame.get("buttons")
    if not isinstance(buttons, list) or len(buttons) > 20:
        return False
    for v in buttons:
        if v not in (0, 1, 0.0, 1.0):
            return False
    return True


def _extract_inner_frame(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Janus TextRoom hook присылает body["text"] как JSON-строку
    if body.get("textroom") != "message":
        return None
    raw = body.get("text")
    if not raw:
        return None
    try:
        inner = json.loads(raw)
        if not isinstance(inner, dict):
            return None
        # normalize ts
        ts = inner.get("ts")
        if isinstance(ts, float):
            inner["ts"] = int(ts)
        return inner
    except Exception:
        return None


async def _forward_worker() -> None:
    global _client, _last_ts_forwarded, _forwarded, _errors, _last_stats_t, _last_pong

    assert _client is not None

    while True:
        frame = await _queue.get()
        try:
            ts = int(frame.get("ts", 0))

            # ── Ping-фрейм: замеряем e2e, НЕ форвардим роботу ──
            if _is_ping(frame):
                relay_rx_ms = frame.get("relay_rx_ms", _now_ms())
                relay_fwd_ms = _now_ms()
                _last_pong = {
                    "id": frame.get("id", 0),
                    "browser_ts": ts,
                    "relay_rx_ms": relay_rx_ms,
                    "relay_fwd_ms": relay_fwd_ms,
                    "robot_elapsed_ms": 0,
                    "server_ms": _now_ms(),
                }
                _forwarded += 1
                continue

            if ts <= _last_ts_forwarded:
                # старый/дубликат — тихо пропускаем
                continue

            # Важно: робот локальный (127.0.0.1), keep-alive даст минимальную задержку
            resp = await _client.post(ROBOT_URL, json=frame)
            if resp.status_code == 200:
                _last_ts_forwarded = ts
                _forwarded += 1
            else:
                _errors += 1
                log.warning("Robot HTTP %s: %s", resp.status_code, resp.text[:200])

        except Exception as e:
            _errors += 1
            log.warning("Forward error: %s", e)

        finally:
            _queue.task_done()

        # редкое логирование статистики
        now = time.time()
        if now - _last_stats_t >= STATS_EVERY_S:
            _last_stats_t = now
            log.info(
                "stats: seen=%d enq=%d drop=%d fwd=%d err=%d q=%d last_ts=%d",
                _seen, _enqueued, _dropped, _forwarded, _errors, _queue.qsize(), _last_ts_forwarded
            )


@app.on_event("startup")
async def _startup() -> None:
    global _client, _worker_task
    # Persistent AsyncClient: никаких AsyncClient() на каждый кадр
    timeout = httpx.Timeout(connect=0.2, read=0.6, write=0.6, pool=0.6)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)

    _client = httpx.AsyncClient(timeout=timeout, limits=limits)
    _worker_task = asyncio.create_task(_forward_worker())
    log.info("Relay started. ROBOT_URL=%s QUEUE_MAX=%d", ROBOT_URL, QUEUE_MAX)


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _client, _worker_task
    if _worker_task:
        _worker_task.cancel()
    if _client:
        await _client.aclose()


@app.post("/textroom-hook")
async def textroom_hook(request: Request) -> Response:
    """
    Быстрый приём входящего вебхука от Janus.
    Никаких indent/log payload на каждый кадр, никаких await POST к роботу здесь.
    """
    # D3: Only accept webhooks from localhost (Janus runs on same host)
    client_ip = request.client.host if request.client else ""
    if client_ip not in ("127.0.0.1", "::1"):
        log.warning("textroom-hook rejected from non-local IP: %s", client_ip)
        return Response(status_code=403, content="forbidden")

    global _seen, _enqueued, _dropped

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="bad json")

    _seen += 1
    inner = _extract_inner_frame(body)
    if not inner or not _is_valid_frame(inner):
        # не джойстик-сообщение → быстро OK
        return Response(status_code=200, content="ok")

    # Штамп времени получения relay для всех фреймов (ping и обычных)
    inner["relay_rx_ms"] = _now_ms()

    # Коалесинг при переполнении очереди: если очередь полна — выбрасываем старое и кладём новое.
    if _queue.full():
        try:
            _queue.get_nowait()
            _queue.task_done()
            _dropped += 1
        except Exception:
            _dropped += 1

    try:
        _queue.put_nowait(inner)
        _enqueued += 1
    except Exception:
        _dropped += 1

    return Response(status_code=200, content="ok")


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "robot_url": ROBOT_URL,
        "queue": {"size": _queue.qsize(), "max": QUEUE_MAX},
        "counters": {
            "seen": _seen,
            "enqueued": _enqueued,
            "dropped": _dropped,
            "forwarded": _forwarded,
            "errors": _errors,
        },
        "now_ms": _now_ms(),
        "last_ts_forwarded": _last_ts_forwarded,
    }


@app.get("/time")
async def server_time() -> Dict[str, int]:
    """Время сервера для clock-sync с браузером."""
    return {"server_ms": _now_ms()}


@app.get("/pong")
async def pong() -> Dict[str, Any]:
    """Последний результат ping → relay → robot round-trip."""
    if _last_pong is None:
        return {"id": -1}
    return _last_pong
