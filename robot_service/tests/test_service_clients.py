"""Tests for downstream HTTP service clients.

Covers XarmManipulatorClient, IgusMotorClient, SymovoAgvClient using
aioresponses to intercept real HTTP calls.
"""
from __future__ import annotations

import pytest
from aioresponses import aioresponses

from exceptions import DeviceConnectionError, DeviceError

# ── XarmManipulatorClient ──────────────────────────────────────────────────

XARM_BASE = "http://127.0.0.1:8102"


@pytest.fixture
def xarm_client():
    from services.xarm_service import XarmManipulatorClient

    return XarmManipulatorClient(base_url=XARM_BASE, timeout_seconds=2)


class TestXarmClientHappy:
    @pytest.mark.asyncio
    async def test_status_returns_json(self, xarm_client):
        with aioresponses() as m:
            m.get(f"{XARM_BASE}/status", payload={"state": 2, "error": 0})
            result = await xarm_client.status()
        assert result == {"state": 2, "error": 0}

    @pytest.mark.asyncio
    async def test_gripper_take(self, xarm_client):
        with aioresponses() as m:
            m.post(f"{XARM_BASE}/gripper/take", payload={"success": True})
            result = await xarm_client.gripper_take()
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_gripper_drop(self, xarm_client):
        with aioresponses() as m:
            m.post(f"{XARM_BASE}/gripper/drop", payload={"success": True})
            result = await xarm_client.gripper_drop()
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_fault_reset(self, xarm_client):
        with aioresponses() as m:
            m.post(f"{XARM_BASE}/recover", payload={"success": True})
            result = await xarm_client.fault_reset()
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_current_position(self, xarm_client):
        with aioresponses() as m:
            m.get(
                f"{XARM_BASE}/current_position",
                payload={"x": 100, "y": 200, "z": 300, "roll": 0, "pitch": 0, "yaw": 0},
            )
            result = await xarm_client.current_position()
        assert result["x"] == 100

    @pytest.mark.asyncio
    async def test_enable_motion(self, xarm_client):
        with aioresponses() as m:
            m.post(f"{XARM_BASE}/enable_motion", payload={"success": True})
            result = await xarm_client.enable_motion()
        assert result["success"] is True


class TestXarmClientErrors:
    @pytest.mark.asyncio
    async def test_503_raises_connection_error(self, xarm_client):
        with aioresponses() as m:
            m.get(f"{XARM_BASE}/status", status=503, payload={"detail": "unavailable"})
            with pytest.raises(DeviceConnectionError):
                await xarm_client.status()

    @pytest.mark.asyncio
    async def test_409_raises_device_error(self, xarm_client):
        with aioresponses() as m:
            m.post(
                f"{XARM_BASE}/gripper/take",
                status=409,
                payload={"detail": {"error": "state=4"}},
            )
            with pytest.raises(DeviceError, match="state=4"):
                await xarm_client.gripper_take()

    @pytest.mark.asyncio
    async def test_resilience_retries_on_recoverable(self, xarm_client):
        """Motion methods retry once after auto-recover on recoverable errors."""
        with aioresponses() as m:
            # First attempt: 409 with motion-recoverable message
            m.post(
                f"{XARM_BASE}/gripper/take",
                status=409,
                payload={"detail": {"error": "robot faulted"}},
            )
            # Auto-recover calls
            m.post(f"{XARM_BASE}/recover", payload={"success": True})
            m.post(f"{XARM_BASE}/enable_motion", payload={"success": True})
            # Second attempt succeeds
            m.post(f"{XARM_BASE}/gripper/take", payload={"success": True})

            result = await xarm_client.gripper_take()
        assert result == {"success": True}


# ── IgusMotorClient ───────────────────────────────────────────────────────

IGUS_BASE = "http://127.0.0.1:8101"


@pytest.fixture
def igus_client():
    from services.igus_service import IgusMotorClient

    return IgusMotorClient(base_url=IGUS_BASE, timeout_seconds=2)


