import asyncio
import logging
import os
import socket

from fastapi import FastAPI

from app.core.settings import get_settings
from app.services import janus_proxy, relay_proxy, watchdogs
from app.services.thermal import start_thermal_monitor

_log = logging.getLogger("events")

# ── systemd sd_notify via raw socket (no C dependency) ──────────
_NOTIFY_SOCKET = os.environ.get("NOTIFY_SOCKET")


def _sd_notify(state: str) -> None:
    """Send a sd_notify datagram if running under systemd."""
    if not _NOTIFY_SOCKET:
        return
    addr = _NOTIFY_SOCKET
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(state.encode())
    except OSError:
        _log.debug("sd_notify(%s) failed", state)


async def _watchdog_loop() -> None:
    """Periodically send WATCHDOG=1 keepalive to systemd."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec or not _NOTIFY_SOCKET:
        return
    interval = int(usec) / 1_000_000 / 2  # half the timeout
    while True:
        _sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)


async def _memory_gauge_loop():
    """Periodic RSS memory gauge update."""
    import resource
    try:
        from app.metrics import process_memory_bytes
    except Exception:
        return
    while True:
        try:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # KB -> bytes on Linux
            process_memory_bytes.set(rss)
        except Exception:
            pass
        await asyncio.sleep(30)


def register_event_handlers(app: FastAPI) -> None:
    @app.on_event("startup")
    async def _startup() -> None:
        from app.services import mode_enforcer
        mode_enforcer.register()

        # Publish camera identity to Prometheus
        try:
            from app.metrics import camera_info
            camera_info.info({
                "camera_type": get_settings().camera_type,
                "hostname": socket.gethostname(),
            })
        except Exception:
            _log.debug("Prometheus camera_info not available")

        watchdogs.start_janus_watchdog()
        await watchdogs.start_snapshot_watchdog()
        start_thermal_monitor()
        await janus_proxy.start_client()
        await relay_proxy.start_client()
        if get_settings().camera_type == "color_camera":
            from app.services import depth_camera_proxy
            await depth_camera_proxy.start_client()
        _sd_notify("READY=1")
        asyncio.create_task(_watchdog_loop())
        asyncio.create_task(_memory_gauge_loop())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        # Stop daemon threads and async watchdog tasks first
        watchdogs.stop_all()
        from app.services.thermal import stop_thermal_monitor
        stop_thermal_monitor()
        # Close HTTP proxy clients
        await janus_proxy.stop_client()
        await relay_proxy.stop_client()
        if get_settings().camera_type == "color_camera":
            from app.services import depth_camera_proxy
            await depth_camera_proxy.stop_client()
        # Close realsense_mux HTTP client
        from app.routes.depth import close_mux_client
        await close_mux_client()
        # Close Janus REST connection pool and thread pool
        from app.services.janus import close_client as _close_janus, _executor
        _close_janus()
        _executor.shutdown(wait=False)

