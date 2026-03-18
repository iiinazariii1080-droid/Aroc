"""
Main FastAPI application module.
"""
import os
import sys
import logging
import asyncio
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Ensure local imports work regardless of CWD
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from app.state import startup, shutdown
from app.config import settings
from app.middleware import RequestIdMiddleware
from services.logging_config import configure_logging
from services.log_utils import install_command_id_filter, install_request_id_filter
from routes.symovo_agv import router as symovo_router
from routes.aehub import router as aehub_router
from routes.health import router as health_router

HEARTBEAT_INTERVAL_S = float(os.getenv("HEALTH_HEARTBEAT_INTERVAL_S", "1.0"))


async def _heartbeat_loop(app: FastAPI) -> None:
    """Updates app.state.last_heartbeat_ts periodically (event-loop health)."""
    while True:
        app.state.last_heartbeat_ts = time.time()
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    app.state.startup_ok = False
    app.state.startup_ts = time.time()
    app.state.last_heartbeat_ts = time.time()

    await startup(app)

    # Start heartbeat AFTER startup succeeds to avoid task leak on failure.
    app.state._heartbeat_task = asyncio.create_task(_heartbeat_loop(app))
    app.state.startup_ok = True

    # Separate thread: HTTP server for teleop (joystick/keyboard)
    if getattr(settings, "teleop_enabled", True):
        def _run_teleop() -> None:
            try:
                import uvicorn
                from app.teleop_server import teleop_app
                uvicorn.run(
                    teleop_app,
                    host=settings.teleop_host,
                    port=settings.teleop_port,
                    log_level="warning",
                )
            except (OSError, SystemExit) as exc:
                logging.getLogger(__name__).warning(
                    "Teleop server failed to start: %s", exc,
                )

        _th = threading.Thread(target=_run_teleop, daemon=True)
        _th.start()
        logging.getLogger(__name__).info(
            "Teleop server thread started on http://%s:%s (PUT/POST /move/speed)",
            settings.teleop_host,
            settings.teleop_port,
        )

    logging.getLogger(__name__).info(
        "Main API listening on http://%s:%s (GET /healthz, /livez, /readyz, /api/v1/..., /drive_mode, etc.)",
        settings.service_host,
        settings.service_port,
    )
    try:
        yield
    finally:
        # Stop heartbeat first
        hb = getattr(app.state, "_heartbeat_task", None)
        if hb:
            hb.cancel()
            try:
                await hb
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await shutdown(app)


# Structured logging (JSON when LOG_FORMAT=json, text otherwise).
# Install log filters FIRST so early uvicorn logs don't crash.
install_command_id_filter()
install_request_id_filter()
_log_format = os.getenv("LOG_FORMAT", "text")
configure_logging(level=settings.log_level, fmt=_log_format)

app = FastAPI(
    title="Symovo Microservice",
    version="1.0.0",
    lifespan=lifespan,
    debug=False
)

app.add_middleware(RequestIdMiddleware)

app.include_router(symovo_router)
app.include_router(aehub_router)
app.include_router(health_router)


STATIC_DIR = os.path.join(CURRENT_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_PAGES = ["dashboard", "map_viewer"]
for _page in _PAGES:
    def _make_handler(name):
        async def handler():
            p = os.path.join(STATIC_DIR, f"{name}.html")
            if not os.path.isfile(p):
                raise HTTPException(status_code=404, detail=f"{name}.html not found")
            return FileResponse(p)
        handler.__name__ = f"serve_{name}"
        return handler
    app.get(f"/{_page}.html")(_make_handler(_page))

if __name__ == "__main__":
    import uvicorn
    if settings.uvicorn_workers != 1:
        raise SystemExit(
            f"UVICORN_WORKERS must be 1 (got {settings.uvicorn_workers}). "
            "This app uses in-memory state and cannot run with multiple workers."
        )
    uvicorn.run(
        "main:app",
        host=settings.service_host,
        port=settings.service_port,
        reload=False,
        log_level=settings.log_level.lower(),
        workers=settings.uvicorn_workers
    )
