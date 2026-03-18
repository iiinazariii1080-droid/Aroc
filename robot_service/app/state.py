import os
import sys
import asyncio
import logging
from typing import Any, Optional

from fastapi import FastAPI

from . import config as app_config
from app.config import (
    JOYSTICK_DEADZONE,
    JOYSTICK_DEFAULT_TTL_MS,
    JOYSTICK_HOLD_TIMEOUT_MS,
    JOYSTICK_LIFT_JOG_SPEED,
    JOYSTICK_LIFT_JOG_TTL_MS,
    JOYSTICK_MOVE_ACC,
    JOYSTICK_IS_MOVE_TOOL,
    JOYSTICK_MODE,
    IGUS_CONTAINER_IP,
    IGUS_CONTAINER_PORT,
    SYMOVO_TELEOP_MOVE_URL,
    SYMOVO_TELEOP_DURATION,
    SYMOVO_TELEOP_LINEAR,
    SYMOVO_TELEOP_ANGULAR,
    XARM_CONTAINER_IP,
    XARM_CONTAINER_PORT,
)
from services.joystick_pipeline import JoystickPipeline
from services.joystick_scheduler import JoystickScheduler
from services.joystick_ingress import JoystickIngress


_LOGGER = logging.getLogger(__name__)


async def startup(app: FastAPI) -> None:
    """Initialize shared services on app startup, including xArm WS client."""
    try:
        # Ensure xarm_websocket_client package can import its siblings using simple imports
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        xarm_pkg_dir = os.path.join(base_dir, "services", "xarm_websocket_client")
        if xarm_pkg_dir not in sys.path:
            sys.path.insert(0, xarm_pkg_dir)

        # Lazy imports to avoid hard dependency if not used
        from config import load_config, load_robot_config_file, build_ws_url_from_ip  # type: ignore
        from response_manager import ResponseManager  # type: ignore
        from message_router import MessageRouter  # type: ignore
        from connection_manager import ConnectionManager  # type: ignore
        from manipulator_commands import ManipulatorCommands  # type: ignore
        from safety_layer import SafetyLayer  # type: ignore

        cfg = load_config()

        connected_event: asyncio.Event = asyncio.Event()

        async def send_stop(reason: str):
            try:
                if getattr(app.state, "xarm_commands", None) is not None:
                    await app.state.xarm_commands.stop(reason)
            except Exception as e:
                _LOGGER.warning("Failed to send stop: %s", e)

        async def forward_command(msg: dict):
            try:
                # Store last full message for manipulator reports
                if isinstance(msg, dict) and msg.get("type") == "report" and msg.get("cmd") == "devices_status_report":
                    from app import xarm_status
                    xarm_status.set_status(msg)
            except Exception as e:
                _LOGGER.debug("forward_command store status failed: %s", e)

        async def on_connect():
            _LOGGER.info("Connected to xArm gateway")
            connected_event.set()

        async def on_disconnect():
            _LOGGER.info("Disconnected from xArm gateway")

        # Safety layer processes incoming reports (watchdog/deadman if used by reports)
        safety = SafetyLayer(
            send_stop=send_stop,
            forward_command=forward_command,
            watchdog_timeout=cfg.watchdog_timeout,
            hold_timeout=cfg.hold_timeout,
        )

        responses = ResponseManager()

        async def on_message(msg: Any):
            try:
                await app.state.xarm_router.route(msg)
            except Exception as e:
                _LOGGER.warning("Router error: %s", e)

        app.state.xarm_router = MessageRouter(
            on_report=safety.handle_message,
            on_response=None,
            on_invalid=lambda info: asyncio.create_task(send_stop("invalid_message")),
            response_manager=responses,
        )

        robot_ip = load_robot_config_file()
        ws_url = cfg.ws_url if not robot_ip else build_ws_url_from_ip(robot_ip)

        app.state.xarm_cm = ConnectionManager(ws_url, on_message=on_message, on_connect=on_connect, on_disconnect=on_disconnect)
        app.state.xarm_cm.heartbeat_interval = cfg.heartbeat_interval
        app.state.xarm_cm_task = asyncio.create_task(app.state.xarm_cm.connect())

        app.state.xarm_commands = ManipulatorCommands(app.state.xarm_cm.send, user_id="joystick", version="xarm6", stop_cmd_name="emergency_stop", response_manager=responses)
        app.state.xarm_safety = safety

        # Inject xarm WS commands into robot_scripts for emergency_stop
        import app.robot_scripts as _rs
        _rs.set_xarm_commands(app.state.xarm_commands)

        # Session state for manual joystick control
        app.state.manual_active_lock = asyncio.Lock()
        app.state.manual_active: bool = False

        # Joystick pipeline (background worker)
        from services.xarm_service import XarmManipulatorClient
        from services.igus_service import IgusMotorClient
        import app.robot_scripts as robot_scripts
        manipulator_base_url = f"http://{XARM_CONTAINER_IP}:{XARM_CONTAINER_PORT}/api/v1/xarm/manipulator"
        igus_base_url = f"http://{IGUS_CONTAINER_IP}:{IGUS_CONTAINER_PORT}"
        app.state.xarm_http = XarmManipulatorClient(manipulator_base_url)
        app.state.igus_http = IgusMotorClient(igus_base_url)
        app.state.joystick_pipeline = JoystickPipeline(
            app.state.xarm_commands,
            xarm_http=app.state.xarm_http,
            igus_client=app.state.igus_http,
            autotake_func=robot_scripts.autotake,
            autotake_velocity=40,
            safety_layer=safety,
            deadzone=JOYSTICK_DEADZONE,
            default_ttl_ms=JOYSTICK_DEFAULT_TTL_MS,
            hold_timeout_ms=JOYSTICK_HOLD_TIMEOUT_MS,
            lift_jog_speed=JOYSTICK_LIFT_JOG_SPEED,
            lift_jog_ttl_ms=JOYSTICK_LIFT_JOG_TTL_MS,
            acc=JOYSTICK_MOVE_ACC,
            is_move_tool=JOYSTICK_IS_MOVE_TOOL,
            mode=JOYSTICK_MODE,
            symovo_teleop_url=SYMOVO_TELEOP_MOVE_URL or None,
            symovo_teleop_duration=SYMOVO_TELEOP_DURATION,
            symovo_linear=SYMOVO_TELEOP_LINEAR,
            symovo_angular=SYMOVO_TELEOP_ANGULAR,
        )
        await app.state.joystick_pipeline.start()

        app.state.joystick_scheduler = JoystickScheduler(
            app.state.joystick_pipeline.submit,
            max_rate_hz=25.0,
        )
        await app.state.joystick_scheduler.start()

        app.state.joystick_ingress = JoystickIngress(
            app.state.joystick_scheduler.submit,
        )
        await app.state.joystick_ingress.start()

        # Wait a bit for connection, but don't block startup forever
        try:
            await asyncio.wait_for(connected_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            _LOGGER.warning("xArm WS connection not ready yet; continuing startup")
    except Exception as e:
        _LOGGER.warning("xArm client initialization failed: %s", e)


async def shutdown(app: FastAPI) -> None:
    """Gracefully close connections on shutdown."""
    try:
        safety = getattr(app.state, "xarm_safety", None)
        if safety is not None:
            try:
                await safety.stop()
            except Exception:
                pass

        cm = getattr(app.state, "xarm_cm", None)
        if cm is not None:
            try:
                await cm.close()
            except Exception:
                pass
        ingress = getattr(app.state, "joystick_ingress", None)
        if ingress is not None:
            try:
                await ingress.stop()
            except Exception:
                pass

        scheduler = getattr(app.state, "joystick_scheduler", None)
        if scheduler is not None:
            try:
                await scheduler.stop()
            except Exception:
                pass

        jp = getattr(app.state, "joystick_pipeline", None)
        if jp is not None:
            try:
                await jp.stop()
            except Exception:
                pass

        igus_http = getattr(app.state, "igus_http", None)
        if igus_http is not None:
            try:
                await igus_http.aclose()
            except Exception:
                pass
    except Exception as e:
        _LOGGER.warning("Shutdown cleanup error: %s", e)

