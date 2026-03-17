"""Igus Microservice — FastAPI entry point.

Composes middleware, exception handlers, routers and static files.
Business logic lives in ``app/`` subpackages.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api_routes import router as api_v1_router
from app.exception_handlers import register_exception_handlers
from app.metrics import MetricsRegistry
from app.middleware import request_id_middleware
from app.request_context import RequestIdFilter
from app.routes import router as igus_router
from app.state import shutdown, startup
from app.system_routes import STATIC_DIR, router as system_router
from app.version import SERVER_VERSION


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup(app)
    try:
        yield
    finally:
        await shutdown(app)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] [rid=%(request_id)s] %(message)s",
)
for handler in logging.getLogger().handlers:
    handler.addFilter(RequestIdFilter())
if log_level == "DEBUG" or os.getenv("DRYVE_MODBUS_LOG", "").lower() in (
    "1",
    "true",
    "yes",
):
    logging.getLogger("dryve_d1.modbus").setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Igus Microservice",
    version=SERVER_VERSION,
    lifespan=lifespan,
    debug=False,
)
metrics = MetricsRegistry()
app.state.metrics = metrics


@app.middleware("http")
async def _middleware(request: Request, call_next):
    return await request_id_middleware(request, call_next, metrics=metrics)


register_exception_handlers(app, metrics=metrics)

app.include_router(system_router)
app.include_router(igus_router)
app.include_router(api_v1_router)

# Serve static files for control panel.
# Static HTML/JS/CSS are NOT behind auth. The control panel UI itself
# is non-sensitive; all motion commands it issues go through the protected API
# endpoints which require X-API-Key when IGUS_API_KEY is set.
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVICE_HOST", "127.0.0.1")
    port = int(os.getenv("SERVICE_PORT", "8101"))
    uvicorn.run("main:app", host=host, port=port, reload=False, log_level=log_level.lower())


