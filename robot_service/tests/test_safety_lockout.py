"""Tests for safety lockout in robot_service —
check_safety_lockout() function and its integration with safe_getter / tasked_getter decorators.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp import ClientConnectionError

from exceptions import SafetyLockoutError
from safety.safety_kernel import SafetyKernel


# ── Helper: create a fresh kernel for safety-specific tests ───────

def _make_test_kernel(safety_state_url: str = "http://test:7905/safety/state") -> SafetyKernel:
    """Create a fresh SafetyKernel not affected by the autouse conftest patch."""
    return SafetyKernel(safety_state_url)


# ── check_safety_lockout unit tests ────────────────────────────────


@pytest.mark.asyncio
async def test_lockout_active_raises():
    """P0: nav2adapter reports safety_lockout=True → raises SafetyLockoutError."""
    kernel = _make_test_kernel()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"safety_lockout": True, "reason": "estop"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    with patch.object(kernel, "_get_session", return_value=mock_session):
        with pytest.raises(SafetyLockoutError, match="estop"):
            await kernel.authorize_motion_force("test")


@pytest.mark.asyncio
async def test_lockout_false_no_exception():
    """P0: nav2adapter reports safety_lockout=False → no exception."""
    kernel = _make_test_kernel()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"safety_lockout": False, "reason": None})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    with patch.object(kernel, "_get_session", return_value=mock_session):
        await kernel.authorize_motion_force("test")  # Should not raise


@pytest.mark.asyncio
async def test_nav2adapter_unreachable_fail_closed():
    """P0: network error → fail-closed (SafetyLockoutError raised)."""
    kernel = _make_test_kernel()

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=ClientConnectionError("unreachable"))
    mock_session.closed = False

    with patch.object(kernel, "_get_session", return_value=mock_session), \
         patch("safety.safety_kernel._SAFETY_FAIL_OPEN", False):
        with pytest.raises(SafetyLockoutError, match="Cannot verify safety"):
            await kernel.authorize_motion_force("test")


@pytest.mark.asyncio
async def test_nav2adapter_non_200_fail_closed():
    """P0: non-200 response → fail-closed (SafetyLockoutError raised)."""
    kernel = _make_test_kernel()

    mock_resp = AsyncMock()
    mock_resp.status = 503
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    with patch.object(kernel, "_get_session", return_value=mock_session), \
         patch("safety.safety_kernel._SAFETY_FAIL_OPEN", False):
        with pytest.raises(SafetyLockoutError, match="Cannot verify safety"):
            await kernel.authorize_motion_force("test")


@pytest.mark.asyncio
async def test_nav2adapter_unreachable_fail_open_override():
    """SAFETY_FAIL_OPEN=true + network error → no exception (legacy override)."""
    kernel = _make_test_kernel()

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=ClientConnectionError("unreachable"))
    mock_session.closed = False

    with patch.object(kernel, "_get_session", return_value=mock_session), \
         patch("safety.safety_kernel._SAFETY_FAIL_OPEN", True):
        await kernel.authorize_motion_force("test")  # Should not raise


# ── E-stop persistent state ──────────────────────────────────────


@pytest.mark.asyncio
async def test_estop_flag_blocks_safety_lockout():
    """H6: estop_active flag causes authorize_motion to raise immediately."""
    kernel = _make_test_kernel()
    kernel.set_estop(True)

    with pytest.raises(SafetyLockoutError, match="E-stop active"):
        await kernel.authorize_motion("test")


@pytest.mark.asyncio
async def test_estop_flag_cleared_does_not_block():
    """Normal lockout check proceeds when estop_active is False."""
    kernel = _make_test_kernel()
    kernel.set_estop(False)

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"safety_lockout": False})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    with patch.object(kernel, "_get_session", return_value=mock_session):
        await kernel.authorize_motion_force("test")  # Should not raise


@pytest.mark.asyncio
async def test_estop_flag_set_by_estop_endpoint(noop_startup, client):
    """H6: POST /tasks/estop sets estop flag via safety kernel."""
    from safety.safety_kernel import get_safety_kernel
    kernel = get_safety_kernel()
    kernel.set_estop(False)
    try:
        with patch("app.robot_scripts._stop_all_devices", new_callable=AsyncMock):
            resp = client.post("/tasks/estop")
        assert resp.status_code == 200
        assert kernel.estop_active is True
    finally:
        kernel.set_estop(False)


@pytest.mark.asyncio
async def test_estop_flag_cleared_by_full_recovery(noop_startup, client):
    """H6: Successful /safety/recover clears estop flag."""
    from safety.safety_kernel import get_safety_kernel
    kernel = get_safety_kernel()
    kernel.set_estop(True)

    try:
        with patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
                   return_value={"safety_lockout": False}), \
             patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock,
                   return_value={"enabled": True}), \
             patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock), \
             patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock), \
             patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock):
            resp = client.post("/safety/recover")
        assert resp.json()["success"] is True
        assert kernel.estop_active is False
    finally:
        kernel.set_estop(False)


@pytest.mark.asyncio
async def test_estop_flag_kept_on_partial_recovery(noop_startup, client):
    """H6: Partial recovery does NOT clear estop flag."""
    from safety.safety_kernel import get_safety_kernel
    kernel = get_safety_kernel()
    kernel.set_estop(True)

    try:
        with patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
                   return_value={"safety_lockout": False}), \
             patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock,
                   side_effect=RuntimeError("igus down")):
            resp = client.post("/safety/recover")
        assert resp.json()["success"] is False
        assert kernel.estop_active is True
    finally:
        kernel.set_estop(False)


# ── Decorator integration tests ────────────────────────────────────
# These test that safe_getter / tasked_getter correctly catch SafetyLockoutError → HTTP 423


@pytest.mark.asyncio
async def test_safe_getter_returns_423_on_lockout(noop_startup, client):
    """P1: safe_getter catches SafetyLockoutError → HTTP 423 with safety_lockout flag."""
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
    with patch("routes.decorators.check_safety_lockout", side_effect=SafetyLockoutError("relay_open")):
        resp = client.post("/move/to_product", json={
            "product_id": "PRODUCT_1",
            "location": {"x_m": 0, "y_m": 0, "theta_deg": 0, "map_id": 0},
            "velocity_percent": 50,
        })
    assert resp.status_code == 423
    body = resp.json()
    assert body["detail"]["safety_lockout"] is True
