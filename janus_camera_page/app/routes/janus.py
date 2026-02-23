from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import subprocess
from pathlib import Path
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field
import requests
from starlette.websockets import WebSocketDisconnect
from websockets.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.services import janus, janus_proxy

router = APIRouter(tags=["janus"])
ADMIN_DEPENDENCY = Depends(require_admin)
CAM_TYPE = get_settings().camera_type

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


@router.get(
    "/janus/healthz",
    response_model=JanusHealthResponse,
    summary="Check Janus mount availability",
    description="Queries janus.plugin.streaming to confirm the mount exists and is enabled.",
)
def janus_healthz() -> JanusHealthResponse:
    settings = get_settings()
    data = janus.streaming_info(settings.janus_mount_id)
    mount = (data or {}).get("data", {}).get("info", {}).get("info", {})
    return JanusHealthResponse(ok=mount.get("enabled") is not None, mount_id=settings.janus_mount_id)


JANUS_CFG_PATH = Path("/opt/janus/etc/janus/janus.jcfg")
JANUS_NAT_JSON = Path("/etc/robot/janus-nat.json")

NAT_BEGIN_MARKER = "# BEGIN NAT AUTO"
NAT_END_MARKER = "# END NAT AUTO"


class JanusNatConfig(BaseModel):
    stun_server: str = Field(default="82.165.177.194")
    stun_port: int = Field(default=3478)

    turn_server: str = Field(default="82.165.177.194")
    turn_port: int = Field(default=3478)
    turn_type: Literal["udp", "tcp", "tls"] = Field(default="udp")
    turn_user: str = Field(default="webrtc")
    turn_pwd: str = Field(default="G456AH37gbc")

    nat_1_1_mapping: str = Field(default="87.156.23.54")

    ice_tcp: bool = Field(default=True)
    full_trickle: bool = Field(default=True)

    min_port: int = Field(default=40000)
    max_port: int = Field(default=41000)

