"""Unit tests for CameraService (app/services/camera_service.py)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from aiohttp import ClientResponseError
from app.models.camera import CameraConfig, OfferRequest, DepthRequest


def _cfg(**overrides):
    defaults = dict(ip="10.0.0.1", port=9999, timeout=5, max_streams=4,
                    enable_depth=True, enable_overlay=True)
    defaults.update(overrides)
    return CameraConfig(**defaults)


@pytest.fixture
def service():
    from app.services.camera_service import CameraService
    return CameraService(_cfg())


# ── context manager ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_aenter_creates_session(service):
    async with service as svc:
        assert svc.session is not None
    # after aexit, session is closed
    assert svc.session is not None  # object exists, but closed


@pytest.mark.asyncio
async def test_aexit_without_session():
    from app.services.camera_service import CameraService
    svc = CameraService(_cfg())
    # __aexit__ without __aenter__ should not blow up
    await svc.__aexit__(None, None, None)


# ── check_connection ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_check_connection_success(service):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    service.session = mock_session

    assert await service.check_connection() is True
    assert service._connection_status is True
    assert service.last_ping is not None


@pytest.mark.asyncio
async def test_check_connection_failure(service):
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    service.session = mock_session

    assert await service.check_connection() is False


@pytest.mark.asyncio
async def test_check_connection_no_session(service):
    service.session = None
    assert await service.check_connection() is False


@pytest.mark.asyncio
async def test_check_connection_exception(service):
    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=Exception("timeout"))
    service.session = mock_session

    assert await service.check_connection() is False
    assert service._connection_status is False


# ── get_status ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_status(service):
    service.check_connection = AsyncMock(return_value=True)
    service.last_ping = datetime(2026, 1, 1, 12, 0)
    service.active_streams = 3

    status = await service.get_status()
    assert status.connected is True
    assert status.ip == "10.0.0.1"
    assert status.port == 9999
    assert status.active_streams == 3
    assert "2026" in status.last_ping


@pytest.mark.asyncio
async def test_get_status_no_ping(service):
    service.check_connection = AsyncMock(return_value=False)
    service.last_ping = None

    status = await service.get_status()
    assert status.connected is False
    assert status.last_ping is None


# ── create_webrtc_offer ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_webrtc_offer_success(service):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"sdp": "answer", "type": "answer"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    service.session = mock_session

    offer = OfferRequest(sdp="offer_sdp", type="offer")
    result = await service.create_webrtc_offer(offer)
    assert result["type"] == "answer"
    assert service.active_streams == 1


@pytest.mark.asyncio
async def test_create_webrtc_offer_no_session(service):
    service.session = None
    offer = OfferRequest(sdp="x", type="offer")
    with pytest.raises(Exception, match="не инициализирована"):
        await service.create_webrtc_offer(offer)


@pytest.mark.asyncio
async def test_create_webrtc_offer_error_status(service):
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Internal Error")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    service.session = mock_session

    offer = OfferRequest(sdp="x", type="offer")
    with pytest.raises(Exception, match="Camera error 500"):
        await service.create_webrtc_offer(offer)


# ── create_overlay_offer ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_overlay_offer_success(service):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"sdp": "ans", "type": "answer"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    service.session = mock_session

    offer = OfferRequest(sdp="s", type="offer")
    result = await service.create_overlay_offer(offer)
    assert result["type"] == "answer"
    assert service.active_streams == 1


@pytest.mark.asyncio
async def test_create_overlay_offer_no_session(service):
    service.session = None
    with pytest.raises(Exception):
        await service.create_overlay_offer(OfferRequest(sdp="s", type="offer"))


@pytest.mark.asyncio
async def test_create_overlay_offer_error_body_unreadable(service):
    """When response.text() also throws, fallback to '<no body>'."""
    mock_resp = AsyncMock()
    mock_resp.status = 502
    mock_resp.text = AsyncMock(side_effect=Exception("read failed"))
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    service.session = mock_session

    with pytest.raises(Exception, match="Camera error 502"):
        await service.create_overlay_offer(OfferRequest(sdp="s", type="offer"))


# ── get_depth ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_depth_success(service):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"type": "depth", "x": 50.0, "y": 50.0, "depth": 1.23})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    service.session = mock_session

    result = await service.get_depth(DepthRequest(x=0.5, y=0.5))
    assert result.depth == 1.23


@pytest.mark.asyncio
async def test_get_depth_400_json_error(service):
    mock_resp = AsyncMock()
    mock_resp.status = 400
    mock_resp.json = AsyncMock(return_value={"error": "out of bounds"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    service.session = mock_session

    with pytest.raises(Exception, match="out of bounds"):
        await service.get_depth(DepthRequest(x=0.5, y=0.5))


@pytest.mark.asyncio
async def test_get_depth_400_text_fallback(service):
    mock_resp = AsyncMock()
    mock_resp.status = 400
    mock_resp.json = AsyncMock(side_effect=Exception("not json"))
    mock_resp.text = AsyncMock(return_value="Bad Request")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    service.session = mock_session

    with pytest.raises(Exception, match="Bad Request"):
        await service.get_depth(DepthRequest(x=0.5, y=0.5))


@pytest.mark.asyncio
async def test_get_depth_500(service):
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="server error")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    service.session = mock_session

    with pytest.raises(Exception, match="Camera error: 500"):
        await service.get_depth(DepthRequest(x=0.1, y=0.2))


@pytest.mark.asyncio
async def test_get_depth_no_session(service):
    service.session = None
    with pytest.raises(Exception, match="not initialized"):
        await service.get_depth(DepthRequest(x=0.1, y=0.1))


# ── submit_polygon ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_submit_polygon_success(service):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"ok": True})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    service.session = mock_session

    result = await service.submit_polygon({"points": []})
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_submit_polygon_error(service):
    mock_resp = AsyncMock()
    mock_resp.status = 422
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    service.session = mock_session

    with pytest.raises(Exception, match="422"):
        await service.submit_polygon({"points": []})


@pytest.mark.asyncio
async def test_submit_polygon_no_session(service):
    service.session = None
    with pytest.raises(Exception):
        await service.submit_polygon({})


# ── close_stream ───────────────────────────────────────────────────
def test_close_stream_decrements(service):
    service.active_streams = 3
    service.close_stream()
    assert service.active_streams == 2


def test_close_stream_at_zero(service):
    service.active_streams = 0
    service.close_stream()
    assert service.active_streams == 0
