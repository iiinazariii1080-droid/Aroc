from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.dependencies import require_api_key
from app.core.settings import get_settings
from app.services import janus
from app.services import relay_proxy
from app.services.system import service_restart, systemd_brief

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    ok: bool = Field(..., description="All critical subsystems are healthy.")
    mode: str = Field("nominal", description="Current system operating mode.")
    janus_reachable: bool = Field(True, description="Janus REST API responds.")
    stream_active: bool = Field(True, description="Primary mountpoint has recent video.")
    details: Dict[str, Any] = Field(default_factory=dict, description="Per-check breakdown.")

class CameraInfo(BaseModel):
    device: str
    pixel_format: str

class RequestedConfig(BaseModel):
    width: int
    height: int
    fps: int

class EffectiveVideoState(BaseModel):
    width: Optional[int]
    height: Optional[int]
    fps: Optional[int]
    pixfmt: Optional[str]

class FfmpegConfig(BaseModel):
    BITRATE_KBPS: int
    GOP: int
    PRESET: str
    TUNE: str
    PORT: int

class ServiceBrief(BaseModel):
    active: bool
    since: Optional[str]
    restarts: int

class JanusStatus(BaseModel):
    mountpoint_id: Optional[int] = None
    enabled: Optional[bool] = None
    video_active: Optional[bool] = None
    video_age_ms: Optional[int] = None
    codec: Optional[str] = None
    pt: Optional[int] = None
    fmtp: Optional[str] = None
    error: Optional[str] = None

class SystemStatusResponse(BaseModel):
    camera: CameraInfo
    requested: RequestedConfig
    effective: EffectiveVideoState
    ffmpeg: FfmpegConfig
    service: ServiceBrief
    janus: JanusStatus
    last_applied_at: Optional[float]

class ActionResponse(BaseModel):
    ok: bool
    message: str

@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Service health probe (deep check)",
    description="Checks Janus connectivity, stream freshness, and system mode. "
    "Returns ok=false when critical subsystems are degraded.",
)
async def healthz() -> HealthResponse:
    from app.services import system_mode as smode

    settings = get_settings()
    details: Dict[str, Any] = {}

    # Check 1: Janus REST API reachable + mountpoint has fresh video
    janus_ok = False
    stream_ok = False
    try:
        summary = await janus.janus_summary(settings.janus_mount_id)
        janus_ok = summary.get("reachable", False)
        age = summary.get("video_age_ms")
        stream_ok = janus.is_stream_fresh(age, settings.watchdog_stale_ms)
        details["video_age_ms"] = age
        details["mountpoint_id"] = summary.get("mountpoint_id")
    except Exception as exc:
        details["janus_error"] = str(exc)

    # Check 2: System mode
    mode = smode.current_mode()
    details["mode_info"] = smode.mode_info()

    overall = janus_ok and stream_ok and mode != smode.SystemMode.SAFE

    return HealthResponse(
        ok=overall,
        mode=mode.value,
        janus_reachable=janus_ok,
        stream_active=stream_ok,
        details=details,
    )


@router.get(
    "/health/stream",
    summary="End-to-end stream health (media-level)",
    description="Checks whether video is actually being decoded by at least one "
    "connected client. Combines Janus mount freshness, client telemetry "
    "recency, and system mode into a single verdict.",
)
async def health_stream() -> JSONResponse:
    """Media-level health: not just 'process alive' but 'stream usable'."""
    from app.services import system_mode as smode

    settings = get_settings()
    checks: Dict[str, Any] = {}

    # 1. Janus mount: is RTP arriving?
    janus_ok = False
    video_age = None
    try:
        summary = await janus.janus_summary(settings.janus_mount_id)
        janus_ok = summary.get("reachable", False)
        video_age = summary.get("video_age_ms")
        rtp_fresh = janus.is_stream_fresh(video_age, settings.watchdog_stale_ms)
        checks["rtp_ingest"] = {
            "ok": rtp_fresh,
            "video_age_ms": video_age,
            "threshold_ms": settings.watchdog_stale_ms,
        }
    except Exception as exc:
        checks["rtp_ingest"] = {"ok": False, "error": str(exc)}
        rtp_fresh = False

    # 2. Client telemetry: has any browser reported stats recently?
    client_reporting = False
    try:
        from app.metrics import get_client_state

        state = get_client_state()
        frames = state["frames_decoded"]
        loss = state["packet_loss_ratio"]
        # Consider client healthy if we got a report AND frames are being decoded
        client_reporting = frames > 0
        checks["client_telemetry"] = {
            "ok": client_reporting,
            "frames_decoded": frames,
            "packet_loss_ratio": round(loss, 4) if loss else 0,
            "note": "no client connected yet" if not client_reporting else None,
        }
    except Exception:
        checks["client_telemetry"] = {"ok": False, "note": "metrics unavailable"}

    # 3. System mode
    mode = smode.current_mode()
    mode_ok = mode not in (smode.SystemMode.SAFE,)
    checks["system_mode"] = {"ok": mode_ok, "mode": mode.value}

    # 4. Recovery ladder
    try:
        from app.services.recovery_ladder import get_ladder

        ladder = get_ladder()
        level = ladder.status()["current_level"] if ladder else 0
        checks["recovery_ladder"] = {"ok": level <= 2, "level": level}
    except Exception:
        checks["recovery_ladder"] = {"ok": True, "level": 0}

    # Verdict: stream is E2E usable when RTP is fresh AND system isn't in SAFE mode.
    # Client telemetry is informational (no clients ≠ broken stream).
    stream_usable = rtp_fresh and mode_ok

    failure_reasons = [name for name, chk in checks.items() if not chk.get("ok", True)]

    return JSONResponse(
        status_code=200 if stream_usable else 503,
        content={
            "stream_usable": stream_usable,
            "failure_reason": failure_reasons if failure_reasons else None,
            "checks": checks,
        },
    )


