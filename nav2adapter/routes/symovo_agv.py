"""
Optimized routes for Symovo AGV.
"""
from typing import Any
import logging
import math
from fastapi import APIRouter, HTTPException, status, Path, Depends, Query
from fastapi.responses import Response

_logger = logging.getLogger(__name__)

from routes.decorators import safe_getter
from services.symovo_service import SymovoAgvClient, normalize_symovo_status
from models.api_types import ErrorStatus
from app.dependencies import SymovoClient, InjectedStateStore, InjectedSafetyTracker
from app.config import settings
from exceptions import DeviceConnectionError
from models.api_types import (
    SymovoStatusResponse,
    GenericResponse,
    GoToPoseRequest,
    ApiError,
)


router = APIRouter(
    tags=["AE.01 (Symovo AGV)"],
    responses={
        202: {"model": ApiError, "description": "Accepted but not finished / Device busy / Conflict"},
        404: {"model": ApiError, "description": "Input not found or invalid"},
        409: {"model": ApiError, "description": "Device error or not ready"},
        422: {"model": ApiError, "description": "Robot command validation error"},
        503: {"model": ApiError, "description": "Device connection error / Service unavailable"},
        500: {"model": ApiError, "description": "Internal server error"},
    },
)

@router.post(
    "/fault_reset",
    response_model=GenericResponse,
    summary="Clear all transports",
    description=(
        "Clears all transports from the AGV.\n\n"
        "On error, returns standard FastAPI error envelope with detail.error description."
    ),
    response_description="Clear all transports",
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def clear_all_transports(client: SymovoClient) -> Any:
    """Clear all transport tasks."""
    result = await client.clear_all_transports()
    if isinstance(result, list):
        return {"status": "ok", "deleted": len(result)}
    raise HTTPException(status_code=500, detail={"error": "Unexpected clear all transports payload"})

@router.get(
    "/pose",
    response_model=GenericResponse,
    summary="AGV pose",
    description=(
        "Returns normalized pose for the AGV with unified fields (pose, velocity, state, battery, etc.).\n\n"
        "On error, returns standard FastAPI error envelope with detail.error description."
    ),
    response_description="AGV pose",
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def get_pose(client: SymovoClient, store: InjectedStateStore) -> Any:
    """Get current AGV pose (from background cache, fallback to direct request)."""
    # Try background cache first
    raw = await store.get_last_raw_pose()
    age = await store.get_last_raw_pose_age_s()
    max_age = float(settings.cache_max_age_s)

    if raw is None or age > max_age:
        _logger.debug("Pose cache miss (raw=%s, age=%.1fs, max=%.1fs) — falling back to direct request", raw is not None, age, max_age)
        raw = await client.pose()
        if isinstance(raw, dict):
            await store.set_last_raw_pose(raw)

    if isinstance(raw, dict):
        normalized = normalize_symovo_status(raw)
        if isinstance(normalized, ErrorStatus):
            raise HTTPException(status_code=502, detail=normalized.model_dump())
        return normalized.model_dump()
    raise HTTPException(status_code=500, detail={"error": "Unexpected pose payload"})


@router.get(
    "/status",
    response_model=GenericResponse,
    summary="AGV status",
    description=(
        "Returns normalized status for the AGV with unified fields (pose, velocity, state, battery, etc.).\n\n"
        "On error, returns standard FastAPI error envelope with detail.error description."
    ),
    response_description="Normalized AGV status",
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def get_status(client: SymovoClient, store: InjectedStateStore) -> Any:
    """Get AGV status (from background cache, fallback to direct request)."""
    # Try background cache first
    raw = await store.get_last_raw_status()
    age = await store.get_last_raw_status_age_s()
    max_age = float(settings.cache_max_age_s)

    if raw is None or age > max_age:
        _logger.debug("Status cache miss (raw=%s, age=%.1fs, max=%.1fs) — falling back to direct request", raw is not None, age, max_age)
        raw = await client.status()
        if isinstance(raw, dict):
            await store.set_last_raw_status(raw)

    if isinstance(raw, dict):
        normalized = normalize_symovo_status(raw)
        if isinstance(normalized, ErrorStatus):
            raise HTTPException(status_code=502, detail=normalized.model_dump())
        return normalized.model_dump()
    raise HTTPException(status_code=500, detail={"error": "Unexpected status payload"})


@router.put(
    "/drive_mode",
    response_model=GenericResponse,
    summary="Set drive mode (enable motors)",
    description="Calls Symovo controller endpoint to set drive mode / enable motors (drive_ready).",
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def set_drive_mode(client: SymovoClient, enable: bool = True) -> Any:
    try:
        result = await client.set_drive_mode(enable=enable)
    except DeviceConnectionError as e:
        # 503 from controller means robot is physically not ready
        # (waiting_for_scanner, drive_ready=false, charging, etc.)
        # Enrich the error with state_flags if available.
        detail = str(e)
        try:
            raw = await client.status_uncached()
            flags = raw.get("state_flags", {}) if isinstance(raw, dict) else {}
            if flags:
                reasons = [k for k, v in flags.items() if v and k in (
                    "waiting_for_scanner", "laser_timeout", "emergency_stop_reset_request",
                    "drive_manual", "safety_relais_reset_request",
                )]
                if not flags.get("drive_ready", True):
                    reasons.insert(0, "drive_ready=false")
                if reasons:
                    detail = f"Robot not ready: {', '.join(reasons)}"
        except Exception:
            pass
        raise DeviceConnectionError(detail)
    return {"status": "ok", "result": result}


@router.put(
    "/pause/stop",
    response_model=GenericResponse,
    summary="Pause stop (unpause)",
    description="Calls Symovo controller endpoint to stop pause state (if supported).",
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def pause_stop(client: SymovoClient) -> Any:
    result = await client.pause_stop()
    return {"status": "ok", "result": result}


@router.put(
    "/pause/start",
    response_model=GenericResponse,
    summary="Pause start",
    description="Calls Symovo controller endpoint to start pause state (if supported).",
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def pause_start(client: SymovoClient) -> Any:
    result = await client.pause_start()
    return {"status": "ok", "result": result}


@router.put(
    "/safety/reset_emergency_stop",
    response_model=GenericResponse,
    summary="Reset emergency stop",
    description="Calls Symovo controller endpoint to reset emergency stop (if supported).",
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def reset_emergency_stop(client: SymovoClient) -> Any:
    result = await client.reset_emergency_stop()
    return {"status": "ok", "result": result}


@router.put(
    "/safety/reset_software_fuse",
    response_model=GenericResponse,
    summary="Reset software fuse (sfuse_blown)",
    description=(
        "Resets the software fuse on the Symovo controller.\n\n"
        "The sfuse_blown flag is set when a high motor current is detected. "
        "This endpoint clears the flag so the robot can resume operation."
    ),
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def reset_software_fuse(client: SymovoClient) -> Any:
    result = await client.reset_software_fuse()
    return {"status": "ok", "result": result}


@router.get(
    "/safety/state",
    response_model=GenericResponse,
    summary="Get current safety state",
    description=(
        "Returns the current safety lockout state derived from Symovo state_flags.\n\n"
        "Fields:\n"
        "- **safety_lockout**: true if E-Stop, relay open, or fuse blown\n"
        "- **reason**: estop | relay_open | sfuse_blown | null\n"
        "- **recovery_available**: true when relay restored but subsystems not yet recovered\n"
        "- **state_flags**: raw Symovo state_flags dict"
    ),
    response_model_exclude_none=True,
)
@safe_getter(GenericResponse)
async def get_safety_state(tracker: InjectedSafetyTracker) -> Any:
    """Return safety state from the shared background tracker."""
    state = tracker.current_state()
    return {"status": "ok", **state.to_dict()}


@router.get(
    "/charging_stations",
    response_model=GenericResponse,
    summary="List all charging stations",
    description="Fetches a list of all charging stations from the Symovo controller.",
    response_description="Charging stations list",
)
@safe_getter(GenericResponse)
async def get_charging_stations(client: SymovoClient) -> Any:
    """Get list of all charging stations."""
    data = await client.get_charging_stations()
    if isinstance(data, dict):
        return data
    return {"stations": data}


@router.post(
    "/go_to_charging_station/{station_id}",
    response_model=GenericResponse,
    summary="Go to charging station",
    description=(
        "Activates a charging station by its ID and lets the AGV navigate there automatically."
    ),
    response_description="Result of station activation",
)
@safe_getter(GenericResponse)
async def go_to_charging_station(
    client: SymovoClient,
    station_id: int = Path(..., description="ID of the charging station")
) -> Any:
    """Go to charging station."""
    # Enable the station
    result = await client.set_charging_station_enabled(station_id, active=True)

    if isinstance(result, dict):
        return result
    return {"station_id": station_id, "activated": bool(result)}


@router.post(
    "/charging_stations/disable_all",
    response_model=GenericResponse,
    summary="Deactivate all charging stations",
    description=(
        "Deactivates every charging station so the AGV does not return to the dock. "
        "Waits up to 5 s for the controller to confirm INACTIVE state."
    ),
)
@safe_getter(GenericResponse)
async def disable_all_charging_stations(client: SymovoClient) -> Any:
    results = await client.disable_all_charging_stations()
    ok = await client.wait_until_charging_stations_inactive(timeout=5.0)
    return {"deactivated": len(results), "all_inactive": ok}


@router.get(
    "/transport/{transport_id}",
    response_model=GenericResponse,
    summary="Get transport by id",
    description=(
        "Reads a specific transport job state from Symovo controller. Useful to poll after creating a transport."
    ),
    response_description="Transport entity",
)
@safe_getter(GenericResponse)
async def get_transport(transport_id: int, client: SymovoClient) -> Any:
    """Get transport task info by ID."""
    data = await client.transport_get(transport_id)
    if isinstance(data, dict):
        return data
    return {"transport": data}


@router.get(
    "/transport/{transport_id}/wait_for_changes",
    response_model=GenericResponse,
    summary="Wait changes of transport by id",
    description=(
        "Long-poll endpoint that waits until the requested transport changes on the controller."
    ),
    response_description="Updated transport entity",
)
@safe_getter(GenericResponse)
async def wait_transport_changes(transport_id: int, client: SymovoClient) -> Any:
    """Wait for transport task changes."""
    data = await client.transport_wait_for_changes(transport_id)
    if isinstance(data, dict):
        return data
    return {"transport": data}


@router.get(
    "/map",
    response_model=GenericResponse,
    summary="Current map list",
    description="Returns list of maps available on the controller.",
    response_description="Maps list",
)
@safe_getter(GenericResponse)
async def get_map(client: SymovoClient) -> Any:
    """Get list of available maps."""
    data = await client.map()
    if isinstance(data, dict):
        return data
    return {"maps": data}


@router.get(
    "/map/{map_id}/full.png",
    summary="Get map as PNG image",
    description="Returns the full map as a PNG image for visualization.",
    response_description="PNG image of the map",
)
async def get_map_png(
    client: SymovoClient,
    map_id: int = Path(..., description="Map ID"),
):
    """Get map as PNG image."""
    try:
        from fastapi.responses import Response

        image_data = await client.map_png(map_id)
        return Response(
            content=image_data,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=300",
            },
        )
    except Exception as e:
        _logger.error("Error fetching map image for map_id=%s: %s", map_id, e)
        raise HTTPException(
            status_code=503,
            detail={"error": {"type": "controller_error", "msg": "Controller communication error"}}
        )


@router.post(
    "/go_to_pose",
    response_model=GenericResponse,
    summary="Navigate to pose",
    description=(
        "Creates a transport that moves the AGV to the specified pose.\n\n"
        "Request model has examples; set wait=true to return final transport state."
    ),
    response_description="Transport result or created transport",
)
@safe_getter(GenericResponse)
async def go_to_pose(req: GoToPoseRequest, client: SymovoClient) -> Any:
    """Move AGV to the specified pose."""
    # Prefer transport API, which is stable on some firmware versions
    rad = req.theta_deg * math.pi / 180.0
    data = await client.transport_move_to_pose(
        x_m=req.x_m,
        y_m=req.y_m,
        theta_rad=rad,
        map_id=req.map_id,
        max_speed_m_s=req.max_speed_m_s,
        wait=req.wait,
    )
    # P1-1: when wait=True, lock is already released; poll for completion outside it.
    if isinstance(data, dict) and "_wait_transport_id" in data:
        tid = data.pop("_wait_transport_id")
        data = await client.poll_transport_completion(tid)
    if isinstance(data, dict):
        return data
    return {"result": data}


@router.get(
    "/api/v1/symovo/map",
    response_model=GenericResponse,
    summary="[v1] Current map list",
    description="Read-only: returns list of maps available on Symovo controller.",
    response_description="Maps list",
)
@safe_getter(GenericResponse)
async def get_map_v1(client: SymovoClient) -> Any:
    data = await client.map()
    if isinstance(data, dict):
        return data
    return {"maps": data}


@router.get(
    "/api/v1/symovo/map/{map_id}",
    response_model=GenericResponse,
    summary="[v1] Get map by id",
    description="Read-only: returns map metadata by ID.",
)
@safe_getter(GenericResponse)
async def get_map_by_id_v1(
    client: SymovoClient,
    map_id: int = Path(..., description="Map ID"),
) -> Any:
    return await client.map_get(map_id)


@router.get(
    "/api/v1/symovo/map/{map_id}/wait_for_changes",
    response_model=GenericResponse,
    summary="[v1] Wait for map changes by id",
    description="Read-only long-poll endpoint for map updates.",
)
@safe_getter(GenericResponse)
async def wait_map_changes_v1(
    client: SymovoClient,
    map_id: int = Path(..., description="Map ID"),
    since: str = Query(default="now", description="Change cursor / etag marker"),
    timeout: float | None = Query(default=None, ge=0.1, le=120.0, description="Long-poll timeout in seconds"),
) -> Any:
    return await client.map_wait_for_changes(map_id, since=since, timeout=timeout)


@router.get(
    "/api/v1/symovo/map/{map_id}/full.png",
    summary="[v1] Get full map as PNG",
    description="Read-only: returns full map PNG for map_id.",
)
async def get_map_png_v1(
    client: SymovoClient,
    map_id: int = Path(..., description="Map ID"),
) -> Response:
    try:
        image_data = await client.map_png(map_id)
        return Response(
            content=image_data,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=300"},
        )
    except Exception as e:
        _logger.error("Error fetching map PNG v1 for map_id=%s: %s", map_id, e)
        raise HTTPException(status_code=503, detail={"error": {"type": "controller_error", "msg": "Controller communication error"}})


@router.get(
    "/api/v1/symovo/map/{map_id}/{zoom}/{x}/{y}.png",
    summary="[v1] Get map tile as PNG",
    description="Read-only: returns a single 256x256 map tile.",
)
async def get_map_tile_png_v1(
    client: SymovoClient,
    map_id: int = Path(..., description="Map ID"),
    zoom: int = Path(..., ge=0, description="Tile zoom level"),
    x: int = Path(..., ge=0, description="Tile X"),
    y: int = Path(..., ge=0, description="Tile Y"),
) -> Response:
    try:
        image_data = await client.map_tile_png(map_id=map_id, zoom=zoom, x=x, y=y)
        return Response(
            content=image_data,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=60"},
        )
    except Exception as e:
        _logger.error("Error fetching map tile map_id=%s z=%s x=%s y=%s: %s", map_id, zoom, x, y, e)
        raise HTTPException(status_code=503, detail={"error": {"type": "controller_error", "msg": "Controller communication error"}})


@router.get(
    "/api/v1/symovo/map/slam/slam.png",
    summary="[v1] Get SLAM preview map as PNG",
    description="Read-only: returns live SLAM map preview image.",
)
async def get_map_slam_png_v1(client: SymovoClient) -> Response:
    try:
        image_data = await client.map_slam_png()
        return Response(
            content=image_data,
            media_type="image/png",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        _logger.error("Error fetching SLAM PNG: %s", e)
        raise HTTPException(status_code=503, detail={"error": {"type": "controller_error", "msg": "Controller communication error"}})


@router.get(
    "/api/v1/symovo/lidar/scan.png",
    summary="[v1] Get lidar scan as PNG",
    description="Read-only: returns current lidar scan image from controller.",
)
async def get_lidar_scan_png_v1(client: SymovoClient) -> Response:
    try:
        image_data = await client.scan_png()
        return Response(
            content=image_data,
            media_type="image/png",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        _logger.error("Error fetching lidar scan PNG: %s", e)
        raise HTTPException(status_code=503, detail={"error": {"type": "controller_error", "msg": "Controller communication error"}})


@router.get(
    "/api/v1/symovo/slam/state",
    response_model=GenericResponse,
    summary="[v1] Get SLAM state",
    description="Read-only: returns current SLAM state for configured robot.",
)
@safe_getter(GenericResponse)
async def get_slam_state_v1(client: SymovoClient) -> Any:
    return await client.slam_state()


@router.get(
    "/api/v1/symovo/slam/pose/station",
    response_model=GenericResponse,
    summary="[v1] Get SLAM station pose",
    description="Read-only: returns station-based SLAM pose.",
)
@safe_getter(GenericResponse)
async def get_slam_pose_station_v1(client: SymovoClient) -> Any:
    return await client.slam_pose_station()


@router.get(
    "/api/v1/symovo/slam/pose/reflector",
    response_model=GenericResponse,
    summary="[v1] Get SLAM reflector poses",
    description="Read-only: returns reflector-based SLAM pose set.",
)
@safe_getter(GenericResponse)
async def get_slam_pose_reflector_v1(client: SymovoClient) -> Any:
    return await client.slam_pose_reflector()


