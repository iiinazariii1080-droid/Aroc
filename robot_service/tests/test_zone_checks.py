import pytest
from unittest.mock import AsyncMock
from app.zone_checks import agv_is_near, arm_tcp_in_job_zone, JOB_ZONE_BOX


# ── agv_is_near ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agv_is_near_within_tolerance():
    client = AsyncMock()
    client.pose.return_value = {"pose": {"x_m": 5.0, "y_m": 5.05}}
    assert await agv_is_near(client, 5.0, 5.0) is True  # 5cm < 10cm


@pytest.mark.asyncio
async def test_agv_is_near_outside_tolerance():
    client = AsyncMock()
    client.pose.return_value = {"pose": {"x_m": 5.0, "y_m": 5.2}}
    assert await agv_is_near(client, 5.0, 5.0) is False  # 20cm > 10cm


@pytest.mark.asyncio
async def test_agv_is_near_custom_tolerance():
    client = AsyncMock()
    client.pose.return_value = {"pose": {"x_m": 1.0, "y_m": 1.15}}
    assert await agv_is_near(client, 1.0, 1.0, tolerance_m=0.20) is True  # 15cm < 20cm
    assert await agv_is_near(client, 1.0, 1.0, tolerance_m=0.10) is False  # 15cm > 10cm


@pytest.mark.asyncio
async def test_agv_is_near_error_returns_false():
    client = AsyncMock()
    client.pose.side_effect = Exception("connection lost")
    assert await agv_is_near(client, 5.0, 5.0) is False


@pytest.mark.asyncio
async def test_agv_is_near_exact_position():
    client = AsyncMock()
    client.pose.return_value = {"pose": {"x_m": 3.0, "y_m": 4.0}}
    assert await agv_is_near(client, 3.0, 4.0) is True


# ── arm_tcp_in_job_zone ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_arm_tcp_inside_job_zone():
    client = AsyncMock()
    client.tcp_position.return_value = {"x": -100.0, "y": 200.0, "z": 100.0}
    assert await arm_tcp_in_job_zone(client) is True


@pytest.mark.asyncio
async def test_arm_tcp_outside_job_zone():
    client = AsyncMock()
    client.tcp_position.return_value = {"x": -500.0, "y": 200.0, "z": 100.0}
    assert await arm_tcp_in_job_zone(client) is False


@pytest.mark.asyncio
async def test_arm_tcp_on_boundary():
    client = AsyncMock()
    # Exactly on boundary — should be True (<=)
    client.tcp_position.return_value = {
        "x": JOB_ZONE_BOX["x"][0],
        "y": JOB_ZONE_BOX["y"][0],
        "z": JOB_ZONE_BOX["z"][0],
    }
    assert await arm_tcp_in_job_zone(client) is True


@pytest.mark.asyncio
async def test_arm_tcp_just_outside_boundary():
    client = AsyncMock()
    client.tcp_position.return_value = {
        "x": JOB_ZONE_BOX["x"][0] - 0.1,
        "y": JOB_ZONE_BOX["y"][0],
        "z": JOB_ZONE_BOX["z"][0],
    }
    assert await arm_tcp_in_job_zone(client) is False


@pytest.mark.asyncio
async def test_arm_tcp_error_returns_false():
    client = AsyncMock()
    client.tcp_position.side_effect = Exception("xarm offline")
    assert await arm_tcp_in_job_zone(client) is False
