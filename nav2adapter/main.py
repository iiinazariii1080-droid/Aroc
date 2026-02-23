"""
Оптимизированный главный модуль FastAPI приложения.
"""
import os
import sys
import logging
import asyncio
import platform
import threading
import time
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Ensure local imports work regardless of CWD
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Fix for Windows: aiomqtt requires SelectorEventLoop, not ProactorEventLoop
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.state import startup, shutdown
from app.config import settings
from routes.symovo_agv import router as symovo_router
from routes.aehub import router as aehub_router
from routes.health import router as health_router
from routes.robot_positions import router as robot_positions_router

HEARTBEAT_INTERVAL_S = float(os.getenv("HEALTH_HEARTBEAT_INTERVAL_S", "1.0"))


async def _heartbeat_loop(app: FastAPI) -> None:
    """Updates app.state.last_heartbeat_ts periodically (event-loop health)."""
    while True:
        app.state.last_heartbeat_ts = time.time()
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения."""
    app.state.startup_ok = False
    app.state.startup_ts = time.time()
    app.state.last_heartbeat_ts = time.time()
    app.state._heartbeat_task = asyncio.create_task(_heartbeat_loop(app))

    await startup(app)
    app.state.startup_ok = True

    # Отдельный поток: HTTP‑сервер телеуправления (джойстик/клавиатура)
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
            except BaseException:
                pass
        await shutdown(app)


# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

app = FastAPI(
    title="Symovo Microservice", 
    version="1.0.0", 
    lifespan=lifespan, 
    debug=False
)
app.include_router(symovo_router)
app.include_router(aehub_router)
app.include_router(health_router)
app.include_router(robot_positions_router)

STATIC_DIR = os.path.join(CURRENT_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/dashboard.html")
async def serve_dashboard():
    """Serve unified AGV dashboard page."""
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))

@app.get("/map_viewer.html")
async def serve_map_viewer():
    return FileResponse(os.path.join(STATIC_DIR, "map_viewer.html"))

@app.get("/robot_monitor.html")
async def serve_robot_monitor():
    """Serve robot monitor HTML page."""
    return FileResponse(os.path.join(STATIC_DIR, "robot_monitor.html"))

@app.get("/nav_console.html")
async def serve_nav_console():
    """Serve navigation console HTML page."""
    return FileResponse(os.path.join(STATIC_DIR, "nav_console.html"))


@app.get("/teleop_console.html")
async def serve_teleop_console():
    """Serve teleop (joystick/keyboard) console HTML page."""
    return FileResponse(os.path.join(STATIC_DIR, "teleop_console.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host=settings.service_host, 
        port=settings.service_port, 
        reload=False, 
        log_level=settings.log_level.lower(), 
        workers=settings.uvicorn_workers
    )
