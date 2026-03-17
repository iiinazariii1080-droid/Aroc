from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.admin import require_admin
from app.core.settings import get_settings
from app.services import janus, janus_proxy
from app.services.nat_config import (
    JanusNatConfig,
    generate_turn_credentials,
    load_nat_config,
    save_nat_config,
    patch_janus_cfg_with_nat,
    restart_janus,
    restart_depth_camera_janus,
)
from app.services.ws_pump import make_ssl_context, proxy_websocket

router = APIRouter(tags=["janus"])
ADMIN_DEPENDENCY = Depends(require_admin)
logger = logging.getLogger(__name__)


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
async def janus_healthz() -> JanusHealthResponse:
    settings = get_settings()
    # Use janus_summary() which reuses the watchdog's persistent session (1 HTTP
    # call) instead of streaming_info() which creates and destroys a full
    # session per call (5 HTTP calls: create+attach+info+detach+destroy).
    summary = await janus.janus_summary(settings.janus_mount_id)
    # enabled must be explicitly True — None or False both indicate the mount is not ready.
    return JanusHealthResponse(ok=summary.get("enabled") is True, mount_id=settings.janus_mount_id)


@router.post(
    "/janus/restart",
    summary="Restart Janus service",
    description="Restarts the Janus service.",
    dependencies=[ADMIN_DEPENDENCY],
)
async def _restart_janus() -> None:
    try:
        await asyncio.to_thread(restart_janus)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/client-config",
    response_model=ClientRtcConfig,
    summary="WebRTC ICE configuration for browser clients",
    description=(
        "Returns STUN/TURN configuration derived from Janus NAT settings so that the "
        "web client (`color_view.html`) can establish media both locally and remotely."
    ),
)
async def get_client_rtc_config() -> ClientRtcConfig:
    """
    Lightweight endpoint consumed by `color_view.html` to configure ICE.

    Security note: this endpoint is intentionally unauthenticated because the
    browser player needs ICE config to establish WebRTC.  When TURN_SHARED_SECRET
    is set, credentials are ephemeral (HMAC-based, TTL-limited).  When only
    static TURN_PASS is used, the credential is exposed — operators should
    prefer TURN_SHARED_SECRET in production deployments.

    Path expectations:
    - When the service listens directly on :8900, the page calls `/client-config`.
    - When it's reverse‑proxied under `/api/v1/{CAM_TYPE}`, the proxy typically
    rewrites `/api/v1/{CAM_TYPE}/client-config` -> `/client-config` on FastAPI.
    """
    settings = get_settings()
    nat_cfg = await load_nat_config()

    ice_servers: List[IceServer] = []

    # STUN (for reflexive candidates)
    stun_url = f"stun:{nat_cfg.stun_server}:{nat_cfg.stun_port}"
    ice_servers.append(IceServer(urls=[stun_url]))

    # ── TURN (multi-transport failover) ──
    # Provide UDP, TCP and TLS variants so the browser can fall back
    # through progressively more firewall-friendly transports.
    turn_host = nat_cfg.turn_server
    turn_port = nat_cfg.turn_port
    turn_tls_port = settings.turn_tls_port

    turn_urls_all: List[str] = []
    # Primary transport configured in Janus nat block
    if nat_cfg.turn_type in {"udp", "tcp"}:
        turn_urls_all.append(f"turn:{turn_host}:{turn_port}?transport=udp")
        turn_urls_all.append(f"turn:{turn_host}:{turn_port}?transport=tcp")
    if nat_cfg.turn_type == "tls" or turn_tls_port:
        turn_urls_all.append(f"turns:{turn_host}:{turn_tls_port}?transport=tcp")

    if turn_urls_all:
        # Prefer ephemeral TURN REST API credentials when shared secret is configured
        turn_shared_secret = settings.turn_shared_secret
        if turn_shared_secret:
            eph_user, eph_cred = generate_turn_credentials(
                shared_secret=turn_shared_secret,
                user=nat_cfg.turn_user,
                ttl=settings.turn_cred_ttl,
            )
            ice_servers.append(
                IceServer(
                    urls=turn_urls_all,
                    username=eph_user,
                    credential=eph_cred,
                )
            )
        else:
            # Static TURN credentials — exposed verbatim.  Log on first
            # request so operators notice in production logs.
            logger.warning(
                "Serving static TURN credentials via /client-config. "
                "Migrate to TURN_SHARED_SECRET for ephemeral credentials."
            )
            from app.metrics.safe import safe_inc
            safe_inc("turn_static_credential_served_total")
            ice_servers.append(
                IceServer(
                    urls=turn_urls_all,
                    username=nat_cfg.turn_user,
                    credential=nat_cfg.turn_pwd,
                )
            )

    policy: Literal["all", "relay"]
    policy_env = settings.ice_policy

    # Depth camera sits behind a double NAT (isolated router → color-camera
    # host → corporate router → internet).  Direct host/srflx candidates
    # advertised via nat_1_1_mapping will never reach it, so we force
    # relay-only ICE to skip the fruitless connectivity checks and connect
    # via TURN immediately.
    if settings.camera_type == "depth_camera":
        policy = "relay"
    elif policy_env == "relay":
        policy = "relay"
    else:
        policy = "all"

    return ClientRtcConfig(
        iceServers=ice_servers,
        iceTransportPolicy=policy,
    )


