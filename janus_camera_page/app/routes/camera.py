from __future__ import annotations

import os
import subprocess
import time
from email.utils import formatdate
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.services.env_store import read_env, write_env_atomic
from app.services.system import service_restart, service_status
from app.services.v4l2 import apply_controls, is_supported, list_v4l2_ctrls, list_v4l2_modes

router = APIRouter(tags=["camera"])

_MJPEG_BOUNDARY = "frame"
CAM_ENV_PATH = Path(os.environ.get("CAM_ENV_PATH", "/etc/robot/cam-rgb.env"))
RTP_RGB_SERVICE = os.environ.get("RTP_RGB_SERVICE", "rtp-rgb@cam-rgb.service")

ADMIN_DEPENDENCY = Depends(require_admin)


class ConfigPatch(BaseModel):
    width: Optional[int] = Field(None, description="Frame width in pixels.")
    height: Optional[int] = Field(None, description="Frame height in pixels.")
    fps: Optional[int] = Field(None, description="Frames per second.")
    bitrate_kbps: Optional[int] = Field(
        None, ge=100, le=20000, description="Video bitrate in kilobits per second."
    )
    gop: Optional[int] = Field(None, ge=1, description="GOP (defaults to FPS when omitted).")
    preset: Optional[str] = Field(None, description="x264 preset.")
    tune: Optional[str] = Field(None, description="x264 tune.")
    port: Optional[int] = Field(None, ge=1024, le=65535, description="RTP UDP port (avoid changing if unsure).")

    @field_validator("preset")
    @classmethod
    def preset_ok(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}:
            raise ValueError("invalid preset")
        return value

    @field_validator("tune")
    @classmethod
    def tune_ok(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in {"zerolatency", "film", "animation"}:
            raise ValueError("invalid tune")
        return value


class ControlPatch(BaseModel):
    values: Dict[str, int | bool]


class ServiceState(BaseModel):
    service: str
    active: bool
    raw: str


class CameraEnvUpdateResult(BaseModel):
    ok: bool
    changed: bool
    requested: Tuple[int, int, int]
    effective: Tuple[int, int, int]
    normalized: Dict[str, Any]
    service: Optional[ServiceState] = None
    would_apply: Optional[Dict[str, str]] = None


class CameraMode(BaseModel):
    width: int
    height: int
    fps: List[int]


class CameraModesResponse(BaseModel):
    pixel_format: str
    device: str
    modes: List[CameraMode]


class CameraControlsResponse(BaseModel):
    device: str
    controls: Dict[str, Dict[str, Any]]


class ControlApplyResponse(BaseModel):
    ok: bool
    applied: Dict[str, int | bool]


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


def _parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _write_env_file(path: Path, data: Dict[str, str]) -> None:
    lines = [f'{key}="{value}"' for key, value in sorted(data.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def restart_rtp_rgb() -> None:
    res = subprocess.run(
        ["sudo", "systemctl", "restart", RTP_RGB_SERVICE],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to restart {RTP_RGB_SERVICE}: {res.stderr or res.stdout}")


def _mjpeg_gen(path: str):
    last_mtime = 0.0
    while True:
        try:
            stat = os.stat(path)
            if stat.st_mtime != last_mtime:
                last_mtime = stat.st_mtime
                with open(path, "rb") as handle:
                    frame = handle.read()
                yield (
                    b"--"
                    + _MJPEG_BOUNDARY.encode()
                    + b"\r\n"
                    + b"Content-Type: image/jpeg\r\n"
                    + b"Content-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
            time.sleep(0.2)
        except Exception:
            time.sleep(0.5)



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
    dependencies=[ADMIN_DEPENDENCY],
    summary="Read applied RTP/ffmpeg configuration (admin)",
    description="Loads cam-rgb.env from disk.",
)
async def get_camera_stream_config() -> CameraStreamConfig:
    env = _parse_env_file(CAM_ENV_PATH)

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
    dependencies=[ADMIN_DEPENDENCY],
    summary="Update cam-rgb.env and restart the RTP service (admin)",
    description=(
        "Overwrites `/etc/robot/cam-rgb.env` and executes `systemctl restart rtp-rgb@cam-rgb.service`."
    ),
)
async def update_camera_stream_config(cfg: CameraStreamConfig) -> CameraStreamConfig:
    env = _parse_env_file(CAM_ENV_PATH)

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
        _write_env_file(CAM_ENV_PATH, env)
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


@router.get("/admin/camera/config", include_in_schema=False, dependencies=[ADMIN_DEPENDENCY])
async def legacy_get_camera_stream_config() -> CameraStreamConfig:
    return await get_camera_stream_config()


@router.post("/admin/camera/config", include_in_schema=False, dependencies=[ADMIN_DEPENDENCY])
async def legacy_update_camera_stream_config(cfg: CameraStreamConfig) -> CameraStreamConfig:
    return await update_camera_stream_config(cfg)


@router.get("/admin/camera/modes", include_in_schema=False)
def legacy_get_camera_modes() -> Dict[str, Any]:
    return get_camera_modes()


@router.get("/snapshot.jpg", include_in_schema=False)
def legacy_get_snapshot() -> FileResponse:
    return get_snapshot()
