"""NAT/STUN/TURN configuration service for Janus Gateway.

Owns all business logic for:
- Loading, caching, and persisting the janus-nat.json config
- Rendering the NAT block into janus.jcfg
- Generating coturn ephemeral TURN credentials
- Restarting the local or remote Janus service
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from typing import Dict, Literal, Optional, Tuple

import httpx
from pydantic import BaseModel, Field, field_validator

from app.core.settings import get_settings
from app.utils.fs import atomic_write_text
from app.utils.process import run_cmd
from app.services.proxy_base import AsyncProxyClient

from app.config import DEVICES, PORTS
from app.core.settings import env_str

# ── Pooled HTTP client for cross-node communication ─────────────────
# Uses AsyncProxyClient for consistent lifecycle, error mapping
# (Timeout→504, ConnectError→502), and pooled connection management.
_cross_node_proxy = AsyncProxyClient(
    "cross-node",
    connect_timeout=5.0,
    read_timeout=5.0,
    write_timeout=5.0,
    pool_timeout=5.0,
    max_keepalive=2,
    max_connections=5,
)


async def start_cross_node_client() -> None:
    """Pre-create the pooled cross-node client during startup (idempotent)."""
    await _cross_node_proxy.start()


async def close_cross_node_client() -> None:
    """Close the pooled cross-node client on shutdown (idempotent)."""
    await _cross_node_proxy.stop()

logger = logging.getLogger(__name__)

# ── Janus config file markers ────────────────────────────────────────
NAT_BEGIN_MARKER = "# BEGIN NAT AUTO"
NAT_END_MARKER = "# END NAT AUTO"

# ── Input validation patterns ────────────────────────────────────────
_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9.\-_]{0,253}$")
_IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


# ── Pydantic model ───────────────────────────────────────────────────

class JanusNatConfig(BaseModel):
    stun_server: str = Field(default_factory=lambda: env_str("TURN_HOST", ""))
    stun_port: int = Field(default=3478, ge=1, le=65535)

    turn_server: str = Field(default_factory=lambda: env_str("TURN_HOST", ""))
    turn_port: int = Field(default=3478, ge=1, le=65535)
    turn_type: Literal["udp", "tcp", "tls"] = Field(default="tcp")
    turn_user: str = Field(default_factory=lambda: env_str("TURN_USER", "webrtc"), max_length=128)
    turn_pwd: str = Field(default_factory=lambda: env_str("TURN_PASS", ""), max_length=256)

    nat_1_1_mapping: str = Field(default="")

    ice_tcp: bool = Field(default=False)
    full_trickle: bool = Field(default=True)
    keep_private_host: bool = Field(default=True)

    min_port: int = Field(default=40000, ge=1024, le=65535)
    max_port: int = Field(default=41000, ge=1024, le=65535)

    @field_validator("stun_server", "turn_server")
    @classmethod
    def _validate_hostname(cls, v: str) -> str:
        if v and not _HOSTNAME_RE.match(v):
            raise ValueError("invalid hostname: must be alphanumeric, dots, hyphens, or underscores only")
        return v

    @field_validator("nat_1_1_mapping")
    @classmethod
    def _validate_nat_ip(cls, v: str) -> str:
        if v and not _IPV4_RE.match(v):
            raise ValueError("nat_1_1_mapping must be an IPv4 address or empty")
        return v

    @field_validator("turn_user", "turn_pwd")
    @classmethod
    def _no_special_chars(cls, v: str) -> str:
        # Reject characters that could inject into janus.jcfg key = "value" syntax.
        for ch in ("\n", "\r", "\x00", '"', "{", "}"):
            if ch in v:
                raise ValueError(
                    "field must not contain newlines, null bytes, "
                    'quotes, or braces (would corrupt janus.jcfg)'
                )
        return v


# ── TURN credential generation ───────────────────────────────────────

def generate_turn_credentials(
    shared_secret: str,
    user: str = "webrtc",
    ttl: int = 86400,
) -> Tuple[str, str]:
    """Generate coturn TURN REST API ephemeral credentials.

    Uses the same algorithm as coturn ``use-auth-secret`` /
    ``static-auth-secret``:
      username = "<unix-expiry>:<user>"
      credential = Base64(HMAC-SHA1(username, shared_secret))

    Returns (username, credential).
    """
    expiry = int(time.time()) + ttl
    username = f"{expiry}:{user}"
    mac = hmac.new(shared_secret.encode(), username.encode(), hashlib.sha1)
    credential = base64.b64encode(mac.digest()).decode()
    return username, credential


# ── NAT config load / save / cache ──────────────────────────────────

_nat_config_cache: Optional[JanusNatConfig] = None
_nat_config_cache_ts: float = 0.0
_nat_config_lock = threading.Lock()


async def load_nat_config() -> JanusNatConfig:
    """
    Returns NAT settings shared between cameras.

    Result is cached for ``NAT_CONFIG_TTL_SEC`` seconds (default 30) to prevent
    blocking on every WebRTC /client-config request when the depth camera asks
    the color camera for its NAT settings.

    Depth camera instances do not manage their own NAT config; they reuse the
    primary (color) camera's settings. If the primary camera is temporarily
    unreachable we gracefully fall back to the locally stored config (if any)
    or to the baked-in defaults so that /client-config keeps working instead
    of returning HTTP 5xx.
    """
    global _nat_config_cache, _nat_config_cache_ts

    with _nat_config_lock:
        if _nat_config_cache is not None and (time.time() - _nat_config_cache_ts) < get_settings().nat_config_ttl_sec:
            return _nat_config_cache
    # Intentional: the lock is released before the (potentially slow) HTTP fetch.
    # Concurrent callers with an expired cache will each issue a fetch and the last
    # writer wins.  This is acceptable for a 30-second TTL cache — the cost of
    # redundant fetches is negligible and avoids holding the lock during I/O.

    data: Optional[Dict[str, str]] = None

    cam_type = get_settings().camera_type
    if cam_type == "depth_camera":
        try:
            url = f"http://{DEVICES.HOST_LAN_IP}:{PORTS.COLOR_CAMERA}/janus/nat"
            response = await _cross_node_proxy.get(url)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("[janus-nat] depth_camera fallback to local config: %s", exc)

    nat_json = get_settings().janus_nat_json
    if data is None and nat_json.exists():
        try:
            data = json.loads(nat_json.read_text())
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[janus-nat] Failed to load %s: %s", nat_json, exc)

    if data:
        try:
            cfg = JanusNatConfig.model_validate(data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[janus-nat] Invalid data, using defaults: %s", exc)
            cfg = JanusNatConfig()
    else:
        cfg = JanusNatConfig()

    with _nat_config_lock:
        _nat_config_cache = cfg
        _nat_config_cache_ts = time.time()

    return cfg


def save_nat_config(cfg: JanusNatConfig) -> None:
    """Persist NAT config to disk, never including turn_pwd.

    Note: there is an inherent TOCTOU race between the stat-and-write here
    and concurrent callers.  ``atomic_write_text`` (rename-into-place) ensures
    readers always see a complete file, but two simultaneous ``save_nat_config``
    calls can still overwrite each other's changes.  This is acceptable because
    config saves are operator-initiated (low frequency) and the 30-second cache
    TTL means the last writer wins within a short convergence window.
    """
    nat_json = get_settings().janus_nat_json
    # Never persist turn_pwd to disk — it must come from env, not a config file.
    data = cfg.model_dump(exclude={"turn_pwd"})
    atomic_write_text(nat_json, json.dumps(data, indent=2))
    # Invalidate cache so the next /client-config request reflects the new config
    # immediately, not after the 30-second TTL expires.
    global _nat_config_cache, _nat_config_cache_ts
    with _nat_config_lock:
        _nat_config_cache = None
        _nat_config_cache_ts = 0.0


# ── janus.jcfg rendering ─────────────────────────────────────────────

def render_nat_block(cfg: JanusNatConfig) -> str:
    """Render the NAT section for janus.jcfg from a JanusNatConfig.

    TURN password is read from the TURN_PASS environment variable at render
    time — never from the config model — so it is not persisted to disk in
    janus.jcfg (consistent with save_nat_config excluding turn_pwd from JSON).
    """
    def b(value: bool) -> str:
        return "true" if value else "false"

    turn_pwd = os.environ.get("TURN_PASS", "")

    return f"""nat: {{
  ice_tcp = {b(cfg.ice_tcp)}
  full_trickle = {b(cfg.full_trickle)}
  ignore_mdns = true
  ice_ignore_list = [ "docker", "veth", "lo", "vmnet" ]
  keep_private_host = {b(cfg.keep_private_host)}

  stun_server = "{cfg.stun_server}"
  stun_port   = {cfg.stun_port}

  turn_server = "{cfg.turn_server}"
  turn_port   = {cfg.turn_port}
  turn_type   = "{cfg.turn_type}"
  turn_user   = "{cfg.turn_user}"
  turn_pwd    = "{turn_pwd}"

  nat_1_1_mapping = "{cfg.nat_1_1_mapping}"
  min_port = {cfg.min_port}
  max_port = {cfg.max_port}
}}"""


def patch_janus_cfg_with_nat(cfg: JanusNatConfig) -> None:
    """Atomically rewrite the NAT block in janus.jcfg."""
    janus_cfg = get_settings().janus_cfg_path
    if not janus_cfg.exists():
        raise RuntimeError(f"{janus_cfg} not found")

    text = janus_cfg.read_text()

    try:
        start = text.index(NAT_BEGIN_MARKER)
        end = text.index(NAT_END_MARKER, start)
    except ValueError as exc:  # pragma: no cover - config guard rails
        raise RuntimeError("Markers '# BEGIN NAT AUTO' / '# END NAT AUTO' not found in janus.jcfg") from exc

    before = text[:start].rstrip()
    after = text[end + len(NAT_END_MARKER):].lstrip()
    nat_block = render_nat_block(cfg)

    new_text = (
        f"{before}\n"
        f"{NAT_BEGIN_MARKER}\n"
        f"{nat_block}\n"
        f"{NAT_END_MARKER}\n"
        f"{after}"
    )

    atomic_write_text(janus_cfg, new_text)


# ── Janus service restart helpers ────────────────────────────────────

def restart_janus() -> None:
    """Restart the local Janus service via systemd."""
    service = get_settings().janus_service_name
    run_cmd(
        ["sudo", "systemctl", "restart", service],
        timeout=30,
        error_prefix=f"Failed to restart {service}",
    )


async def restart_depth_camera_janus() -> None:
    """Ask the depth camera to restart its own Janus service."""
    try:
        url = f"http://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.COLOR_CAMERA}/janus/restart"
        settings = get_settings()
        headers = {"X-Admin-Token": settings.admin_token}
        response = await _cross_node_proxy.post(url, headers=headers, timeout=10.0)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to restart janus: {response.text}")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Failed to restart janus: {exc}") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Unknown error: {exc}") from exc


def _reset_for_tests() -> None:
    """Reset module-level cache for test isolation.

    Called by ``ServiceRegistry.reset()`` — keeps internal details private.
    """
    global _nat_config_cache, _nat_config_cache_ts
    with _nat_config_lock:
        _nat_config_cache = None
        _nat_config_cache_ts = 0.0
