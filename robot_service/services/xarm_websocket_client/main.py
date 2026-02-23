import asyncio
import logging
from connection_manager import ConnectionManager
from message_router import MessageRouter
from safety_layer import SafetyLayer
from manipulator_commands import ManipulatorCommands
from response_manager import ResponseManager
from config import load_config, load_robot_config_file, build_ws_url_from_ip


async def main():
    # === STEP 1. Callbacks ===
    async def send_stop(reason: str):
        """Send STOP command on safety trigger"""
        logging.warning(f"STOP triggered: {reason}")
        await commands.stop(reason)

    async def forward_command(msg):
        """Forward validated commands downstream"""
        logging.debug("Forwarded safe command: %s", msg)

    connected_event = asyncio.Event()

    async def on_connect():
        logging.info("Connected to manipulator")
        connected_event.set()

    async def on_disconnect():
        logging.info("Disconnected")

    # === STEP 0. Logging & Config ===
    cfg = load_config()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

    # === STEP 2. Safety Layer ===
    safety = SafetyLayer(
        send_stop=send_stop,
        forward_command=forward_command,
        watchdog_timeout=cfg.watchdog_timeout,
        hold_timeout=cfg.hold_timeout
    )

    # === STEP 3. Message Router ===
    resp_manager = ResponseManager()

    router = MessageRouter(
        on_report=safety.handle_message,
        on_response=lambda msg: logging.debug("Response: %s", msg),
        on_invalid=lambda info: asyncio.create_task(send_stop("invalid_message")),
        response_manager=resp_manager
    )

    # === STEP 4. Connection Manager ===
    async def on_message(msg):
        await router.route(msg)

    # Prefer hardcoded/cloud URL at runtime, but allow optional robot_config.json to override if desired
    robot_ip = load_robot_config_file()
    ws_url = cfg.ws_url if not robot_ip else build_ws_url_from_ip(robot_ip)

    cm = ConnectionManager(
        ws_url,
        on_message=on_message,
        on_connect=on_connect,
        on_disconnect=on_disconnect
    )
    cm.heartbeat_interval = cfg.heartbeat_interval

    # === STEP 5. Commands API ===
    global commands
    commands = ManipulatorCommands(cm.send, user_id="test", version="xarm6", stop_cmd_name="emergency_stop", response_manager=resp_manager)

    # === STEP 6. Start ===
    asyncio.create_task(cm.connect())
    await connected_event.wait()

    # === STEP 7. Demo scenario ===
    logging.info("Sending test commands")
    await commands.move_step("position-z-decrease", acc=500)
    await asyncio.sleep(0.5)
    await commands.move_step_over()
    await asyncio.sleep(0.5)
    await commands.stop("end_of_test")

    # Keep running so the connection stays alive
    logging.info("Idle; keeping connection alive. Press Ctrl+C to exit.")
    try:
        await asyncio.Event().wait()
    finally:
        await cm.close()
        await safety.stop()


if __name__ == "__main__":
    asyncio.run(main())
