"""Fake HTTP service responses for inter-service communication testing.

Usage::

    from testing_utils.fakes.http_services import (
        mock_xarm_service, mock_igus_service, mock_symovo_service,
    )

    # For httpx (api_gateway, frontend, xarm_service)
    import respx
    with respx.mock:
        mock_xarm_service(respx)
        ...

    # For aiohttp (robot_service)
    from aioresponses import aioresponses
    with aioresponses() as m:
        mock_xarm_service_aiohttp(m)
        ...
"""

from __future__ import annotations

from typing import Any

from shared_config.network import DEVICES, PORTS


# ── Default response payloads ───────────────────────────────────────────────

XARM_HEALTH_OK = {"status": "ok"}
XARM_READY_OK = {
    "ready": True,
    "connected": True,
    "faulted": False,
    "motion_enabled": True,
    "busy": False,
}
XARM_STATUS_OK = {
    "success": True,
    "joints": {"j1": 0, "j2": 0, "j3": 0, "j4": 0, "j5": 0, "j6": 0},
    "position": {"x": 100, "y": 200, "z": 300, "roll": 0, "pitch": 0, "yaw": 0},
}

IGUS_HEALTH_OK = {"status": "ok", "detail": "running"}
IGUS_STATUS_OK = {"position": 100, "homed": True, "moving": False, "fault": False}

SYMOVO_HEALTH_OK = {"status": "ok"}
SYMOVO_POSE_OK = {"x": 1.0, "y": 2.0, "theta": 0.5}

DEPTH_CAMERA_HEALTH_OK = {"ok": True}


# ── respx helpers (for httpx-based services) ─────────────────────────────

def mock_xarm_service(router: Any, base: str = f"http://{DEVICES.HOST_LAN_IP}:{PORTS.XARM}") -> None:
    """Register mock routes for xarm_service on a respx router."""
    router.get(f"{base}/health/live").respond(json=XARM_HEALTH_OK)
    router.get(f"{base}/health/ready").respond(json=XARM_READY_OK)
    router.get(f"{base}/status").respond(json=XARM_STATUS_OK)
    router.post(f"{base}/move/change_joints").respond(json={"success": True, "message": None})
    router.post(f"{base}/move/change_pose").respond(json={"success": True, "message": None})


def mock_igus_service(router: Any, base: str = f"http://{DEVICES.HOST_LAN_IP}:{PORTS.IGUS}") -> None:
    """Register mock routes for igus_service on a respx router."""
    router.get(f"{base}/healthz").respond(json=IGUS_HEALTH_OK)
    router.get(f"{base}/status").respond(json=IGUS_STATUS_OK)
    router.post(f"{base}/move").respond(json={"success": True})
    router.post(f"{base}/home").respond(json={"success": True})
    router.post(f"{base}/stop").respond(json={"success": True})


def mock_symovo_service(router: Any, base: str = f"http://{DEVICES.HOST_LAN_IP}:{PORTS.SYMOVO}") -> None:
    """Register mock routes for nav2adapter (symovo) on a respx router."""
    router.get(f"{base}/healthz").respond(json=SYMOVO_HEALTH_OK)
    router.get(f"{base}/api/v1/robots/fahrdummy-01/pose").respond(json=SYMOVO_POSE_OK)
    router.post(f"{base}/api/v1/robots/fahrdummy-01/move/speed").respond(json={"ok": True})
    router.post(f"{base}/drive_mode").respond(json={"ok": True})


def mock_depth_camera(router: Any, base: str = f"http://{DEVICES.DEPTH_CAMERA_IP}:8000") -> None:
    """Register mock routes for depth camera service."""
    router.get(f"{base}/healthz").respond(json=DEPTH_CAMERA_HEALTH_OK)
    router.get(f"{base}/depth").respond(content=b"\x00" * (640 * 480 * 2))


def mock_all_services(router: Any) -> None:
    """Register mocks for all backend services at once."""
    mock_xarm_service(router)
    mock_igus_service(router)
    mock_symovo_service(router)
    mock_depth_camera(router)


# ── aioresponses helpers (for aiohttp-based services like robot_service) ──

def mock_xarm_service_aiohttp(m: Any, base: str = f"http://{DEVICES.HOST_LAN_IP}:{PORTS.XARM}") -> None:
    """Register mock routes on an aioresponses instance."""
    m.get(f"{base}/health/live", payload=XARM_HEALTH_OK, repeat=True)
    m.get(f"{base}/health/ready", payload=XARM_READY_OK, repeat=True)
    m.get(f"{base}/status", payload=XARM_STATUS_OK, repeat=True)
    m.post(f"{base}/move/change_joints", payload={"success": True}, repeat=True)


def mock_igus_service_aiohttp(m: Any, base: str = f"http://{DEVICES.HOST_LAN_IP}:{PORTS.IGUS}") -> None:
    m.get(f"{base}/healthz", payload=IGUS_HEALTH_OK, repeat=True)
    m.get(f"{base}/status", payload=IGUS_STATUS_OK, repeat=True)
    m.post(f"{base}/move", payload={"success": True}, repeat=True)
    m.post(f"{base}/stop", payload={"success": True}, repeat=True)


def mock_symovo_service_aiohttp(m: Any, base: str = f"http://{DEVICES.HOST_LAN_IP}:{PORTS.SYMOVO}") -> None:
    m.get(f"{base}/healthz", payload=SYMOVO_HEALTH_OK, repeat=True)
    m.post(f"{base}/api/v1/robots/fahrdummy-01/move/speed", payload={"ok": True}, repeat=True)
    m.post(f"{base}/drive_mode", payload={"ok": True}, repeat=True)


def mock_all_services_aiohttp(m: Any) -> None:
    """Register aiohttp mocks for all backend services."""
    mock_xarm_service_aiohttp(m)
    mock_igus_service_aiohttp(m)
    mock_symovo_service_aiohttp(m)
