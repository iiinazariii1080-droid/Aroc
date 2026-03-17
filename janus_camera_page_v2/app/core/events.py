import asyncio
import hashlib
import logging
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.core.settings import get_settings
from app.services import janus as janus_service, janus_proxy, relay_proxy, watchdogs
from app.services.thermal import start_thermal_monitor, stop_thermal_monitor

_log = logging.getLogger(__name__)

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


def _run_startup_checks(settings) -> None:
    """Log warnings for mandatory configuration that is missing or insecure.

    These checks run at startup so operators see problems in the service
    journal immediately, not silently at first use.

    When ``settings.startup_fail_fast`` is True (the default), critical
    checks raise ``SystemExit`` to prevent the service from starting in a
    known-broken configuration.
    """
    fatal_errors: list[str] = []

    # TURN auth: remote WebRTC clients cannot connect without it.
    if not settings.turn_pass and not settings.turn_shared_secret:
        msg = (
            "STARTUP CHECK FAILED: Neither TURN_PASS nor TURN_SHARED_SECRET "
            "is set. Remote WebRTC clients will fail to connect through NAT. "
            "Set at least one via /etc/robot/camera-secrets.env"
        )
        _log.error(msg)
        fatal_errors.append(msg)

    # Static TURN password without ephemeral credentials: the password is
    # exposed verbatim via the unauthenticated /client-config endpoint.
    # Warn loudly; in fail-fast mode this is not fatal (service still works)
    # but operators should migrate to TURN_SHARED_SECRET.
    if settings.turn_pass and not settings.turn_shared_secret:
        _log.warning(
            "STARTUP CHECK: TURN_PASS is set without TURN_SHARED_SECRET. "
            "Static TURN credentials are exposed via /client-config to any "
            "unauthenticated client. Migrate to TURN_SHARED_SECRET for "
            "ephemeral HMAC-based credentials in production."
        )

    # janus.js: the player UI is broken if this file is absent.
    janus_js = Path(settings.templates_dir) / "janus.js"
    if not janus_js.exists():
        msg = (
            f"STARTUP CHECK FAILED: janus.js not found at {janus_js}. "
            "The WebRTC player UI will return 503 for every request. "
            "Deploy the Janus Gateway JavaScript library before starting the service. "
            "See infrastructure/README.md for the required version."
        )
        _log.critical(msg)
        fatal_errors.append(msg)
    elif settings.janus_js_sha256:
        # Verify janus.js integrity when an expected hash is configured.
        actual = hashlib.sha256(janus_js.read_bytes()).hexdigest()
        if actual != settings.janus_js_sha256:
            _log.error(
                "STARTUP CHECK FAILED: janus.js integrity mismatch. "
                "Expected SHA256=%s, got %s. "
                "The file may have been modified or is an unexpected version. "
                "Update JANUS_JS_SHA256 in camera-secrets.env if this is intentional.",
                settings.janus_js_sha256,
                actual,
            )

    # allow_insecure_tls: log loudly if enabled in production.
    if settings.allow_insecure_tls:
        _log.error(
            "STARTUP CHECK FAILED: ALLOW_INSECURE_TLS=1 is set. "
            "TLS certificate verification is DISABLED. "
            "This must never be enabled in production."
        )

    # Metrics endpoint: warn if unauthenticated exposure is in effect.
    if not settings.api_key:
        _log.warning(
            "STARTUP CHECK: CAMCTRL_API_KEY is not set — the /metrics endpoint "
            "is unauthenticated. Set CAMCTRL_API_KEY in camera-secrets.env to "
            "restrict Prometheus scraping to authorised clients."
        )

    # depth_camera: HOST_LAN_IP is required for NAT/TURN config.
    if settings.camera_type == "depth_camera":
        from app.config.network_defaults import DEVICES
        if getattr(DEVICES, "HOST_LAN_IP", "127.0.0.1") == "127.0.0.1":
            _log.warning(
                "STARTUP CHECK: HOST_LAN_IP is not configured (using loopback "
                "fallback). Remote WebRTC clients on the depth camera node will "
                "fail NAT traversal. Set HOST_LAN_IP in camera-secrets.env."
            )

    # ── Fail fast on critical errors ──
    if fatal_errors and settings.startup_fail_fast:
        _log.critical(
            "Aborting startup due to %d critical check(s). "
            "Set CAM_STARTUP_FAIL_FAST=0 to override (not recommended for production).",
            len(fatal_errors),
        )
        raise SystemExit(1)


