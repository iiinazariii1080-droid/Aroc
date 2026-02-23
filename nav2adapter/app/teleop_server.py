"""
Отдельный HTTP‑сервер для телеуправления (джойстик/клавиатура).
Запускается в отдельном потоке, отдаёт один маршрут POST /move/speed
и проксирует запросы на Symovo PUT /v0/agv/{id}/move/speed.
"""
from __future__ import annotations

import ssl
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

import aiohttp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Локальный импорт конфига (тот же .env, что и основное приложение)
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# ── Shared session (created once, reused across requests) ──────────
_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    """Lazily create and return the shared aiohttp session."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )
    return _session


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    # Cleanup on shutdown
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None
    _LOGGER.info("Teleop session closed")


teleop_app = FastAPI(
    title="Teleop API",
    description="Канал управления по скорости (джойстик/клавиатура)",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)


class MoveSpeedBody(BaseModel):
    """Тело запроса по OpenAPI MoveSpeed."""
    speed: Optional[float] = Field(default=None, description="Линейная скорость, м/с (опционально; default из настроек)")
    angular_speed: Optional[float] = Field(default=None, description="Угловая скорость, рад/с (опционально; default из настроек)")
    linear_dir: Optional[int] = Field(default=None, description="Направление линейной скорости: -1/0/1 (опционально)")
    angular_dir: Optional[int] = Field(default=None, description="Направление угловой скорости: -1/0/1 (опционально)")
    duration: Optional[float] = Field(default=None, description="Длительность, с (опционально; default из настроек)")


def _robot_url() -> str:
    base = f"https://{settings.symovo_car_ip}/v0"
    return f"{base}/agv/{settings.symovo_robot_number}/move/speed"


def _ssl_context() -> ssl.SSLContext | None:
    if getattr(settings, "symovo_allow_invalid_certs", True):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


@teleop_app.put("/move/speed")
@teleop_app.post("/move/speed")
async def move_speed(body: MoveSpeedBody) -> dict[str, Any]:
    """
    Отправить команду скорости на машину.
    Проксирует на Symovo PUT /v0/agv/{id}/move/speed.
    """
    url = _robot_url()
    linear_dir = 0 if body.linear_dir is None else int(max(-1, min(1, body.linear_dir)))
    angular_dir = 0 if body.angular_dir is None else int(max(-1, min(1, body.angular_dir)))
    speed = float(body.speed) if body.speed is not None else float(settings.teleop_default_linear_speed) * float(linear_dir)
    angular_speed = float(body.angular_speed) if body.angular_speed is not None else float(settings.teleop_default_angular_speed) * float(angular_dir)
    duration = float(body.duration) if body.duration is not None else float(settings.teleop_default_duration)
    payload = {"speed": speed, "angular_speed": angular_speed, "duration": duration}
    req_timeout = aiohttp.ClientTimeout(total=max(1.0, duration + 2.0))
    ssl_ctx = _ssl_context()
    try:
        session = await _get_session()
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
            raise HTTPException(
                status_code=502,
                detail=f"Robot returned {resp.status}: {text[:500]}",
            )
    except aiohttp.ClientError as e:
        _LOGGER.warning("Teleop move_speed request failed: %s", e)
        raise HTTPException(status_code=503, detail=f"Robot unreachable: {e}") from e
