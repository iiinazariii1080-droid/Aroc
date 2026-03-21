"""Tests for go_to_charging_station endpoint and orchestration."""

import pytest
from unittest.mock import AsyncMock, patch


# ── route tests (use conftest.py client fixture) ─────────────────

def test_go_to_charging_returns_task_id(client):
    """Endpoint accepts GoToChargingRequest and returns 200 with task_id."""
    with patch("routes.robot.robot.go_to_charging_station", new_callable=AsyncMock, return_value=True):
        resp = client.post(
            "/tasks/go_to_charging_station",
            json={"station_id": 687327357713408},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "task_id" in data


def test_go_to_charging_validation_error(client):
    """Missing station_id returns 422."""
    resp = client.post("/tasks/go_to_charging_station", json={})
    assert resp.status_code == 422


def test_go_to_charging_invalid_type(client):
    """Non-integer station_id returns 422."""
    resp = client.post("/tasks/go_to_charging_station", json={"station_id": "abc"})
    assert resp.status_code == 422


# ── unit tests for go_to_charging_station function ───────────────

@pytest.fixture
def _import_scripts():
    """Import robot_scripts, patching heavy dependencies at module level."""
    import app.robot_scripts as scripts
    return scripts


@pytest.mark.asyncio
async def test_go_to_charging_calls_preflight_and_activate(_import_scripts):
    """Verify the function calls preflight steps, clears transports, and activates charger."""
    scripts = _import_scripts
    with patch.object(scripts, "manipulator") as mock_manip, \
         patch.object(scripts, "lift") as mock_lift, \
         patch.object(scripts, "agv") as mock_agv, \
         patch.object(scripts, "_move_to_job_pose", new_callable=AsyncMock) as mock_job_pose, \
         patch.object(scripts, "igus_move_and_check", new_callable=AsyncMock), \
         patch.object(scripts, "_get_global_velocity", return_value=30):

        mock_manip.fault_reset = AsyncMock()
        mock_manip.enable_motion = AsyncMock()
        # After preflight, arm is at JOB_POSE (coordination gate re-queries)
        mock_manip.current_joints_position = AsyncMock(return_value={"name": "JOB_POSE"})
        mock_lift.fault_reset = AsyncMock()
        mock_lift.position = AsyncMock(return_value={"position": 5000})
        mock_agv.fault_reset = AsyncMock()
        mock_agv.go_to_charging_station = AsyncMock(return_value={"status": "ok"})
        mock_agv.disable_all_charging_stations = AsyncMock(return_value={})

        result = await scripts.go_to_charging_station.__wrapped__(station_id=12345)

        assert result is True
        mock_manip.fault_reset.assert_awaited_once()
        mock_manip.enable_motion.assert_awaited_once()
        mock_agv.fault_reset.assert_awaited_once()
        mock_agv.go_to_charging_station.assert_awaited_once_with(12345)


@pytest.mark.asyncio
async def test_go_to_charging_skips_job_pose_if_already_there(_import_scripts):
    """If arm is already at JOB_POSE, skip _move_to_job_pose."""
    scripts = _import_scripts
    with patch.object(scripts, "manipulator") as mock_manip, \
         patch.object(scripts, "lift") as mock_lift, \
         patch.object(scripts, "agv") as mock_agv, \
         patch.object(scripts, "_move_to_job_pose", new_callable=AsyncMock) as mock_job_pose, \
         patch.object(scripts, "igus_move_and_check", new_callable=AsyncMock), \
         patch.object(scripts, "_get_global_velocity", return_value=30):

        mock_manip.fault_reset = AsyncMock()
        mock_manip.enable_motion = AsyncMock()
        mock_manip.current_joints_position = AsyncMock(return_value={"name": "JOB_POSE"})
        mock_lift.fault_reset = AsyncMock()
        mock_lift.position = AsyncMock(return_value={"position": 5000})
        mock_agv.fault_reset = AsyncMock()
        mock_agv.go_to_charging_station = AsyncMock(return_value={"status": "ok"})
        mock_agv.disable_all_charging_stations = AsyncMock(return_value={})

        result = await scripts.go_to_charging_station.__wrapped__(station_id=12345)

        assert result is True
        mock_job_pose.assert_not_awaited()


@pytest.mark.asyncio
async def test_go_to_charging_lowers_lift_if_too_high(_import_scripts):
    """If lift is above LIFT_TRANSPORT_MAX, lower it."""
    scripts = _import_scripts
    with patch.object(scripts, "manipulator") as mock_manip, \
         patch.object(scripts, "lift") as mock_lift, \
         patch.object(scripts, "agv") as mock_agv, \
         patch.object(scripts, "_move_to_job_pose", new_callable=AsyncMock), \
         patch.object(scripts, "igus_move_and_check", new_callable=AsyncMock) as mock_igus, \
         patch.object(scripts, "_get_global_velocity", return_value=30):

        mock_manip.fault_reset = AsyncMock()
        mock_manip.enable_motion = AsyncMock()
        mock_manip.current_joints_position = AsyncMock(return_value={"name": "JOB_POSE"})
        mock_lift.fault_reset = AsyncMock()
        mock_lift.position = AsyncMock(return_value={"position": 50000})  # above LIFT_TRANSPORT_MAX
        mock_agv.fault_reset = AsyncMock()
        mock_agv.go_to_charging_station = AsyncMock(return_value={"status": "ok"})
        mock_agv.disable_all_charging_stations = AsyncMock(return_value={})

        await scripts.go_to_charging_station.__wrapped__(station_id=12345)

        mock_igus.assert_awaited_once_with(20000, 30)  # LIFT_TRANSPORT_MAX, velocity
