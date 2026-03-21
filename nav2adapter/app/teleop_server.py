"""
Separate HTTP server for teleop control (joystick/keyboard).
Runs in a dedicated thread, exposes a single POST /move/speed route
and proxies requests to Symovo PUT /v0/agv/{id}/move/speed.
"""
from __future__ import annotations

import ssl
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional

import aiohttp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Local config import (same .env as the main application)
from app.config import settings, teleop_config

_LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Create session inside the teleop event loop (this runs in the teleop thread).
    app.state.session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10),
        connector=aiohttp.TCPConnector(limit=5),
    )
    yield
    # Cleanup on shutdown
    if app.state.session is not None and not app.state.session.closed:
        await app.state.session.close()
        app.state.session = None
    _LOGGER.info("Teleop session closed")


teleop_app = FastAPI(
    title="Teleop API",
    description="Speed control channel (joystick/keyboard)",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)


class MoveSpeedBody(BaseModel):
    """MoveSpeed OpenAPI request body."""
    speed: Optional[float] = Field(default=None, ge=-2.0, le=2.0, description="Linear speed, m/s (optional; default from settings)")
    angular_speed: Optional[float] = Field(default=None, ge=-3.0, le=3.0, description="Angular speed, rad/s (optional; default from settings)")
    linear_dir: Optional[int] = Field(default=None, ge=-1, le=1, description="Linear speed direction: -1/0/1 (optional)")
    angular_dir: Optional[int] = Field(default=None, ge=-1, le=1, description="Angular speed direction: -1/0/1 (optional)")
    duration: Optional[float] = Field(default=None, ge=0.01, le=10.0, description="Duration, s (optional; default from settings)")


def _robot_url() -> str:
    base = f"https://{settings.symovo_car_ip}/v0"
    return f"{base}/agv/{settings.symovo_robot_number}/move/speed"


_SSL_CTX_CACHE: ssl.SSLContext | None = None
_SSL_CTX_NEEDS_INSECURE: bool | None = None  # tracks which mode the cache was built for
_SSL_CTX_LOCK = threading.Lock()


def _ssl_context() -> ssl.SSLContext | None:
    global _SSL_CTX_CACHE, _SSL_CTX_NEEDS_INSECURE
    needs_insecure = bool(getattr(settings, "symovo_allow_invalid_certs", True))
    with _SSL_CTX_LOCK:
        if _SSL_CTX_NEEDS_INSECURE is needs_insecure:
            return _SSL_CTX_CACHE
        if needs_insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            _SSL_CTX_CACHE = ctx
        else:
            _SSL_CTX_CACHE = None
        _SSL_CTX_NEEDS_INSECURE = needs_insecure
        return _SSL_CTX_CACHE


@teleop_app.put("/move/speed")
@teleop_app.post("/move/speed")
async def move_speed(body: MoveSpeedBody) -> dict[str, Any]:
    """Send speed command to the robot.
    Proxies to Symovo PUT /v0/agv/{id}/move/speed.

    LIMITATION: This endpoint runs in a separate thread without StateStore access.
    Coordinated busy-check is enforced in aehub.py /move/speed, not here.
    """
    url = _robot_url()
    linear_dir = 0 if body.linear_dir is None else int(max(-1, min(1, body.linear_dir)))
    angular_dir = 0 if body.angular_dir is None else int(max(-1, min(1, body.angular_dir)))
    speed = float(body.speed) if body.speed is not None else teleop_config.linear_speed * float(linear_dir)
    angular_speed = float(body.angular_speed) if body.angular_speed is not None else teleop_config.angular_speed * float(angular_dir)
    duration = float(body.duration) if body.duration is not None else teleop_config.duration
    payload = {"speed": speed, "angular_speed": angular_speed, "duration": duration}
    req_timeout = aiohttp.ClientTimeout(total=max(1.0, duration + 2.0))
    ssl_ctx = _ssl_context()
    try:
        session: aiohttp.ClientSession = teleop_app.state.session
        async with session.put(
            url,
            json=payload,
            ssl=ssl_ctx,
            timeout=req_timeout,
        ) as resp:
            if resp.status in (200, 202):
                try:
                    data = await resp.json()
                    return data if isinstance(data, dict) else {"status": "ok"}
                except Exception:
                    return {"status": "ok"}
            text = await resp.text()
            _LOGGER.warning("Teleop upstream error %s: %s", resp.status, text[:500])
            raise HTTPException(
                status_code=502,
                detail="Upstream device error",
            )
    except aiohttp.ClientError as e:
        _LOGGER.warning("Teleop move_speed request failed: %s", e)
        raise HTTPException(status_code=503, detail="Robot unreachable") from e
