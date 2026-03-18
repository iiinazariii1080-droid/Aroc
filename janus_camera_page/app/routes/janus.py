from __future__ import annotations

import os
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.services import janus, janus_proxy
from app.services.nat_config import (
    JanusNatConfig,
    generate_turn_credentials,
    load_nat_config,
    patch_janus_cfg_with_nat,
    restart_depth_camera_janus,
    restart_janus,
    save_nat_config,
)
from shared_config.network import PORTS

router = APIRouter(tags=["janus"])
ADMIN_DEPENDENCY = Depends(require_admin)
# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type


class IceServer(BaseModel):
    urls: List[str]
    username: Optional[str] = None
    credential: Optional[str] = None
    credentialType: Literal["password"] = "password"


class ClientRtcConfig(BaseModel):
    iceServers: List[IceServer]
    iceTransportPolicy: Literal["all", "relay"] = "relay"
    sdpSemantics: Literal["unified-plan"] = "unified-plan"
    bundlePolicy: Literal["balanced", "max-bundle", "max-compat"] = "balanced"
    rtcpMuxPolicy: Literal["require"] = "require"


class JanusHealthResponse(BaseModel):
    ok: bool
    mount_id: int


# ── Janus health ──


@router.get(
    "/janus/healthz",
    response_model=JanusHealthResponse,
    summary="Check Janus mount availability",
    description="Queries janus.plugin.streaming to confirm the mount exists and is enabled.",
)
def janus_healthz() -> JanusHealthResponse:
    settings = get_settings()
    data = janus.streaming_info(settings.janus_mount_id)
    mount = (data or {}).get("data", {}).get("info", {})
    return JanusHealthResponse(ok=mount.get("enabled") is not None, mount_id=settings.janus_mount_id)


# ── Client WebRTC config ──


@router.get(
    f"/api/v1/{_CAM_TYPE}/client-config",
    response_model=ClientRtcConfig,
    summary="WebRTC ICE configuration for browser clients",
    description=(
        "Returns STUN/TURN configuration derived from Janus NAT settings so that the "
        "web client (`color_view.html`) can establish media both locally and remotely."
    ),
)
@router.get(
    "/client-config",
    response_model=ClientRtcConfig,
    summary="WebRTC ICE configuration for browser clients",
    description=(
        "Returns STUN/TURN configuration derived from Janus NAT settings so that the "
        "web client (`color_view.html`) can establish media both locally and remotely."
    ),
)
def get_client_rtc_config() -> ClientRtcConfig:
    settings = get_settings()
    nat_cfg = load_nat_config()

    ice_servers: List[IceServer] = []

    # STUN (for reflexive candidates)
    stun_url = f"stun:{nat_cfg.stun_server}:{nat_cfg.stun_port}"
    ice_servers.append(IceServer(urls=[stun_url]))

    # ── TURN (multi-transport failover) ──
    turn_host = nat_cfg.turn_server
    turn_port = nat_cfg.turn_port
    turn_tls_port_env = os.environ.get("TURN_TLS_PORT")

    turn_urls_all: List[str] = []
    if nat_cfg.turn_type in {"udp", "tcp"}:
        turn_urls_all.append(f"turn:{turn_host}:{turn_port}?transport=udp")
        turn_urls_all.append(f"turn:{turn_host}:{turn_port}?transport=tcp")
    if nat_cfg.turn_type == "tls" or turn_tls_port_env:
        tls_port = int(turn_tls_port_env) if turn_tls_port_env else 443
        turn_urls_all.append(f"turns:{turn_host}:{tls_port}?transport=tcp")

    if turn_urls_all:
        turn_shared_secret = settings.turn_shared_secret
        if turn_shared_secret:
            eph_user, eph_cred = generate_turn_credentials(
                shared_secret=turn_shared_secret,
                user=nat_cfg.turn_user,
                ttl=settings.turn_cred_ttl,
            )
            ice_servers.append(
                IceServer(urls=turn_urls_all, username=eph_user, credential=eph_cred)
            )
        else:
            ice_servers.append(
                IceServer(urls=turn_urls_all, username=nat_cfg.turn_user, credential=nat_cfg.turn_pwd)
            )

    # Depth camera behind double NAT → force relay-only ICE
    if settings.camera_type == "depth_camera":
        policy: Literal["all", "relay"] = "relay"
    elif settings.ice_policy == "relay":
        policy = "relay"
    else:
        policy = "all"

    return ClientRtcConfig(iceServers=ice_servers, iceTransportPolicy=policy)


# ── NAT config CRUD ──


@router.get(
    "/janus/nat",
    response_model=JanusNatConfig,
    dependencies=[ADMIN_DEPENDENCY],
    summary="Read Janus NAT/STUN/TURN settings",
    description="Loads the JSON stored at `/etc/robot/janus-nat.json`.",
)
def get_janus_nat_config():
    return load_nat_config()


if _CAM_TYPE == "color_camera":
    @router.post(
        "/janus/nat",
        response_model=JanusNatConfig,
        dependencies=[ADMIN_DEPENDENCY],
        summary="Update Janus NAT/STUN/TURN settings",
        description="Persists the JSON, rewrites the `janus.jcfg` block between markers, and restarts Janus.",
    )
    def update_janus_nat_config(new_cfg: JanusNatConfig):
            save_nat_config(new_cfg)

            try:
                patch_janus_cfg_with_nat(new_cfg)
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            try:
                restart_janus()
                restart_depth_camera_janus()
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            return new_cfg


# ── Janus restart ──


@router.post("/janus/restart", summary="Restart Janus service", description="Restarts the Janus service.", dependencies=[ADMIN_DEPENDENCY])
def _restart_janus() -> None:
    try:
        restart_janus()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Proxies ──


@router.api_route(
    "/janus",
    methods=["GET", "POST", "PUT", "DELETE"],
    summary="HTTP proxy to the Janus core API",
    description="Transparently forwards REST calls to the upstream Janus (`/janus`) endpoint used by the web client.",
)
async def proxy_janus_root(request: Request) -> Response:
    return await janus_proxy.forward_request(request)


@router.websocket("/janus/ws")
async def janus_ws_proxy(client_ws: WebSocket) -> None:
    from app.services.ws_proxy import proxy_websocket

    settings = get_settings()
    upstream_url = settings.janus_ws_backends.get("1", f"ws://127.0.0.1:{PORTS.JANUS_WS}/janus-ws")
    await proxy_websocket(client_ws, upstream_url, pass_subprotocol=False, label="janus-ws")
