"""Local fallback for shared_config.network constants.

When running inside the Aroc monorepo, ``shared_config.network`` is the
authoritative source (preferred via try/except in settings.py).
This module provides the same constants for standalone deployments:
  - CI/CD pipelines outside the monorepo
  - Unit-test isolation
  - Single-service Docker deployments

Keep these values in sync with ``shared_config/network.py``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ServicePorts:
    """Canonical port numbers used by janus_camera_page."""
    COLOR_CAMERA: int = 8900   # this service's listen port
    DEPTH_PROXY: int = 9000    # textroom relay / depth-proxy sidecar
    JANUS_HTTP: int = 8088     # Janus Gateway REST API
    JANUS_WS: int = 8188       # Janus Gateway WebSocket


@dataclass(frozen=True)
class _DeviceDefaults:
    """Default addresses for physical hardware.

    These are fallback values only. All addresses MUST be overridden via
    environment variables in production — no real infrastructure IPs are
    committed to source control.
    """
    DEPTH_CAMERA_IP: str = "127.0.0.1"   # override via DEPTH_CAM_URL env
    TURN_HOST: str = ""                   # must be set via TURN_HOST env
    HOST_LAN_IP: str = "127.0.0.1"       # override via env / shared_config


PORTS = _ServicePorts()
DEVICES = _DeviceDefaults()
