"""Core system routes: health probes, status, relay proxy, service restart."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.admin import require_admin
from app.core.dependencies import require_api_key
from app.core.settings import get_settings
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import janus
from app.services import relay_proxy
from app.services.system import service_restart, systemd_brief

router = APIRouter(tags=["system"])
# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type


# ── Response models ──

class HealthResponse(BaseModel):
    ok: bool = Field(..., description="All critical subsystems are healthy.")
    mode: str = Field("nominal", description="Current system operating mode.")
    janus_reachable: bool = Field(True, description="Janus REST API responds.")
    stream_active: bool = Field(True, description="Primary mountpoint has recent video.")
    details: Dict[str, Any] = Field(default_factory=dict, description="Per-check breakdown.")


class ActionResponse(BaseModel):
    ok: bool
    message: str


# ── Health probes ──

@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Service health probe (deep check)",
)
def healthz() -> HealthResponse:
    from app.services import system_mode as smode

    settings = get_settings()
    details: Dict[str, Any] = {}

    janus_ok = False
    stream_ok = False
    try:
        summary = janus.janus_summary(settings.janus_mount_id)
        janus_ok = True
        age = summary.get("video_age_ms")
        stream_ok = age is not None and isinstance(age, (int, float)) and age <= settings.watchdog_stale_ms
        details["video_age_ms"] = age
        details["mountpoint_id"] = summary.get("mountpoint_id")
    except Exception as exc:
        details["janus_error"] = str(exc)

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


@router.get("/health/stream", summary="End-to-end stream health (media-level)")
def health_stream() -> JSONResponse:
    from app.services import system_mode as smode

    settings = get_settings()
    checks: Dict[str, Any] = {}

    # 1. RTP ingest freshness
    rtp_fresh = False
    try:
        summary = janus.janus_summary(settings.janus_mount_id)
        video_age = summary.get("video_age_ms")
        rtp_fresh = (
            video_age is not None
            and isinstance(video_age, (int, float))
            and video_age <= settings.watchdog_stale_ms
        )
        checks["rtp_ingest"] = {"ok": rtp_fresh, "video_age_ms": video_age, "threshold_ms": settings.watchdog_stale_ms}
    except Exception as exc:
        checks["rtp_ingest"] = {"ok": False, "error": str(exc)}

    # 2. Client telemetry
    client_reporting = False
    try:
        from prometheus_client import REGISTRY
        frames = REGISTRY.get_sample_value("camstack_client_frames_decoded_total") or 0
        loss = REGISTRY.get_sample_value("camstack_client_packet_loss_ratio") or 0
        client_reporting = frames > 0
        checks["client_telemetry"] = {
            "ok": client_reporting,
            "frames_decoded": frames,
            "packet_loss_ratio": round(loss, 4) if loss else 0,
            "note": "no client connected yet" if not client_reporting else None,
        }
    except Exception as exc:
        logging.warning("client telemetry metrics unavailable: %s", exc)
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
    except Exception as exc:
        logging.warning("recovery ladder status unavailable: %s", exc)
        checks["recovery_ladder"] = {"ok": True, "level": 0}

    # 5. TURN server reachability (TCP connect probe)
    try:
        import socket
        turn_host = settings.turn_host if hasattr(settings, "turn_host") else None
        turn_port = settings.turn_port if hasattr(settings, "turn_port") else 3478
        if turn_host:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            try:
                sock.connect((turn_host, int(turn_port)))
                checks["turn_server"] = {"ok": True, "host": turn_host, "port": turn_port}
            except (socket.timeout, OSError) as exc:
                checks["turn_server"] = {"ok": False, "host": turn_host, "port": turn_port, "error": str(exc)}
            finally:
                sock.close()
        else:
            checks["turn_server"] = {"ok": True, "note": "no TURN configured"}
    except Exception as exc:
        checks["turn_server"] = {"ok": False, "error": str(exc)}

    stream_usable = rtp_fresh and mode_ok
    return JSONResponse(
        status_code=200 if stream_usable else 503,
        content={"stream_usable": stream_usable, "checks": checks},
    )


# ── Full system status ──

@router.get(f"/api/v1/{_CAM_TYPE}/status", summary="Full system status snapshot", dependencies=[Depends(require_admin), Depends(require_admin_rate_limit)])
@router.get("/status", summary="Full system status snapshot", dependencies=[Depends(require_admin), Depends(require_admin_rate_limit)])
def system_status() -> JSONResponse:
    import time as _time
    from app.services import system_mode as smode
    from app.services.recovery_ladder import get_ladder

    settings = get_settings()

    health_data: Dict[str, Any] = {}
    try:
        h = healthz()
        health_data = {"ok": h.ok, "mode": h.mode, "janus_reachable": h.janus_reachable, "stream_active": h.stream_active}
    except Exception as exc:
        health_data = {"ok": False, "error": str(exc)}

    ladder_data: Dict[str, Any] = {}
    try:
        ladder = get_ladder()
        ladder_data = ladder.status() if ladder else {"current_level": 0}
    except Exception:
        logging.debug("recovery ladder status unavailable", exc_info=True)
        ladder_data = {"current_level": 0}

    svc: Dict[str, Any] = {}
    try:
        svc = systemd_brief(settings.service_name)
    except Exception:
        logging.debug("systemd_brief unavailable", exc_info=True)
        svc = {"active": False, "since": None, "restarts": 0}

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
        "timestamp": _time.time(),
        "camera_type": settings.camera_type,
        "health": health_data,
        "mode": smode.mode_info(),
        "recovery_ladder": ladder_data,
        "service": svc,
        "settings": cfg,
    })


# ── Relay proxy ──

@router.get(f"/api/v1/{_CAM_TYPE}/relay/time", include_in_schema=False)
@router.get("/relay/time", summary="Relay server time for clock-sync")
async def relay_time():
    try:
        return await relay_proxy.relay_get("time")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e


@router.get(f"/api/v1/{_CAM_TYPE}/relay/pong", include_in_schema=False)
@router.get("/relay/pong", summary="Last joystick ping/pong result")
async def relay_pong():
    try:
        return await relay_proxy.relay_get("pong")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e


# ── Service restart ──

@router.post(
    "/action/restart",
    response_model=ActionResponse,
    dependencies=[Depends(require_api_key)],
    summary="Restart the camera systemd service",
)
def restart_service() -> ActionResponse:
    service_restart()
    return ActionResponse(ok=True, message="service restarted")


# ── API root + favicon ──

@router.get(
    f"/api/v1/{_CAM_TYPE}",
    response_model=HealthResponse,
    summary="Camera API root",
)
def camera_api_root() -> HealthResponse:
    return HealthResponse(ok=True)


@router.get(f"/api/v1/{_CAM_TYPE}/favicon.ico", include_in_schema=False)
@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    settings = get_settings()
    path = Path(settings.static_dir) / "favicon.ico"
    try:
        if path.is_file():
            return FileResponse(str(path), headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        logging.warning("favicon not accessible: %s", exc)
    return Response(status_code=204)
