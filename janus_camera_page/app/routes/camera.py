from __future__ import annotations

import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.middleware.rate_limit import require_admin_rate_limit
from app.services.env_store import read_env, write_env_atomic
from app.services.system import run as run_cmd
from app.services.v4l2 import list_v4l2_modes

router = APIRouter(tags=["camera"])

RTP_RGB_SERVICE = os.environ.get("RTP_RGB_SERVICE", "rtp-rgb@cam-rgb.service")

ADMIN_DEPENDENCY = Depends(require_admin)
ADMIN_RATE_LIMIT = Depends(require_admin_rate_limit)


class CameraMode(BaseModel):
    width: int
    height: int
    fps: List[int]


class CameraModesResponse(BaseModel):
    pixel_format: str
    device: str
    modes: List[CameraMode]



class CameraStreamConfig(BaseModel):
    width: int = Field(..., description="Video width in pixels, e.g. 640.")
    height: int = Field(..., description="Video height in pixels, e.g. 480.")
    fps: int = Field(..., description="Frames per second, e.g. 30.")

    bitrate_kbps: int = Field(1800, ge=100, description="H.264 bitrate in kbps.")
    gop: Optional[int] = Field(
        None,
        description="Keyframe interval (GOP). Defaults to FPS when omitted.",
    )
    preset: str = Field("veryfast", description="x264 preset, e.g. veryfast.")
    tune: str = Field("zerolatency", description="x264 tune, typically zerolatency.")

    snapshot_fps: int = Field(1, ge=0, description="JPEG snapshot cadence in FPS.")
    port: int = Field(5004, ge=1024, le=65535, description="RTP UDP port consumed by Janus.")




def restart_rtp_rgb() -> None:
    run_cmd(["sudo", "systemctl", "restart", RTP_RGB_SERVICE], timeout=60)



@router.get(
    "/modes",
    response_model=CameraModesResponse,
    summary="Available camera modes (V4L2)",
    description="Parses `v4l2-ctl --list-formats-ext` and returns supported YUYV resolutions/FPS combinations.",
)
def get_camera_modes() -> CameraModesResponse:
    raw = list_v4l2_modes()
    modes = [CameraMode(**mode) for mode in raw.get("modes", [])]
    return CameraModesResponse(
        pixel_format=raw.get("pixel_format", "YUYV"),
        device=raw.get("device", get_settings().camera_device),
        modes=modes,
    )

@router.get(
    "/config",
    response_model=CameraStreamConfig,
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Read applied RTP/ffmpeg configuration (admin)",
    description="Loads cam-rgb.env from disk.",
)
async def get_camera_stream_config() -> CameraStreamConfig:
    env = read_env()

    width = int(env.get("WIDTH", "640"))
    height = int(env.get("HEIGHT", "480"))
    fps = int(env.get("FPS", "30"))
    bitrate_kbps = int(env.get("BITRATE_KBPS", "1800"))
    preset = env.get("PRESET", "veryfast")
    tune = env.get("TUNE", "zerolatency")
    snapshot_fps = int(env.get("SNAPSHOT_FPS", "1"))
    port = int(env.get("PORT", "5004"))
    gop_env = env.get("GOP")
    gop = int(gop_env) if gop_env is not None else None

    return CameraStreamConfig(
        width=width,
        height=height,
        fps=fps,
        bitrate_kbps=bitrate_kbps,
        gop=gop,
        preset=preset,
        tune=tune,
        snapshot_fps=snapshot_fps,
        port=port,
    )


@router.post(
    "/config",
    response_model=CameraStreamConfig,
    dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT],
    summary="Update cam-rgb.env and restart the RTP service (admin)",
    description=(
        "Overwrites `/etc/robot/cam-rgb.env` and executes `systemctl restart rtp-rgb@cam-rgb.service`."
    ),
)
async def update_camera_stream_config(cfg: CameraStreamConfig) -> CameraStreamConfig:
    env = read_env()

    env["WIDTH"] = str(cfg.width)
    env["HEIGHT"] = str(cfg.height)
    env["FPS"] = str(cfg.fps)
    env["BITRATE_KBPS"] = str(cfg.bitrate_kbps)
    env["PRESET"] = cfg.preset
    env["TUNE"] = cfg.tune
    env["SNAPSHOT_FPS"] = str(cfg.snapshot_fps)
    env["PORT"] = str(cfg.port)

    if cfg.gop is not None:
        env["GOP"] = str(cfg.gop)
    else:
        env.pop("GOP", None)

    try:
        write_env_atomic(env)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write env: {exc}") from exc

    try:
        restart_rtp_rgb()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return cfg


@router.get(
    "/snapshot.jpg",
    summary="Latest JPEG snapshot",
    description="Returns the most recent still frame. HTTP clients should not cache the response beyond a single request.",
)
def get_snapshot() -> FileResponse:
    path = get_settings().snapshot_path
    if not os.path.exists(path):
        raise HTTPException(status_code=503, detail="snapshot not available")
    return FileResponse(
        path,
        media_type="image/jpeg",
        filename="snapshot.jpg",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Disposition": "inline; filename=snapshot.jpg",
            "X-Accel-Buffering": "no",
        },
    )




