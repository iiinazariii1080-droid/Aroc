import asyncio
import json
import pytest
import websockets

from connection_manager import ConnectionManager


@pytest.mark.asyncio
async def test_send_during_close_does_not_raise():
    # simple echo server that keeps connection alive briefly
    async def handler(ws):
        try:
            async for raw in ws:
                await ws.send(raw)
        except websockets.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0, ping_interval=None)
    port = server.sockets[0].getsockname()[1]

    cm = ConnectionManager(f"ws://127.0.0.1:{port}")
    task = asyncio.create_task(cm.connect())

    try:
        # wait a bit for connection
        await asyncio.sleep(0.1)
        # Schedule a send and immediately close
        async def do_send():
            await cm.send({"type": "ping", "ts": 1})
        send_t = asyncio.create_task(do_send())
        await cm.close()
        # Ensure send completion without exceptions
        await asyncio.wait_for(send_t, timeout=1.0)
    finally:
        task.cancel()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_connect_disconnect_callbacks_exceptions_are_caught():
    connect_called = asyncio.Event()
    disconnect_called = asyncio.Event()

    async def on_connect():
        connect_called.set()
        raise RuntimeError("boom-connect")

    async def on_disconnect():
        disconnect_called.set()
        raise RuntimeError("boom-disconnect")

    async def handler(ws):
        await asyncio.sleep(0.05)
        await ws.close()

    server = await websockets.serve(handler, "127.0.0.1", 0, ping_interval=None)
    port = server.sockets[0].getsockname()[1]

    cm = ConnectionManager(f"ws://127.0.0.1:{port}", on_connect=on_connect, on_disconnect=on_disconnect)
    task = asyncio.create_task(cm.connect())

    try:
        # Wait until both callbacks had a chance to run
        await asyncio.wait_for(connect_called.wait(), timeout=2.0)
        await asyncio.wait_for(disconnect_called.wait(), timeout=2.0)
        # If exceptions weren't caught, the connect loop would have crashed
        assert True
    finally:
        await cm.close()
        task.cancel()
        server.close()
        await server.wait_closed()
