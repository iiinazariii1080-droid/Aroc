"""Integration tests for RobotActor (mocked SDK). Requires xarm SDK."""
import asyncio
import pytest

pytest.importorskip("xarm")

from drivers.xarm_driver.state import StateStore
from drivers.xarm_driver.actor import RobotActor, Command, CommandType, ResultStatus


class MockArm:
    """Mock XArmAPI for tests."""
    connected = True
    def get_robot_sn(self): pass
    def disconnect(self): self.connected = False
    def get_position(self): return (0, [100.0, 200.0, 300.0, 0, 0, 0])
    def get_servo_angle(self): return (0, [0.0] * 7)
    def get_state(self): return 1
    def get_err_warn_code(self): return [0, 0]
    def set_reduced_tcp_boundary(self, x): return 0
    def set_reduced_mode(self, x): return 0
    def register_error_warn_changed_callback(self, cb): pass
    def register_state_changed_callback(self, cb): pass
    def register_count_changed_callback(self, cb): pass
    def release_error_warn_changed_callback(self, cb): pass
    def release_state_changed_callback(self, cb): pass
    def release_count_changed_callback(self, cb): pass


@pytest.fixture
def mock_xarm_api(monkeypatch):
    """Patch XArmAPI so RobotActor uses MockArm."""
    monkeypatch.setattr("drivers.xarm_driver.actor.actor.XArmAPI", lambda *a, **k: MockArm())


@pytest.mark.asyncio
async def test_actor_enqueue_get_status_sequential(mock_xarm_api):
    """Actor processes GET_STATUS commands sequentially."""
    store = StateStore()
    actor = RobotActor(state_store_getter=lambda: store)
    await actor.start()
    await asyncio.sleep(0.5)  # let connect_loop set _arm
    try:
        cmd1 = Command(command_id="c1", type=CommandType.GET_STATUS, params={})
        cmd2 = Command(command_id="c2", type=CommandType.GET_STATUS, params={})
        r1 = await actor.enqueue(cmd1)
        r2 = await actor.enqueue(cmd2)
        assert r1.status == ResultStatus.SUCCEEDED
        assert r2.status == ResultStatus.SUCCEEDED
        assert r1.telemetry_snapshot is not None
        assert r2.telemetry_snapshot is not None
    finally:
        await actor.stop()