# ── Full system status snapshot ──

@router.get(
    "/status",
    summary="Full system status snapshot",
    description="Combines health, mode, recovery ladder, settings, and uptime "
    "into a single diagnostic payload for operators and dashboards.",
)
async def system_status() -> JSONResponse:
    import time

    from app.services import system_mode as smode
    from app.services.recovery_ladder import get_ladder

    settings = get_settings()

    # Health
    health_data: Dict[str, Any] = {}
    try:
        h = await healthz()
        health_data = {
            "ok": h.ok,
            "mode": h.mode,
            "janus_reachable": h.janus_reachable,
            "stream_active": h.stream_active,
        }
    except Exception as exc:
        health_data = {"ok": False, "error": str(exc)}

    # Recovery ladder
    ladder_data: Dict[str, Any] = {}
    try:
        ladder = get_ladder()
        ladder_data = ladder.status() if ladder else {"current_level": 0}
    except Exception:
        ladder_data = {"current_level": 0}

    # System mode
    mode_data = smode.mode_info()

    # Service brief — subprocess call, run in executor to avoid blocking event loop
    svc: Dict[str, Any] = {}
    try:
        import asyncio as _aio
        svc = await _aio.to_thread(systemd_brief, settings.service_name)
    except Exception:
        svc = {"active": False, "since": None, "restarts": 0}

    # Settings snapshot (non-secret)
    cfg = {
        "camera_type": settings.camera_type,
        "janus_mount_id": settings.janus_mount_id,
        "watchdog_enabled": settings.watchdog_enabled,
        "snapshot_watchdog_enabled": settings.snapshot_watchdog_enabled,
        "watchdog_interval_sec": settings.watchdog_interval_sec,
        "watchdog_stale_ms": settings.watchdog_stale_ms,
        "ice_policy": settings.ice_policy,
    }

    return JSONResponse(content={
        "timestamp": time.time(),
        "camera_type": settings.camera_type,
        "health": health_data,
        "mode": mode_data,
        "recovery_ladder": ladder_data,
        "service": svc,
        "settings": cfg,
    })


# ── Relay proxy: joystick e2e latency measurement ──

@router.get("/relay/time", summary="Relay server time for clock-sync")
async def relay_time():
    try:
        return await relay_proxy.relay_get("time")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e


@router.get("/relay/pong", summary="Last joystick ping/pong result")
async def relay_pong():
    try:
        return await relay_proxy.relay_get("pong")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e

@router.post(
    "/action/restart",
    response_model=ActionResponse,
    dependencies=[Depends(require_api_key)],
    summary="Restart the camera systemd service",
    description="Requires `X-API-Key`. Runs `systemctl restart` for the configured unit.",
)
async def restart_service() -> ActionResponse:
    import asyncio
    await asyncio.to_thread(service_restart)
    return ActionResponse(ok=True, message="service restarted")


def camera_api_root() -> HealthResponse:
    """API root for gateway prefix discovery (/api/v1/{camera_type}).

    Registered dynamically in app/routes/__init__.py using the runtime camera
    type so the path is not frozen at module import time.

    Returns ok=False when the system is in SAFE mode so upstream gateways
    can detect a degraded backend and route accordingly.
    """
    from app.services import system_mode as smode
    mode = smode.current_mode()
    return HealthResponse(
        ok=mode != smode.SystemMode.SAFE,
        mode=mode.value,
    )

