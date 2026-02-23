"""Health endpoints: /health/live and /health/ready."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

health_router = APIRouter(tags=["Health"])


@health_router.get("/health/live")
async def health_live():
    """Liveness: process is alive."""
    return JSONResponse({"status": "ok"})


@health_router.get("/health/ready")
async def health_ready():
    """Readiness: robot is connected, not faulted, motion enabled, not busy."""
    try:
        from app.di import get_readiness_gate
        gate = get_readiness_gate()
        ready = gate.ready
        return JSONResponse({
            "ready": ready,
            "connected": gate.connected,
            "faulted": gate.faulted,
            "motion_enabled": gate.motion_enabled,
            "busy": gate.busy,
        })
    except Exception as e:
        return JSONResponse(
            {"ready": False, "error": str(e)},
            status_code=503,
        )