# Kept as a module-level reference so the task can be cancelled on shutdown.
_sd_watchdog_task: asyncio.Task | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _sd_watchdog_task

    # ── startup ──────────────────────────────────────────────────
    settings = get_settings()
    # Evaluated here, not at module import, so tests can override CAM_TYPE via
    # environment variables or Settings patches without module reload.
    is_color = settings.camera_type == "color_camera"
    is_depth = settings.camera_type == "depth_camera"
    _run_startup_checks(settings)

    from app.services.recovery_ladder import get_ladder
    get_ladder().check_circuit_breaker()

    # Start Janus HTTP client before watchdog so the pooled connection is ready.
    await janus_service.start_janus_client()
    await watchdogs.start_janus_watchdog()
    await watchdogs.start_snapshot_watchdog()
    start_thermal_monitor()
    await janus_proxy.start_client()
    await relay_proxy.start_client()
    if is_color:
        from app.services import depth_camera_proxy
        await depth_camera_proxy.start_client()
    if is_depth:
        from app.services import realsense_mux_proxy
        await realsense_mux_proxy.start_client()
    from app.services.nat_config import start_cross_node_client
    await start_cross_node_client()
    _sd_notify("READY=1")
    _sd_watchdog_task = asyncio.create_task(_watchdog_loop())

    yield

    # ── shutdown ─────────────────────────────────────────────────
    # 1. Cancel the systemd keepalive task first.
    if _sd_watchdog_task and not _sd_watchdog_task.done():
        _sd_watchdog_task.cancel()
        try:
            await _sd_watchdog_task
        except asyncio.CancelledError:
            pass

    # 2. Stop watchdog tasks and daemon threads BEFORE shutting down the
    #    listener executor so they cannot submit to an already-closed
    #    ThreadPoolExecutor.
    await watchdogs.stop_janus_watchdog()
    stop_thermal_monitor()

    # 2a. Explicitly destroy the watchdog's persistent Janus session now that
    #     the watchdog thread has been signalled to stop.  Best-effort — errors
    #     are swallowed inside close_monitor_session().
    try:
        await janus_service.close_monitor_session()
    except Exception:
        _log.warning("Failed to close Janus monitor session during shutdown", exc_info=True)

    # 3. Stop async watchdog task and HTTP clients.
    await watchdogs.stop_snapshot_watchdog()
    await janus_proxy.stop_client()
    await relay_proxy.stop_client()
    if is_color:
        from app.services import depth_camera_proxy
        await depth_camera_proxy.stop_client()
    if is_depth:
        from app.services import realsense_mux_proxy
        await realsense_mux_proxy.stop_client()

    # 4. Close HTTP clients (Janus + cross-node).
    await janus_service.stop_janus_client()
    try:
        from app.services.nat_config import close_cross_node_client
        await close_cross_node_client()
    except Exception:
        _log.debug("Failed to close cross-node client during shutdown", exc_info=True)

    # 5. Shut down the mode-listener thread pool cleanly.
    try:
        from app.services import system_mode
        system_mode.shutdown_listener_executor()
    except Exception:
        _log.debug("Failed to shut down listener executor during shutdown", exc_info=True)


def register_event_handlers(app: FastAPI) -> None:
    app.router.lifespan_context = _lifespan
