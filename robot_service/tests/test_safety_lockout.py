"""Tests for safety lockout in robot_service —
check_safety_lockout() function and its integration with safe_getter / tasked_getter decorators.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp import ClientConnectionError

from exceptions import SafetyLockoutError


# ── check_safety_lockout unit tests ────────────────────────────────


@pytest.mark.asyncio
async def test_lockout_active_raises():
    """P0: nav2adapter reports safety_lockout=True → raises SafetyLockoutError."""
    from routes.decorators import check_safety_lockout

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"safety_lockout": True, "reason": "estop"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.decorators.aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(SafetyLockoutError, match="estop"):
            await check_safety_lockout()


@pytest.mark.asyncio
async def test_lockout_false_no_exception():
    """P0: nav2adapter reports safety_lockout=False → no exception."""
    from routes.decorators import check_safety_lockout

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"safety_lockout": False, "reason": None})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.decorators.aiohttp.ClientSession", return_value=mock_session):
        await check_safety_lockout()  # Should not raise


@pytest.mark.asyncio
async def test_nav2adapter_unreachable_fail_open():
    """P0: network error → fail open (no exception raised)."""
    from routes.decorators import check_safety_lockout

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=ClientConnectionError("unreachable"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.decorators.aiohttp.ClientSession", return_value=mock_session):
        await check_safety_lockout()  # Should not raise


@pytest.mark.asyncio
async def test_nav2adapter_non_200_fail_open():
    """P0: non-200 response → fail open (no exception raised)."""
    from routes.decorators import check_safety_lockout

    mock_resp = AsyncMock()
    mock_resp.status = 503
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.decorators.aiohttp.ClientSession", return_value=mock_session):
        await check_safety_lockout()  # Should not raise


# ── Decorator integration tests ────────────────────────────────────
# These test that safe_getter / tasked_getter correctly catch SafetyLockoutError → HTTP 423


@pytest.mark.asyncio
async def test_safe_getter_returns_423_on_lockout(noop_startup, client):
    """P1: safe_getter catches SafetyLockoutError → HTTP 423 with safety_lockout flag."""
    # Patch where the name is used (robot.py imported it from decorators)
    with patch("routes.robot.check_safety_lockout", side_effect=SafetyLockoutError("estop")):
        resp = client.post("/joystick/frame", json={
            "ts": 1000, "axes": [0, 0, 0, 0], "buttons": [0]*19, "ttl": 200,
        })
    assert resp.status_code == 423
    body = resp.json()
    assert body["detail"]["safety_lockout"] is True


@pytest.mark.asyncio
async def test_tasked_getter_returns_423_on_lockout(noop_startup, client):
    """P1: tasked_getter catches SafetyLockoutError → HTTP 423."""
    # tasked_getter calls check_safety_lockout from within decorators module
    with patch("routes.decorators.check_safety_lockout", side_effect=SafetyLockoutError("relay_open")):
        resp = client.post("/move/to_product", json={
            "product_id": "PRODUCT_1",
            "location": {"x_m": 0, "y_m": 0, "theta_deg": 0, "map_id": 0},
            "velocity_percent": 50,
        })
    assert resp.status_code == 423
    body = resp.json()
    assert body["detail"]["safety_lockout"] is True
