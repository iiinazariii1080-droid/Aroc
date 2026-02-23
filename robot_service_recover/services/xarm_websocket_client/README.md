## xarm_websocket_client — xArm WebSocket client

Lightweight async Python module for communicating with the xArm controller over WebSocket. Includes reliable connection with auto-reconnect, message routing, safety layer (watchdog/deadman), manipulator command API and response manager.

### Architecture

```
   External application
          |
          v
  +--------------------+
  |  ConnectionManager |<=======> WebSocket server (xArm/gateway)
  +---------+----------+
            | incoming raw messages
            v
  +--------------------+     +------------------+
  |   MessageRouter    |---->|  ResponseManager |
  +---------+----------+     +------------------+
            | report                          ^
            v                                 |
  +--------------------+       request         |
  |    SafetyLayer     |-----------------------+
  +---------+----------+
            |
            v
  +--------------------+
  | ManipulatorCommands|
  +--------------------+
```

Main components:
- **ConnectionManager**: holds WebSocket, auto-reconnect with jitter, heartbeat, processing queue.
- **MessageRouter**: normalizes incoming messages (including list→report), validates and dispatches by type.
- **SafetyLayer**: watchdog/TTL/deadman, emits STOP on anomalies.
- **ManipulatorCommands**: high-level xArm commands (move_step, move_step_over, stop, ping).
- **ResponseManager**: request↔response correlation by id, timeouts, error on code≠0.

### Requirements
- Python 3.9+
- Dependencies: websockets (for run/library), pytest/pytest-asyncio (for tests)

### Installation

```
pip install websockets pytest pytest-asyncio
```

### Quick start (standalone)

Run the built-in example for connection and sending test commands:

```
python main.py
```

By default the cloud URL from config is used (see below). Logging is configured via the `LOG_LEVEL` environment variable.

### Configuration

The module reads configuration from the environment and an optional file. Main environment variables:
- `WS_URL` — default WebSocket URL (used when running `main.py` if no IP is set in the file).
- `LOG_LEVEL` — log level (`DEBUG`, `INFO`, `WARNING`, ...).
- `WATCHDOG_TIMEOUT` — message silence timeout, seconds.
- `HOLD_TIMEOUT` — max hold time for `move_step_over`, seconds.
- `HEARTBEAT_INTERVAL` — heartbeat period, seconds.
- `RECONNECT_MAX_DELAY` — max backoff delay, seconds.

You can also set the robot IP via a `robot_config.json` file next to the app:

```
{
  "robot_ip": "192.168.1.50"
}
```

If the file exists and contains `robot_ip`, `main.py` will build a URL like `ws://<IP>:18333/ws?...` and connect directly. Otherwise `WS_URL` from the environment or default is used.

### Use as a library

Minimal async integration example:

```python
import asyncio
import logging
from connection_manager import ConnectionManager
from message_router import MessageRouter
from safety_layer import SafetyLayer
from manipulator_commands import ManipulatorCommands
from response_manager import ResponseManager

async def main():
    logging.basicConfig(level=logging.INFO)

    async def send_stop(reason: str):
        logging.warning("STOP: %s", reason)

    async def forward(msg):
        logging.debug("forward: %s", msg)

    safety = SafetyLayer(send_stop=send_stop, forward_command=forward, watchdog_timeout=2.0, hold_timeout=0.5)
    responses = ResponseManager()

    async def on_message(msg):
        await router.route(msg)

    router = MessageRouter(on_report=safety.handle_message, response_manager=responses)

    cm = ConnectionManager("ws://host:18333/ws?channel=prod&lang=en&v=1&id=test", on_message=on_message)
    cmds = ManipulatorCommands(cm.send, user_id="test", version="xarm6", response_manager=responses)

    asyncio.create_task(cm.connect())
    await asyncio.sleep(0.5)

    # send and wait for response
    resp = await cmds.move_step("attitude-pitch-decrease", acc=500, wait_response=True, timeout=3.0)
    print("response:", resp)

    await cm.close()
    await safety.stop()

asyncio.run(main())
```

### Safety layer

```
   report/response ---> SafetyLayer
           |             - TTL check (only if data is dict)
           |             - Watchdog (silence > WATCHDOG_TIMEOUT)
           |             - Deadman (hold > HOLD_TIMEOUT)
           v
         STOP
```

- Watchdog is updated by incoming report messages (e.g. `devices_status_report`).
- When timeouts are exceeded, `send_stop("watchdog_timeout" | "deadman_release" | "expired_command")` is called.

### Connection reliability

```
 CONNECT -> LISTEN -> ERROR -> BACKOFF (1..max, with jitter) -> CONNECT ...
              |                               ^
              +-- heartbeat (periodic ping)
```

- Incoming messages are queued; a separate worker processes them (reduces impact of slow callbacks).
- On queue overflow the module drops the oldest item and logs a warning.

### Logging

- Standard `logging` is used. Example for verbose log:

```
set LOG_LEVEL=DEBUG
python main.py
```

### Testing

Run all tests:

```
python -m pytest -q
```

Coverage includes unit tests (`SafetyLayer`, `MessageRouter`, `ManipulatorCommands`, `ResponseManager`) and e2e tests with a mock server (connect/reconnect, command responses, report load, parallel requests).
