"""Tests for POST /safety/recover — sequential recovery orchestrator."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


# ── Helpers ──────────────────────────────────────────────────────────


def _step_dict(steps: list[dict], name: str) -> dict | None:
    return next((s for s in steps if s["name"] == name), None)


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_all_steps_ok(noop_startup, client):
    """P0: all 4 recovery steps succeed → success=True, 5 steps all 'ok'."""
    with (
        patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
              return_value={"safety_lockout": False}),
        patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock,
              return_value={"enabled": True}),
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
    with (
        patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
              return_value={"safety_lockout": True, "reason": "estop"}),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock) as mock_igus,
    ):
        resp = client.post("/safety/recover")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "Safety relay still open" in body.get("message", "")
    mock_igus.assert_not_awaited()


@pytest.mark.asyncio
async def test_igus_fault_reset_fails_partial(noop_startup, client):
    """P1: igus fault_reset raises → step=failed, rest aborted."""
    with (
        patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
              return_value={"safety_lockout": False}),
        patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock,
              side_effect=RuntimeError("igus down")),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    assert body["success"] is False
    igus_step = _step_dict(body["steps"], "igus_fault_reset")
    assert igus_step["status"] == "failed"
    assert "igus down" in igus_step["error"]
    assert _step_dict(body["steps"], "xarm_recover") is None
    assert "aborted" in body.get("message", "").lower()


@pytest.mark.asyncio
async def test_xarm_recover_fails_partial(noop_startup, client):
    """H5: xarm recover raises → step=failed, enable_motion still attempted, then abort."""
    with (
        patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
              return_value={"safety_lockout": False}),
        patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock,
              side_effect=RuntimeError("xarm error")),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    assert body["success"] is False
    assert _step_dict(body["steps"], "xarm_recover")["status"] == "failed"
    assert _step_dict(body["steps"], "xarm_enable_motion")["status"] == "ok"
    assert _step_dict(body["steps"], "drive_mode_enable") is None
    assert "aborted" in body.get("message", "").lower()


@pytest.mark.asyncio
async def test_drive_mode_failure(noop_startup, client):
    """P1: drive_mode enable raises → step=failed."""
    from exceptions import DeviceConnectionError
    with (
        patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
              return_value={"safety_lockout": False}),
        patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock,
              side_effect=DeviceConnectionError("timeout")),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    assert body["success"] is False
    dm_step = _step_dict(body["steps"], "drive_mode_enable")
    assert dm_step["status"] == "failed"
    assert "timeout" in dm_step["error"]


@pytest.mark.asyncio
async def test_nav2adapter_unreachable_skips_relay_check(noop_startup, client):
    """P1: nav2adapter connection error → relay check=skipped, rest proceeds."""
    from exceptions import DeviceConnectionError
    with (
        patch("routes.robot.robot.agv.safety_state", new_callable=AsyncMock,
              side_effect=DeviceConnectionError("timeout")),
        patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock,
              return_value={"enabled": True}),
        patch("routes.robot.robot.lift.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.fault_reset", new_callable=AsyncMock),
        patch("routes.robot.robot.manipulator.enable_motion", new_callable=AsyncMock),
    ):
        resp = client.post("/safety/recover")

    body = resp.json()
    relay_step = _step_dict(body["steps"], "check_safety_relay")
    assert relay_step["status"] == "skipped"
