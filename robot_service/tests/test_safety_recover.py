"""Tests for POST /safety/recover — sequential recovery orchestrator."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────

def _mock_aiohttp_get(status: int = 200, json_data: dict | None = None, exc: Exception | None = None):
    """Returns a context-manager mock for aiohttp session.get()."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.text = AsyncMock(return_value="error")
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_aiohttp_put(status: int = 200, text: str = "ok"):
    """Returns a context-manager mock for aiohttp session.put()."""
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class _FakeSession:
    """Minimal aiohttp.ClientSession mock that routes .get() and .put()."""

    def __init__(self, get_resp=None, put_resp=None, get_exc=None, put_exc=None):
        self._get_resp = get_resp
        self._put_resp = put_resp
        self._get_exc = get_exc
        self._put_exc = put_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **kw):
        if self._get_exc:
            raise self._get_exc
        return self._get_resp

    def put(self, url, **kw):
        if self._put_exc:
            raise self._put_exc
        return self._put_resp


def _step_dict(steps: list[dict], name: str) -> dict | None:
    return next((s for s in steps if s["name"] == name), None)


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_all_steps_ok(noop_startup, client):
    """P0: all 4 recovery steps succeed → success=True, 5 steps all 'ok'."""
    get_resp = _mock_aiohttp_get(200, {"safety_lockout": False})
    put_resp = _mock_aiohttp_put(200)

    fake_session = _FakeSession(get_resp=get_resp, put_resp=put_resp)

    with (
        patch("routes.robot.aiohttp.ClientSession", return_value=fake_session),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock) as mock_igus,
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock) as mock_xarm_recover,
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock) as mock_xarm_enable,
    ):
        resp = client.post("/safety/recover")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["steps"]) == 5
    assert all(s["status"] == "ok" for s in body["steps"])
    mock_igus.assert_awaited_once()
    mock_xarm_recover.assert_awaited_once()
    mock_xarm_enable.assert_awaited_once()


@pytest.mark.asyncio
async def test_relay_still_open_immediate_failure(noop_startup, client):
    """P0: nav2adapter reports lockout active → immediate failure, no further steps."""
    get_resp = _mock_aiohttp_get(200, {"safety_lockout": True, "reason": "estop"})
    fake_session = _FakeSession(get_resp=get_resp)

    with (
        patch("routes.robot.aiohttp.ClientSession", return_value=fake_session),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock) as mock_igus,
    ):
        resp = client.post("/safety/recover")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "Safety relay still open" in body.get("message", "")
    mock_igus.assert_not_awaited()  # No further steps after relay check failure


@pytest.mark.asyncio
async def test_igus_fault_reset_fails_partial(noop_startup, client):
    """P1: igus fault_reset raises → step=failed, rest continues."""
    get_resp = _mock_aiohttp_get(200, {"safety_lockout": False})
    put_resp = _mock_aiohttp_put(200)
    fake_session = _FakeSession(get_resp=get_resp, put_resp=put_resp)

    with (
        patch("routes.robot.aiohttp.ClientSession", return_value=fake_session),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock, side_effect=RuntimeError("igus down")),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    assert body["success"] is False
    igus_step = _step_dict(body["steps"], "igus_fault_reset")
    assert igus_step["status"] == "failed"
    assert "igus down" in igus_step["error"]
    # xarm steps should still be attempted
    assert _step_dict(body["steps"], "xarm_recover")["status"] == "ok"


@pytest.mark.asyncio
async def test_xarm_recover_fails_partial(noop_startup, client):
    """P1: xarm recover raises → step=failed, enable_motion + drive_mode still attempted."""
    get_resp = _mock_aiohttp_get(200, {"safety_lockout": False})
    put_resp = _mock_aiohttp_put(200)
    fake_session = _FakeSession(get_resp=get_resp, put_resp=put_resp)

    with (
        patch("routes.robot.aiohttp.ClientSession", return_value=fake_session),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock, side_effect=RuntimeError("xarm error")),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    assert body["success"] is False
    assert _step_dict(body["steps"], "xarm_recover")["status"] == "failed"
    assert _step_dict(body["steps"], "xarm_enable_motion")["status"] == "ok"


@pytest.mark.asyncio
async def test_drive_mode_http_error(noop_startup, client):
    """P1: drive_mode enable returns HTTP 500 → step=failed."""
    get_resp = _mock_aiohttp_get(200, {"safety_lockout": False})
    put_resp = _mock_aiohttp_put(500, "Internal Server Error")
    fake_session = _FakeSession(get_resp=get_resp, put_resp=put_resp)

    with (
        patch("routes.robot.aiohttp.ClientSession", return_value=fake_session),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    assert body["success"] is False
    dm_step = _step_dict(body["steps"], "drive_mode_enable")
    assert dm_step["status"] == "failed"
    assert "HTTP 500" in dm_step["error"]


@pytest.mark.asyncio
async def test_nav2adapter_unreachable_skips_relay_check(noop_startup, client):
    """P1: nav2adapter connection error → relay check=skipped, rest proceeds."""
    from aiohttp import ClientConnectionError

    put_resp = _mock_aiohttp_put(200)
    fake_session = _FakeSession(get_exc=ClientConnectionError("timeout"), put_resp=put_resp)

    with (
        patch("routes.robot.aiohttp.ClientSession", return_value=fake_session),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    relay_step = _step_dict(body["steps"], "check_safety_relay")
    assert relay_step["status"] == "skipped"


@pytest.mark.asyncio
async def test_drive_mode_url_not_set_skipped(noop_startup, client):
    """P2: SYMOVO_DRIVE_MODE_URL empty → drive_mode step skipped."""
    get_resp = _mock_aiohttp_get(200, {"safety_lockout": False})
    fake_session = _FakeSession(get_resp=get_resp)

    with (
        patch("routes.robot.aiohttp.ClientSession", return_value=fake_session),
        patch("routes.robot.SYMOVO_DRIVE_MODE_URL", ""),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    dm_step = _step_dict(body["steps"], "drive_mode_enable")
    assert dm_step["status"] == "skipped"
