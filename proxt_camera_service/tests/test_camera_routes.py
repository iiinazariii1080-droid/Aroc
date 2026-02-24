"""Tests for camera routes (app/routes/camera.py)."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.models.camera import CameraStatus, DepthResponse, OfferRequest


def _make_mock_service(**overrides):
    svc = AsyncMock()
    svc.get_status = AsyncMock(return_value=CameraStatus(
        connected=True, ip="10.0.0.1", port=9999,
        last_ping="2026-01-01T12:00:00", active_streams=1,
    ))
    svc.create_overlay_offer = AsyncMock(return_value={"sdp": "ans", "type": "answer"})
    svc.get_depth = AsyncMock(return_value=DepthResponse(
        type="depth", x=50.0, y=50.0, depth=2.5,
    ))
    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


@pytest.fixture
def patched_client(app):
    """Override get_camera_service dependency with a mock."""
    from routes.camera import get_camera_service
    mock_svc = _make_mock_service()

    async def _override():
        return mock_svc

    app.dependency_overrides[get_camera_service] = _override
    yield app, mock_svc
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_camera_status(patched_client, monkeypatch):
    """Status endpoint uses module-level camera_service (not DI)."""
    app, mock_svc = patched_client
    import routes.camera as _rcam
    monkeypatch.setattr(_rcam, "camera_service", mock_svc)
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/depth_camera/status")
    assert r.status_code == 200
    body = r.json()
    assert "connected" in body


@pytest.mark.asyncio
async def test_get_camera_status_error(app, monkeypatch):
    """Status endpoint propagates service exceptions."""
    broken_svc = AsyncMock()
    broken_svc.get_status = AsyncMock(side_effect=RuntimeError("boom"))
    import routes.camera as _rcam
    monkeypatch.setattr(_rcam, "camera_service", broken_svc)
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/depth_camera/status")
    # main.py status_check catches exception and falls back; so we get a 200 with fallback data
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_overlay_offer_success(patched_client):
    app, _ = patched_client
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/depth_camera/overlay_offer", json={
            "sdp": "offer_sdp", "type": "offer",
        })
    assert r.status_code == 200
    assert r.json()["type"] == "answer"


@pytest.mark.asyncio
async def test_overlay_offer_error(app):
    from routes.camera import get_camera_service

    async def _broken():
        svc = AsyncMock()
        svc.create_overlay_offer = AsyncMock(side_effect=RuntimeError("cam offline"))
        return svc

    app.dependency_overrides[get_camera_service] = _broken
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/depth_camera/overlay_offer", json={
            "sdp": "x", "type": "offer",
        })
    assert r.status_code == 500
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_depth_success(patched_client):
    app, _ = patched_client
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/depth_camera/depth", params={
            "message": json.dumps({"x": 50, "y": 50}),
        })
    assert r.status_code == 200
    body = r.json()
    assert "depth" in body


@pytest.mark.asyncio
async def test_get_depth_invalid_json(patched_client):
    app, _ = patched_client
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/depth_camera/depth", params={"message": "not-json"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_depth_service_error(app):
    from routes.camera import get_camera_service

    async def _broken():
        svc = AsyncMock()
        svc.get_depth = AsyncMock(side_effect=RuntimeError("no depth"))
        return svc

    app.dependency_overrides[get_camera_service] = _broken
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/depth_camera/depth", params={
            "message": json.dumps({"x": 50, "y": 50}),
        })
    assert r.status_code == 500
    app.dependency_overrides.clear()
