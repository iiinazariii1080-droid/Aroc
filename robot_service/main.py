import os
import sys
import logging
from fastapi import FastAPI, HTTPException, Request
import time
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse 
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from app.state import startup, shutdown
from routes.robot import router as robot_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup(app)
    try:
        yield
    finally:
        await shutdown(app)

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def _skip_joystick_frame_access(record: logging.LogRecord) -> bool:
    """Drop uvicorn access log for POST /joystick/frame to keep streaming path quiet."""
    a = getattr(record, "args", None)
    if not a or len(a) < 3:
        return True
    method, path = a[1], a[2]
    if method == "POST" and (path == "/joystick/frame" or path.startswith("/joystick/frame?")):
        return False
    return True


logging.getLogger("uvicorn.access").addFilter(_skip_joystick_frame_access)

app = FastAPI(title="Robot Microservice", version="1.0.0", lifespan=lifespan, debug=False)
app.include_router(robot_router)

LAST_CALL_TS = 0
MIN_INTERVAL = 1 / 60  # 20Hz = 0.05 s

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    global LAST_CALL_TS
    now = time.time()
    if now - LAST_CALL_TS < MIN_INTERVAL and request.url.path.startswith("/api/v1/robot/"):
        return JSONResponse(
            status_code=429,
            content={"success": False, "error": "Rate limit 20Hz exceeded"}
        )
    LAST_CALL_TS = now
    return await call_next(request)

@app.get("/healthz", tags=["Meta"])
async def healthz():
    return JSONResponse({"status": "ok"})

@app.get("/readyz", tags=["Meta"])
async def readyz():
    try:
        return JSONResponse({"ready": True})
    except Exception as e:
        return JSONResponse({"ready": False, "error": str(e)}, status_code=503)


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("SERVICE_PORT", "8110"))
    uvicorn.run("main:app", host=host, port=port, reload=False, log_level=log_level.lower())
