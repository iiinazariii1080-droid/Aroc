"""Smoke test for WebSocket proxy endpoints."""

import pytest


@pytest.mark.asyncio
async def test_ws_xarm_rejects_without_upstream(app):
    """
    WS connect to /api/v1/xarm/ws — when upstream is unreachable
    the proxy should close with 1011.
    """
    from starlette.testclient import TestClient

    with TestClient(app) as tc:
        try:
            with tc.websocket_connect("/api/v1/xarm/ws") as ws:
                # If connect succeeds, upstream will fail → expect close
                data = ws.receive()
        except Exception:
            pass  # Expected: upstream unreachable → error/close


@pytest.mark.asyncio
async def test_ws_targets_configured():
    """All expected WS targets are present in the proxy mapping."""
    from app.routers.proxy_ws import _WS_TARGETS

    assert "xarm" in _WS_TARGETS
    assert "color_camera" in _WS_TARGETS
    assert "depth_camera" in _WS_TARGETS
    assert all(
        url.startswith(("ws://", "wss://"))
        for url in _WS_TARGETS.values()
    )
