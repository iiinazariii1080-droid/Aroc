#!/usr/bin/env python3
"""
Proof-of-concept: xArm WebSocket works WITHOUT any frontend/browser.
Connects directly to the UFactory Studio WebSocket proxy and reads telemetry.
Can also send commands (move_step, switch_mode, etc.)
"""

import asyncio
import json
import time
import websockets


WS_URL = "wss://api.techvisioncloud.pl/api/v1/xarm/ws"
WS_PARAMS = "channel=prod&lang=en&v=1&id={}"


async def listen_telemetry(duration_sec: float = 5.0):
    """Connect and print xArm telemetry for `duration_sec` seconds."""
    session_id = int(time.time() * 1000)
    url = f"{WS_URL}?{WS_PARAMS.format(session_id)}"

    print(f"Connecting to: {url}")
    print(f"(no browser, no frontend — pure Python)\n")

    async with websockets.connect(url) as ws:
        print("Connected! Receiving telemetry...\n")
        start = time.time()
        msg_count = 0

        while time.time() - start < duration_sec:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(raw)
                cmd = data.get("cmd", "?")
                msg_count += 1

                if cmd == "devices_info_report":
                    d = data["data"]
                    print(f"[INFO] type={d.get('xarm_type', d.get('server_is_xarm'))}  "
                          f"SN={d.get('xarm_sn', '?')}  "
                          f"fw={d.get('xarm_version', d.get('core_version', '?'))}  "
                          f"IP={d.get('xarm_port_name', '?')}")

                elif cmd == "devices_status_report":
                    d = data["data"]
                    tcp = d.get("xarm_tcp_pose", [])
                    joints = d.get("xarm_joint_pose", [])
                    state = d.get("xarm_state")
                    err = d.get("xarm_error_code", 0)
                    clients = d.get("current_clients_count", "?")
                    print(f"[STATUS] TCP={tcp}  joints={joints}  "
                          f"state={state}  err={err}  clients={clients}")

                elif cmd == "devices_status_keys_report":
                    keys = data["data"]
                    print(f"[KEYS] {len(keys)} telemetry fields available")

                else:
                    print(f"[{cmd}] (received)")

            except asyncio.TimeoutError:
                print("(no data for 2s)")

        print(f"\nDone. Received {msg_count} messages in {duration_sec}s.")


async def send_command_example(ws, cmd: str, data: dict):
    """Example: send a command to the xArm via WebSocket."""
    msg = {"cmd": cmd, "data": data, "id": str(int(time.time()))}
    print(f"\n>>> Sending: {json.dumps(msg)}")
    await ws.send(json.dumps(msg))
    response = await asyncio.wait_for(ws.recv(), timeout=3.0)
    print(f"<<< Response: {response}")
    return json.loads(response)


if __name__ == "__main__":
    print("=" * 60)
    print("xArm WebSocket — standalone (no browser/frontend)")
    print("=" * 60)
    asyncio.run(listen_telemetry(duration_sec=5.0))
