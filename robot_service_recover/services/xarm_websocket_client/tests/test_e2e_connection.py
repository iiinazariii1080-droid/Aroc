import asyncio
import json
import socket
from contextlib import closing
import contextlib

import pytest
import websockets

from connection_manager import ConnectionManager
from message_router import MessageRouter
from manipulator_commands import ManipulatorCommands
from response_manager import ResponseManager


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _make_status_report() -> dict:
    return {
        "cmd": "devices_status_report",
        "type": "report",
        "data": [True, "x", "x", False, 0, [2, 4, 0]],
    }


@pytest.mark.asyncio
async def test_e2e_basic_reports_and_response():
    port = _find_free_port()
    requests_seen = []

    async def handler(ws):
        async def sender():
            # send a few reports
            for _ in range(5):
                await ws.send(json.dumps(_make_status_report()))
                await asyncio.sleep(0.05)
        send_task = asyncio.create_task(sender())

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "request":
                    requests_seen.append(msg)
                    # echo response with same id
                    resp = {"type": "response", "id": msg.get("id"), "code": 0, "data": None}
                    await ws.send(json.dumps(resp))
        except websockets.ConnectionClosed:
            pass
        finally:
            send_task.cancel()

    server = await websockets.serve(handler, "127.0.0.1", port, ping_interval=None)

    received_reports = []
    received_responses = []
    connect_count = 0

    async def on_report(msg):
        received_reports.append(msg)

    async def on_response(msg):
        received_responses.append(msg)

    rm = ResponseManager()
    router = MessageRouter(on_report=on_report, on_response=on_response, response_manager=rm)

    async def on_message(msg):
        await router.route(msg)

    async def on_connect():
        nonlocal connect_count
        connect_count += 1

    cm = ConnectionManager(f"ws://127.0.0.1:{port}", on_message=on_message, on_connect=on_connect)
    commands = ManipulatorCommands(cm.send, user_id="test", version="xarm6", response_manager=rm)

    connect_task = asyncio.create_task(cm.connect())

    try:
        # wait for first report to flow through
        async def wait_for_reports():
            while len(received_reports) < 1:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_for_reports(), timeout=2.0)

        # send a request and wait for response
        await commands.move_step("attitude-pitch-decrease", acc=500)

        async def wait_for_response():
            while len(received_responses) < 1:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_for_response(), timeout=2.0)

        assert connect_count >= 1
        assert len(received_reports) >= 1
        assert len(requests_seen) >= 1
        assert received_responses[-1]["type"] == "response"
        assert received_responses[-1].get("code", 0) == 0
    finally:
        await cm.close()
        connect_task.cancel()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_e2e_reconnect_on_server_close():
    port = _find_free_port()

    # First connection: send a couple of reports, then close to force reconnect
    async def handler(ws):
        try:
            for i in range(3):
                await ws.send(json.dumps(_make_status_report()))
                await asyncio.sleep(0.05)
            await ws.close()
        except websockets.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", port, ping_interval=None)

    connect_events = []
    reports_total = 0

    async def on_message(_):
        nonlocal reports_total
        reports_total += 1

    async def on_connect():
        connect_events.append("connect")

    cm = ConnectionManager(f"ws://127.0.0.1:{port}", on_message=on_message, on_connect=on_connect)
    connect_task = asyncio.create_task(cm.connect())

    try:
        # Wait for two connections: initial and after reconnect
        async def wait_for_two_connects():
            while len(connect_events) < 2:
                await asyncio.sleep(0.05)
        await asyncio.wait_for(wait_for_two_connects(), timeout=6.0)
        assert len(connect_events) >= 2
        assert reports_total >= 3  # received some reports before disconnect
    finally:
        await cm.close()
        connect_task.cancel()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_e2e_move_sequence_responses():
    port = _find_free_port()

    async def handler(ws):
        spam_task = None
        try:
            # continuously send status reports
            async def spam_reports():
                while True:
                    await ws.send(json.dumps(_make_status_report()))
                    await asyncio.sleep(0.03)
            spam_task = asyncio.create_task(spam_reports())

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "request":
                    resp = {"type": "response", "id": msg.get("id"), "code": 0, "data": None}
                    await ws.send(json.dumps(resp))
        except websockets.ConnectionClosed:
            pass
        finally:
            if spam_task:
                spam_task.cancel()
                with contextlib.suppress(Exception):
                    await spam_task

    server = await websockets.serve(handler, "127.0.0.1", port, ping_interval=None)

    received_responses = []

    async def on_response(msg):
        received_responses.append(msg)

    router = MessageRouter(on_response=on_response)

    async def on_message(msg):
        await router.route(msg)

    cm = ConnectionManager(f"ws://127.0.0.1:{port}", on_message=on_message)
    commands = ManipulatorCommands(cm.send, user_id="test", version="xarm6")

    connect_task = asyncio.create_task(cm.connect())

    try:
        # wait for connection to stabilize
        await asyncio.sleep(0.2)

        # sequence of moves
        directions = [
            "attitude-pitch-decrease",
            "attitude-roll-increase",
            "attitude-pitch-increase",
        ]
        for d in directions:
            await commands.move_step(d, acc=500, wait_response=True, timeout=1.0)
            # small spacing
            await asyncio.sleep(0.02)

        # and a hold
        await commands.move_step_over(wait_response=True, timeout=1.0)

        async def wait_for_n_responses(n):
            while len(received_responses) < n:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_n_responses(len(directions) + 1), timeout=3.0)

        # Validate responses correspond to sends (ids should be sequential starting at current seq base)
        assert len(received_responses) >= len(directions) + 1
        assert all(r.get("type") == "response" and r.get("code") == 0 for r in received_responses[-(len(directions)+1):])
    finally:
        await cm.close()
        connect_task.cancel()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_e2e_parallel_wait_responses_and_load_reports():
    port = _find_free_port()

    # server: spam reports at high rate and echo responses
    async def handler(ws):
        async def spam_reports():
            try:
                for _ in range(400):  # ~12s at 0.03s each would be slow; we use faster
                    await ws.send(json.dumps(_make_status_report()))
                    await asyncio.sleep(0.005)
            except Exception:
                pass
        spam_task = asyncio.create_task(spam_reports())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "request":
                    await ws.send(json.dumps({"type": "response", "id": msg.get("id"), "code": 0, "data": None}))
        except websockets.ConnectionClosed:
            pass
        finally:
            spam_task.cancel()

    server = await websockets.serve(handler, "127.0.0.1", port, ping_interval=None)

    rm = ResponseManager()
    router = MessageRouter(response_manager=rm)

    async def on_message(msg):
        await router.route(msg)

    cm = ConnectionManager(f"ws://127.0.0.1:{port}", on_message=on_message)
    commands = ManipulatorCommands(cm.send, user_id="test", version="xarm6", response_manager=rm)

    connect_task = asyncio.create_task(cm.connect())
    try:
        await asyncio.sleep(0.2)
        # fire N parallel requests and wait for all
        coros = [
            commands.move_step("attitude-pitch-decrease", acc=500, wait_response=True, timeout=2.0)
            for _ in range(10)
        ]
        results = await asyncio.gather(*coros)
        assert len(results) == 10
        assert all(r.get("type") == "response" and r.get("code") == 0 for r in results)
    finally:
        await cm.close()
        connect_task.cancel()
        server.close()
        await server.wait_closed()