class TestIgusClientHappy:
    @pytest.mark.asyncio
    async def test_status(self, igus_client):
        with aioresponses() as m:
            m.get(f"{IGUS_BASE}/status", payload={"position": 42.0, "homed": True})
            result = await igus_client.status()
        assert result["homed"] is True

    @pytest.mark.asyncio
    async def test_position(self, igus_client):
        with aioresponses() as m:
            m.get(f"{IGUS_BASE}/position", payload={"position_cm": 10.5})
            result = await igus_client.position()
        assert result["position_cm"] == 10.5

    @pytest.mark.asyncio
    async def test_reference(self, igus_client):
        with aioresponses() as m:
            m.post(f"{IGUS_BASE}/reference", payload={"success": True})
            result = await igus_client.reference()
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_jog_stop(self, igus_client):
        with aioresponses() as m:
            m.post(f"{IGUS_BASE}/drive/jog_stop", payload={"ok": True, "data": {"stopped": True}})
            result = await igus_client.jog_stop()
        assert result["stopped"] is True


class TestIgusClientErrors:
    @pytest.mark.asyncio
    async def test_503_raises_connection_error(self, igus_client):
        with aioresponses() as m:
            m.get(f"{IGUS_BASE}/status", status=503, payload={"detail": "down"})
            with pytest.raises(DeviceConnectionError):
                await igus_client.status()

    @pytest.mark.asyncio
    async def test_envelope_error_unwrapped(self, igus_client):
        """If server returns ok=false envelope, DeviceError is raised."""
        with aioresponses() as m:
            m.post(
                f"{IGUS_BASE}/drive/jog_stop",
                payload={"ok": False, "error": {"message": "not homed"}},
            )
            with pytest.raises(DeviceError, match="not homed"):
                await igus_client.jog_stop()


# ── SymovoAgvClient ───────────────────────────────────────────────────────

SYMOVO_BASE = "http://127.0.0.1:7906"


@pytest.fixture
def symovo_client():
    from services.symovo_service import SymovoAgvClient

    return SymovoAgvClient(base_url=SYMOVO_BASE, timeout_seconds=2)


class TestSymovoClientHappy:
    @pytest.mark.asyncio
    async def test_status(self, symovo_client):
        with aioresponses() as m:
            m.get(f"{SYMOVO_BASE}/status", payload={"online": True, "state": "idle"})
            result = await symovo_client.status()
        assert result["online"] is True

    @pytest.mark.asyncio
    async def test_pose(self, symovo_client):
        with aioresponses() as m:
            m.get(f"{SYMOVO_BASE}/pose", payload={"x_m": 1.0, "y_m": 2.0})
            result = await symovo_client.pose()
        assert result["x_m"] == 1.0

    @pytest.mark.asyncio
    async def test_healthz(self, symovo_client):
        with aioresponses() as m:
            m.get(f"{SYMOVO_BASE}/healthz", payload={"ok": True})
            result = await symovo_client.healthz()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_fault_reset(self, symovo_client):
        with aioresponses() as m:
            m.post(f"{SYMOVO_BASE}/fault_reset", payload={"success": True})
            result = await symovo_client.fault_reset()
        assert result["success"] is True


class TestSymovoClientErrors:
    @pytest.mark.asyncio
    async def test_503_raises_connection_error(self, symovo_client):
        with aioresponses() as m:
            m.get(f"{SYMOVO_BASE}/status", status=503, payload={"detail": "offline"})
            with pytest.raises(DeviceConnectionError):
                await symovo_client.status()

    @pytest.mark.asyncio
    async def test_error_detail_extracted(self, symovo_client):
        with aioresponses() as m:
            m.get(
                f"{SYMOVO_BASE}/pose",
                status=500,
                payload={"detail": {"error": "sensor fault"}},
            )
            with pytest.raises(DeviceError, match="sensor fault"):
                await symovo_client.pose()


# ── SymovoAgvClient._normalize_symovo_status ──────────────────────────────

