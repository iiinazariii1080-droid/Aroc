import os
import time
import json
import asyncio
import pytest

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None


@pytest.mark.asyncio
async def test_ws_connect_basic():
    if websockets is None:
        pytest.skip("websockets package not available")

    url = os.getenv("XARM_WS_URL") or os.getenv("WS_URL")
    if not url:
        pytest.skip("XARM_WS_URL/WS_URL is not set; skipping live websocket test")

    ws = None
    try:
        # Small timeout to avoid hanging CI runs
        ws = await asyncio.wait_for(websockets.connect(url, ping_interval=None), timeout=5.0)
        assert ws.open is True

        # Send a lightweight ping frame compatible with our client heartbeat
        payload = {"type": "ping", "ts": time.time()}
        await asyncio.wait_for(ws.send(json.dumps(payload)), timeout=2.0)

        # Optionally try to receive one message without asserting its content
        try:
            _ = await asyncio.wait_for(ws.recv(), timeout=2.0)
        except Exception:
            # It's fine if server doesn't send anything immediately
            pass
    finally:
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=2.0)
            except Exception:
                pass


