from fastapi import FastAPI

from app.core.settings import get_settings
from app.routes import camera, fdir, janus, media, metrics, system, telemetry


def register_routes(app: FastAPI) -> None:
    app.include_router(system.router)
    app.include_router(media.router)
    app.include_router(camera.router)
    app.include_router(janus.router)
    app.include_router(fdir.router)
    app.include_router(metrics.router)
    app.include_router(telemetry.router)

    # Versioned aliases and the camera API root are all registered here so that
    # every path that embeds the runtime camera_type is computed inside
    # register_routes() (called from create_app()) rather than at module import
    # time.  This lets tests override CAM_TYPE via environment variables without
    # requiring module reloads.
    cam_type = get_settings().camera_type
    _alias = app.add_api_route
    _alias(
        f"/api/v1/{cam_type}",
        system.camera_api_root,
        methods=["GET"],
        response_model=system.HealthResponse,
        summary="Camera API root",
        description="Used by API gateway to discover upstream prefix (/api/v1/{camera_type}).",
    )
    _alias(f"/api/v1/{cam_type}/status",          system.system_status,  include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/relay/time",       system.relay_time,     include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/relay/pong",       system.relay_pong,     include_in_schema=False, methods=["GET"])
    # depth_map/load is registered by depth.py (depth_camera) or depth_proxy.py (color_camera)
    if cam_type == "depth_camera":
        from app.routes import depth as _depth_mod
        _alias(f"/api/v1/{cam_type}/depth_map/load", _depth_mod.depth_map_load, include_in_schema=False, methods=["GET"])
    elif cam_type == "color_camera":
        from app.routes import depth_proxy as _dproxy_mod
        _alias(f"/api/v1/{cam_type}/depth_map/load", _dproxy_mod.depth_map_load, include_in_schema=False, methods=["GET"])

    # Versioned media/JS asset aliases (cam_type computed at runtime, not import time)
    _alias(f"/api/v1/{cam_type}/janus.js",              media.janus_js,           include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/streamer.js",           media.streaming_js,       include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/depth_features.js",     media.depth_features_js,  include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/gripper_reticle.js",    media.gripper_reticle_js, include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/gamepaddriver.js",      media.gamepad_js,         include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/gamepad_config.json",   media.gamepad_config,     include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/player/{{path:path}}",  media.player_script_no_prefix, include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/color_view.html",       media.color_view,         include_in_schema=False, methods=["GET"])
    _alias(f"/api/v1/{cam_type}/favicon.ico",           media.favicon,            include_in_schema=False, methods=["GET"])

    if cam_type == "color_camera":
        from app.routes import depth_proxy
        app.include_router(depth_proxy.router)

    if cam_type == "depth_camera":
        from app.routes import depth
        app.include_router(depth.router)