class TestNormalizeSymovoStatus:
    """Pure-function tests for _normalize_symovo_status."""

    def test_minimal_dict_returns_error_status(self):
        """Minimal dict without required pose fields returns ErrorStatus."""
        from services.symovo_service import _normalize_symovo_status
        from models.api_types import ErrorStatus

        result = _normalize_symovo_status({"online": True})
        # pose.x_m, pose.y_m are required by SymovoStatusResponse, so
        # validation fails and we get an ErrorStatus back.
        assert isinstance(result, ErrorStatus)

    def test_complete_dict(self):
        from services.symovo_service import _normalize_symovo_status

        raw = {
            "online": True,
            "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 45.0, "map_id": 0},
            "velocity": {"vx_m_s": 0.1, "vy_m_s": 0.2, "omega_rad_s": 0.3},
            "state": "idle",
        }
        result = _normalize_symovo_status(raw)
        assert result.online is True
        assert result.pose.x_m == 1.0

    def test_list_input_unwrapped(self):
        from services.symovo_service import _normalize_symovo_status

        raw = [{
            "online": False,
            "state": "error",
            "pose": {"x_m": 0.0, "y_m": 0.0, "theta_deg": 0.0, "map_id": 0},
            "velocity": {"vx_m_s": 0.0, "vy_m_s": 0.0, "omega_rad_s": 0.0},
        }]
        result = _normalize_symovo_status(raw)
        assert result.online is False
        assert result.state == "error"

    def test_old_shape_converted(self):
        from services.symovo_service import _normalize_symovo_status

        raw = {
            "online": True,
            "pose": {"x": 1.5, "y": 2.5, "theta": 45.0, "map_id": 0},
            "velocity": {"x": 0.1, "y": 0.2, "theta": 0.3},
            "battery_level": 0.85,
        }
        result = _normalize_symovo_status(raw)
        assert result.pose.x_m == 1.5
        assert result.pose.y_m == 2.5
        assert result.pose.theta_deg == 45.0
        assert result.velocity.vx_m_s == 0.1
        assert abs(result.battery_level_percent - 85.0) < 0.01

    def test_theta_rad_conversion(self):
        import math
        from services.symovo_service import _normalize_symovo_status

        raw = {
            "pose": {"x_m": 0, "y_m": 0, "theta_rad": math.pi / 2, "map_id": 0},
            "velocity": {"vx_m_s": 0, "vy_m_s": 0, "omega_rad_s": 0},
        }
        result = _normalize_symovo_status(raw)
        assert abs(result.pose.theta_deg - 90.0) < 0.01

    def test_empty_list_returns_error(self):
        from services.symovo_service import _normalize_symovo_status
        from models.api_types import ErrorStatus

        result = _normalize_symovo_status([])
        # Empty list → empty dict → missing required fields → ErrorStatus
        assert isinstance(result, ErrorStatus)


# ── xArm channel lock ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xarm_channel_lock_serializes_calls():
    """_channel_guarded decorator serializes concurrent calls via shared lock."""
    import asyncio
    from services.xarm_service import _channel_guarded, set_xarm_channel_lock

    lock = asyncio.Lock()
    set_xarm_channel_lock(lock)

    log: list[tuple[str, str]] = []

    @_channel_guarded
    async def task_a():
        log.append(("a", "enter"))
        await asyncio.sleep(0.05)
        log.append(("a", "exit"))

    @_channel_guarded
    async def task_b():
        log.append(("b", "enter"))
        await asyncio.sleep(0.05)
        log.append(("b", "exit"))

    try:
        await asyncio.gather(task_a(), task_b())
        # With serialization, one must fully complete before the other starts
        assert log[0] == (log[0][0], "enter")
        assert log[1] == (log[0][0], "exit")
        assert log[2] == (log[2][0], "enter")
        assert log[3] == (log[2][0], "exit")
        # Both tasks ran
        names = {e[0] for e in log}
        assert names == {"a", "b"}
    finally:
        set_xarm_channel_lock(None)
