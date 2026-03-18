"""NAT/STUN/TURN configuration management for Janus.

Handles loading, saving, rendering, and patching of NAT configuration
shared between color and depth camera nodes.  Also manages TURN
credential generation and Janus service restarts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import httpx
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.services.system import atomic_write_text, run as run_cmd
from shared_config.network import DEVICES, PORTS

logger = logging.getLogger(__name__)

NAT_BEGIN_MARKER = "# BEGIN NAT AUTO"
NAT_END_MARKER = "# END NAT AUTO"


def _env(key: str, fallback: str = "") -> str:
    """Read TURN/STUN defaults from env vars (same source as Settings)."""
    return os.environ.get(key, fallback)


# ── Models ─────────────────────────────────────────────────────────────


class JanusNatConfig(BaseModel):
    stun_server: str = Field(default_factory=lambda: _env("TURN_HOST", "82.165.177.194"))
    stun_port: int = Field(default=3478)

    turn_server: str = Field(default_factory=lambda: _env("TURN_HOST", "82.165.177.194"))
    turn_port: int = Field(default=3478)
    turn_type: Literal["udp", "tcp", "tls"] = Field(default="tcp")
    turn_user: str = Field(default_factory=lambda: _env("TURN_USER", "webrtc"))
    turn_pwd: str = Field(default_factory=lambda: _env("TURN_PASS", ""))

    nat_1_1_mapping: str = Field(default="")

    ice_tcp: bool = Field(default=False)
    full_trickle: bool = Field(default=True)
    ice_enforce_list: str = Field(default="br0", description="Whitelist of interfaces for ICE gathering (substring match)")
    keep_private_host: bool = Field(default=False)

    min_port: int = Field(default=40000)
    max_port: int = Field(default=41000)


# ── TURN credentials ───────────────────────────────────────────────────


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


# ── Load / Save ────────────────────────────────────────────────────────


def _janus_cfg_path() -> Path:
    return get_settings().janus_cfg_path


def _janus_nat_json() -> Path:
    return get_settings().janus_nat_json


def load_nat_config() -> JanusNatConfig:
    """Return NAT settings shared between cameras.

    Depth camera instances reuse the primary (color) camera's settings.
    If the primary camera is temporarily unreachable we fall back to the
    locally stored config or to the baked-in defaults.
    """
    data: Optional[Dict[str, str]] = None

    if get_settings().camera_type == "depth_camera":
        try:
            response = httpx.get(
                f"http://{DEVICES.HOST_LAN_IP}:{PORTS.COLOR_CAMERA}/janus/nat", timeout=3
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning("depth_camera fallback to local config: %s", exc)

    if data is None and _janus_nat_json().exists():
        try:
            data = json.loads(_janus_nat_json().read_text())
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load %s: %s", _janus_nat_json(), exc)

    if data:
        try:
            return JanusNatConfig.model_validate(data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Invalid NAT data, using defaults: %s", exc)

    return JanusNatConfig()


def save_nat_config(cfg: JanusNatConfig) -> None:
    atomic_write_text(_janus_nat_json(), cfg.model_dump_json(indent=2))


# ── Render / Patch janus.jcfg ──────────────────────────────────────────


def render_nat_block(cfg: JanusNatConfig) -> str:
    def b(value: bool) -> str:
        return "true" if value else "false"

    return f"""nat: {{
  ice_tcp = {b(cfg.ice_tcp)}
  full_trickle = {b(cfg.full_trickle)}
  ignore_mdns = true
  ice_enforce_list = "{cfg.ice_enforce_list}"
  keep_private_host = {b(cfg.keep_private_host)}

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


def patch_janus_cfg_with_nat(cfg: JanusNatConfig) -> None:
    """Rewrite the NAT block in janus.jcfg between markers."""
    if not _janus_cfg_path().exists():
        raise RuntimeError(f"{_janus_cfg_path()} not found")

    text = _janus_cfg_path().read_text()

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

    atomic_write_text(_janus_cfg_path(), new_text)


# ── Service restarts ───────────────────────────────────────────────────


def restart_janus() -> None:
    run_cmd(["sudo", "systemctl", "restart", "janus.service"], timeout=60)


def restart_depth_camera_janus() -> None:
    try:
        url = f"http://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.COLOR_CAMERA}/janus/restart"
        response = httpx.post(url, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to restart janus: {response.text}")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Failed to restart janus: {exc}") from exc