@router.get(
    "/janus/nat",
    dependencies=[ADMIN_DEPENDENCY],
    summary="Read Janus NAT/STUN/TURN settings",
    description="Loads the JSON stored at `/etc/robot/janus-nat.json`. "
                "`turn_pwd` is always masked in the response — it lives in env only.",
)
async def get_janus_nat_config():
    cfg = await load_nat_config()
    # Mask the password — never expose credentials through the management API.
    data = cfg.model_dump()
    data["turn_pwd"] = "***" if data["turn_pwd"] else ""
    return data


@router.post(
    "/janus/nat",
    response_model=JanusNatConfig,
    dependencies=[ADMIN_DEPENDENCY],
    summary="Update Janus NAT/STUN/TURN settings",
    description="Persists the JSON, rewrites the `janus.jcfg` block between markers, and restarts Janus. "
                "Only available on color_camera nodes.",
)
async def update_janus_nat_config(new_cfg: JanusNatConfig):
    settings = get_settings()
    if settings.camera_type != "color_camera":
        raise HTTPException(
            status_code=405,
            detail="NAT configuration is only available on color camera nodes.",
        )

    # ── Snapshot existing state for rollback ─────────────────────────
    old_cfg: JanusNatConfig | None = None
    old_janus_cfg_text: str | None = None

    try:
        old_cfg = await load_nat_config()
    except Exception as exc:
        logger.warning("Could not read current NAT config for rollback: %s", exc)

    try:
        if settings.janus_cfg_path.exists():
            old_janus_cfg_text = settings.janus_cfg_path.read_text()
    except OSError as exc:
        logger.warning("Could not read janus.jcfg for rollback: %s", exc)

    # ── Step 1: persist JSON ──────────────────────────────────────────
    try:
        save_nat_config(new_cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save NAT config: {exc}") from exc

    # ── Step 2: rewrite janus.jcfg ───────────────────────────────────
    try:
        patch_janus_cfg_with_nat(new_cfg)
    except RuntimeError as exc:
        _rollback_nat(old_cfg=old_cfg, old_janus_text=old_janus_cfg_text, restore_json=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to patch janus.jcfg (NAT JSON + janus.jcfg rolled back): {exc}",
        ) from exc

    # ── Step 3: restart Janus on both nodes ──────────────────────────
    try:
        await asyncio.to_thread(restart_janus)
        await restart_depth_camera_janus()
    except RuntimeError as exc:
        _rollback_nat(old_cfg=old_cfg, old_janus_text=old_janus_cfg_text, restore_json=True)
        raise HTTPException(
            status_code=500,
            detail=f"Janus restart failed (config rolled back): {exc}",
        ) from exc

    return new_cfg


def _rollback_nat(
    old_cfg: JanusNatConfig | None,
    old_janus_text: str | None,
    *,
    restore_json: bool,
) -> None:
    """Best-effort rollback of NAT config changes.  Logs failures; never raises."""
    if restore_json and old_cfg is not None:
        try:
            save_nat_config(old_cfg)
            logger.info("NAT JSON rolled back to previous version")
        except Exception as exc:
            logger.error("NAT JSON rollback failed: %s", exc)

    if old_janus_text is not None:
        try:
            from app.utils.fs import atomic_write_text
            atomic_write_text(get_settings().janus_cfg_path, old_janus_text)
            logger.info("janus.jcfg rolled back to previous version")
        except Exception as exc:
            logger.error("janus.jcfg rollback failed: %s", exc)


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
    ssl_ctx = make_ssl_context(upstream_url, allow_insecure=settings.allow_insecure_tls)
    # Always pass janus-protocol — Janus requires it in the WS upgrade request
    # and rejects connections without it (EOF before HTTP response).
    await proxy_websocket(
        client_ws, upstream_url, ssl_ctx=ssl_ctx,
        subprotocols=["janus-protocol"], log_name="janus-ws-proxy",
    )


@router.websocket("/janus-ws")
async def janus_ws_proxy_legacy(client_ws: WebSocket) -> None:
    """Legacy path used by v1 clients and the color-camera depth proxy."""
    await janus_ws_proxy(client_ws)


@router.get("/janus_healthz", include_in_schema=False)
async def legacy_janus_healthz() -> Dict[str, object]:
    """Deprecated: use /janus/healthz. Remove after 2026-06-01 if counter is 0."""
    from app.metrics.safe import safe_inc
    safe_inc("legacy_endpoint_hits_total", labels={"endpoint": "/janus_healthz"})
    return await janus_healthz()