def load_nat_config() -> JanusNatConfig:
    """
    Returns NAT settings shared between cameras.

    Depth camera instances do not manage their own NAT config; they reuse the
    primary (color) camera's settings. If the primary camera is temporarily
    unreachable we gracefully fall back to the locally stored config (if any)
    or to the baked-in defaults so that /client-config keeps working instead
    of returning HTTP 5xx.
    """

    data: Optional[Dict[str, str]] = None

    if CAM_TYPE == "depth_camera":
        try:
            response = requests.get(
                "http://192.168.1.10:8900/janus/nat", timeout=3
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            print(f"[janus-nat] depth_camera fallback to local config: {exc}")

    if data is None and JANUS_NAT_JSON.exists():
        try:
            data = json.loads(JANUS_NAT_JSON.read_text())
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[janus-nat] Failed to load {JANUS_NAT_JSON}: {exc}")

    if data:
        try:
            return JanusNatConfig.model_validate(data)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[janus-nat] Invalid data, using defaults: {exc}")

    return JanusNatConfig()

def save_nat_config(cfg: JanusNatConfig) -> None:
    JANUS_NAT_JSON.parent.mkdir(parents=True, exist_ok=True)
    JANUS_NAT_JSON.write_text(cfg.model_dump_json(indent=2))

def render_nat_block(cfg: JanusNatConfig) -> str:
    def b(value: bool) -> str:
        return "true" if value else "false"

    return f"""nat: {{
  ice_tcp = {b(cfg.ice_tcp)}
  full_trickle = {b(cfg.full_trickle)}
  ignore_mdns = true
  ice_ignore_list = [ "docker", "veth", "lo", "vmnet" ]

  stun_server = "{cfg.stun_server}"
  stun_port   = {cfg.stun_port}

  turn_server = "{cfg.turn_server}"
  turn_port   = {cfg.turn_port}
  turn_type   = "{cfg.turn_type}"
  turn_user   = "{cfg.turn_user}"
  turn_pwd    = "{cfg.turn_pwd}"

  nat_1_1_mapping = "{cfg.nat_1_1_mapping}"
  min_port = {cfg.min_port}
  max_port = {cfg.max_port}
}}"""

def restart_janus() -> None:
    res = subprocess.run(
        ["sudo", "systemctl", "restart", "janus.service"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to restart janus: {res.stderr or res.stdout}")

def restart_depth_camera_janus() -> None:
    try:
        url = f"http://192.168.1.55:8900/janus/restart"
        response = requests.post(url)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to restart janus: {response.text}")
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to restart janus: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unknown error: {exc}") from exc

@router.post("/janus/restart", summary="Restart Janus service", description="Restarts the Janus service.")
async def _restart_janus() -> None:
    try:
        restart_janus()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



def patch_janus_cfg_with_nat(cfg: JanusNatConfig) -> None:
    if not JANUS_CFG_PATH.exists():
        raise RuntimeError(f"{JANUS_CFG_PATH} not found")

    text = JANUS_CFG_PATH.read_text()

    try:
        start = text.index(NAT_BEGIN_MARKER)
        end = text.index(NAT_END_MARKER, start)
    except ValueError as exc:  # pragma: no cover - config guard rails
        raise RuntimeError("Markers '# BEGIN NAT AUTO' / '# END NAT AUTO' not found in janus.jcfg") from exc

    before = text[:start].rstrip()
    after = text[end + len(NAT_END_MARKER) :].lstrip()

    nat_block = render_nat_block(cfg)

    new_text = (
        f"{before}\n"
        f"{NAT_BEGIN_MARKER}\n"
        f"{nat_block}\n"
        f"{NAT_END_MARKER}\n"
        f"{after}"
    )

    JANUS_CFG_PATH.write_text(new_text)
@router.get(
    f"/api/v1/{CAM_TYPE}/client-config",
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
    """
    Lightweight endpoint consumed by `color_view.html` to configure ICE.

    Path expectations:
    - When the service listens directly on :8900, the page calls `/client-config`.
    - When it's reverse‑proxied under `/api/v1/{CAM_TYPE}`, the proxy typically
    rewrites `/api/v1/{CAM_TYPE}/client-config` -> `/client-config` on FastAPI.
    """
    settings = get_settings()
    nat_cfg = load_nat_config()

    ice_servers: List[IceServer] = []

    # STUN (for reflexive candidates)
    stun_url = f"stun:{nat_cfg.stun_server}:{nat_cfg.stun_port}"
    ice_servers.append(IceServer(urls=[stun_url]))

    # TURN (for relayed candidates)
    turn_schemes: List[str] = []
    if nat_cfg.turn_type in {"udp", "tcp"}:
        # plain TURN, transport specified in the query string
        turn_schemes.append("turn")
    elif nat_cfg.turn_type == "tls":
        # secured TURN over TLS
        turn_schemes.append("turns")

    if turn_schemes:
        turn_urls = [
            f"{scheme}:{nat_cfg.turn_server}:{nat_cfg.turn_port}?transport={nat_cfg.turn_type}"
            for scheme in turn_schemes
        ]
        ice_servers.append(
            IceServer(
                urls=turn_urls,
                username=nat_cfg.turn_user,
                credential=nat_cfg.turn_pwd,
            )
        )

    policy: Literal["all", "relay"]
    policy_env = settings.ice_policy
    if policy_env == "relay":
        policy = "relay"
    else:
        policy = "all"

    return ClientRtcConfig(
        iceServers=ice_servers,
        iceTransportPolicy=policy,
    )


@router.get(
    "/janus/nat",
    response_model=JanusNatConfig,
    dependencies=[ADMIN_DEPENDENCY],
    summary="Read Janus NAT/STUN/TURN settings",
    description="Loads the JSON stored at `/etc/robot/janus-nat.json`.",
)
async def get_janus_nat_config():
    return load_nat_config()

if CAM_TYPE == "color_camera":
    @router.post(
        "/janus/nat",
        response_model=JanusNatConfig,
        dependencies=[ADMIN_DEPENDENCY],
        summary="Update Janus NAT/STUN/TURN settings",
        description="Persists the JSON, rewrites the `janus.jcfg` block between markers, and restarts Janus.",
    )
    async def update_janus_nat_config(new_cfg: JanusNatConfig):
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

def _ssl_ctx_for(url: str) -> ssl.SSLContext | None:
    settings = get_settings()
    if url.startswith("wss://"):
        ctx = ssl.create_default_context()
        if settings.allow_insecure_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None

async def _pump_client_to_upstream(client_ws: WebSocket, upstream_ws) -> None:
    try:
        while True:
            message = await client_ws.receive()
            msg_type = message.get("type")
            if msg_type == "websocket.receive":
                if "text" in message and message["text"] is not None:
                    await upstream_ws.send(message["text"])
                elif "bytes" in message and message["bytes"] is not None:
                    await upstream_ws.send(message["bytes"])
            elif msg_type == "websocket.disconnect":
                try:
                    await upstream_ws.close()
                except Exception:
                    pass
                break
    except WebSocketDisconnect:
        try:
            await upstream_ws.close()
        except Exception:
            pass

async def _pump_upstream_to_client(client_ws: WebSocket, upstream_ws) -> None:
    try:
        async for message in upstream_ws:
            if isinstance(message, (bytes, bytearray)):
                await client_ws.send_bytes(message)
            else:
                await client_ws.send_text(message)
    except (ConnectionClosedOK, ConnectionClosedError):
        try:
            await client_ws.close()
        except Exception:
            pass

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
    settings = get_settings()
    upstream_url = settings.janus_ws_backends.get("1", "ws://127.0.0.1:8188/janus-ws")

    req_header = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [item.strip() for item in req_header.split(",") if item.strip()]
    subprotocol = "janus-protocol" if "janus-protocol" in offered else None

    await client_ws.accept(subprotocol=subprotocol)

    kwargs: Dict[str, object] = {
        "open_timeout": 5,
        "ping_interval": 20,
        "ping_timeout": 20,
        "close_timeout": 3,
        "max_size": 2**20,
        "compression": None,
        "ssl": _ssl_ctx_for(upstream_url),
    }
    if subprotocol:
        kwargs["subprotocols"] = [subprotocol]

    try:
        async with ws_connect(upstream_url, **kwargs) as upstream_ws:
            await asyncio.gather(
                _pump_client_to_upstream(client_ws, upstream_ws),
                _pump_upstream_to_client(client_ws, upstream_ws),
            )
    except Exception as exc:
        logging.error("WS proxy error: %s", exc)
        await client_ws.close()

@router.get("/janus_healthz", include_in_schema=False)
def legacy_janus_healthz() -> Dict[str, object]:
    return janus_healthz()

@router.get("/admin/janus-nat", include_in_schema=False, response_model=JanusNatConfig, dependencies=[ADMIN_DEPENDENCY])
async def legacy_get_janus_nat_config():
    return await get_janus_nat_config()

@router.post("/admin/janus-nat", include_in_schema=False, response_model=JanusNatConfig, dependencies=[ADMIN_DEPENDENCY])
async def legacy_update_janus_nat_config(new_cfg: JanusNatConfig):
    return await update_janus_nat_config(new_cfg)

@router.get(f"/api/v1/{CAM_TYPE}/janus-ws", include_in_schema=False)
@router.websocket("/janus-ws")
async def legacy_janus_ws_proxy(client_ws: WebSocket) -> None:
    await janus_ws_proxy(client_ws)

