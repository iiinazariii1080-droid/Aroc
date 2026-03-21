"""Unit tests for app/robot_scripts.py orchestration functions.

Mock all external service clients (lift, manipulator, symovo, depth_camera)
at module level to avoid any hardware calls.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from exceptions import DeviceReadyError


def _symovo_raw_ok(**overrides):
    """Raw dict that _normalize_symovo_status can convert to SymovoStatusResponse."""
    d = dict(
        online=True,
        enabled=True,
        pose={"x_m": 0, "y_m": 0, "theta_deg": 0, "map_id": 0},
        velocity={"vx_m_s": 0, "vy_m_s": 0, "omega_rad_s": 0},
    )
    d.update(overrides)
    return d


# ── helpers ────────────────────────────────────────────────────────
def _patch_services():
    """Return dict of patches for all module-level service clients."""
    return {
        "lift": patch("app.robot_scripts.lift"),
        "manipulator": patch("app.robot_scripts.manipulator"),
        "agv": patch("app.robot_scripts.agv"),
        "depth_camera": patch("app.robot_scripts.depth_camera"),
        "robot_lock": patch("app.robot_scripts.robot_lock", new_callable=lambda: asyncio.Lock),
    }


@pytest.fixture
def mocks():
    """Patch service singletons and the robot_lock."""
    patches = _patch_services()
    started = {k: p.start() for k, p in patches.items()}

    # Make all service methods async by default
    for name in ("lift", "manipulator", "agv", "depth_camera"):
        for method in ("status", "fault_reset", "reference", "enable_motion",
                       "position", "move", "current_joints_position",
                       "complex_move_with_joints", "change_tool_position",
                       "gripper_take", "gripper_status", "go_to_pose", "pose",
                       "depth"):
            if not hasattr(started[name], method):
                setattr(started[name], method, AsyncMock())
            else:
                mock_attr = getattr(started[name], method)
                if not isinstance(mock_attr, AsyncMock):
                    setattr(started[name], method, AsyncMock())

    yield started

    for p in patches.values():
        p.stop()


# ── _ns_to_dict ────────────────────────────────────────────────────
def test_ns_to_dict_basic():
    from app.robot_scripts import _ns_to_dict
    ns = SimpleNamespace(a=1, b=SimpleNamespace(c=2))
    assert _ns_to_dict(ns) == {"a": 1, "b": {"c": 2}}


def test_ns_to_dict_pydantic():
    from app.robot_scripts import _ns_to_dict
    m = MagicMock()
    m.model_dump = MagicMock(return_value={"x": 10})
    del m.dict  # so model_dump is preferred
    assert _ns_to_dict(m) == {"x": 10}


def test_ns_to_dict_list():
    from app.robot_scripts import _ns_to_dict
    assert _ns_to_dict([1, 2]) == [1, 2]


def test_ns_to_dict_dict():
    from app.robot_scripts import _ns_to_dict
    assert _ns_to_dict({"a": 1}) == {"a": 1}


def test_ns_to_dict_scalar():
    from app.robot_scripts import _ns_to_dict
    assert _ns_to_dict(42) == 42


# ── fault_reset ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fault_reset_success(mocks):
    from app.robot_scripts import fault_reset
    result = await fault_reset()
    assert result is True
    mocks["lift"].fault_reset.assert_awaited_once()
    mocks["manipulator"].fault_reset.assert_awaited_once()
    mocks["agv"].fault_reset.assert_awaited_once()
    mocks["manipulator"].enable_motion.assert_awaited_once()


@pytest.mark.asyncio
async def test_fault_reset_raises(mocks):
    mocks["manipulator"].fault_reset = AsyncMock(side_effect=RuntimeError("xarm down"))
    from app.robot_scripts import fault_reset
    with pytest.raises(RuntimeError, match="xarm down"):
        await fault_reset()


# ── igus_move_and_check ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_igus_move_and_check_success(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 1005})
    from app.robot_scripts import igus_move_and_check
    result = await igus_move_and_check(1000, 50)
    assert result is True


@pytest.mark.asyncio
async def test_igus_move_and_check_too_far(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 5000})
    from app.robot_scripts import igus_move_and_check
    result = await igus_move_and_check(1000, 50)
    assert result is False  # position too far


# ── set_ready ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_set_ready_success(mocks):
    from app import xarm_status
    # Set up xarm_status cache to show everything ready
    xarm_status.set_status({
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["agv"].status = AsyncMock(return_value=_symovo_raw_ok())

    from app.robot_scripts import set_ready
    result = await set_ready()
    assert result is True


@pytest.mark.asyncio
async def test_set_ready_not_ready(mocks):
    from app import xarm_status
    from exceptions import DeviceReadyError
    # No cache → xarm not ready
    xarm_status._last_status = None
    xarm_status._last_updated_ts = 0.0
    mocks["lift"].status = AsyncMock(return_value={"connected": False})
    mocks["agv"].status = AsyncMock(side_effect=RuntimeError("offline"))

    from app.robot_scripts import set_ready
    with pytest.raises(DeviceReadyError):
        await set_ready()


# ── move_robot_to_box_1 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_move_robot_to_box_1(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 30010})
    mocks["manipulator"].complex_move_with_joints = AsyncMock()

    from app.robot_scripts import move_robot_to_box_1
    result = await move_robot_to_box_1(40)
    assert result is True


@pytest.mark.asyncio
async def test_move_robot_to_box_2(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 30010})
    mocks["manipulator"].complex_move_with_joints = AsyncMock()

    from app.robot_scripts import move_robot_to_box_2
    result = await move_robot_to_box_2(40)
    assert result is True


# ── move_to_transport_position ─────────────────────────────────────
@pytest.mark.asyncio
async def test_move_to_transport_position(mocks):
    mocks["lift"].position = AsyncMock(
        side_effect=[{"position": 20010}, {"position": 5}]
    )
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "SOME_POSE"}
    )
    mocks["manipulator"].complex_move_with_joints = AsyncMock()

    from app.robot_scripts import move_to_transport_position
    result = await move_to_transport_position(50)
    assert result is True


@pytest.mark.asyncio
async def test_move_to_transport_already_in_position(mocks):
    mocks["lift"].position = AsyncMock(
        side_effect=[{"position": 20010}, {"position": 5}]
    )
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )

    from app.robot_scripts import move_to_transport_position
    result = await move_to_transport_position(50)
    assert result is True


# ── get_robot_system_status ────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_robot_system_status_all_ready(mocks):
    import time
    from app import xarm_status
    xarm_status.set_status({
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["agv"].status = AsyncMock(return_value=_symovo_raw_ok())

    from app.robot_scripts import get_robot_system_status
    result = await get_robot_system_status()
    assert result["ready"] is True
    assert result["message"] == ""


@pytest.mark.asyncio
async def test_get_robot_system_status_igus_down(mocks):
    from app import xarm_status

    xarm_status.set_status({
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(side_effect=RuntimeError("igus offline"))
    mocks["agv"].status = AsyncMock(return_value=_symovo_raw_ok())

    from app.robot_scripts import get_robot_system_status
    result = await get_robot_system_status()
    assert result["ready"] is False
    assert "Igus" in result["message"]


@pytest.mark.asyncio
async def test_get_robot_system_status_no_cache(mocks):
    from app import xarm_status
    xarm_status._last_status = None
    xarm_status._last_updated_ts = 0.0

    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["agv"].status = AsyncMock(side_effect=RuntimeError("symovo offline"))

    from app.robot_scripts import get_robot_system_status
    result = await get_robot_system_status()
    assert result["ready"] is False


# ── _preflight_make_transport_safe ─────────────────────────────────
@pytest.mark.asyncio
async def test_preflight_all_ready(mocks):
    mocks["manipulator"].status = AsyncMock(return_value={
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_xarm_not_ready_recovers(mocks):
    # First status call: not ready. Second call after reset: ready.
    mocks["manipulator"].status = AsyncMock(side_effect=[
        {"connected": False},
        {"connected": True, "has_error": False, "has_err_warn": False, "motion_enabled": True},
    ])
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_igus_not_ready_recovers(mocks):
    mocks["manipulator"].status = AsyncMock(return_value={
        "connected": True, "has_error": False, "has_err_warn": False
    })
    # First not ready, second after reference: ready
    mocks["lift"].status = AsyncMock(side_effect=[
        {"connected": False},
        {"connected": True, "homed": True, "error": None},
    ])
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_both_fail(mocks):
    from exceptions import DeviceReadyError
    mocks["manipulator"].status = AsyncMock(side_effect=RuntimeError("xarm fail"))
    mocks["manipulator"].fault_reset = AsyncMock(side_effect=RuntimeError("reset fail"))
    mocks["lift"].status = AsyncMock(side_effect=RuntimeError("igus fail"))
    mocks["lift"].fault_reset = AsyncMock(side_effect=RuntimeError("reset fail"))
    mocks["lift"].reference = AsyncMock(side_effect=RuntimeError("ref fail"))

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    with pytest.raises(DeviceReadyError, match="XArm.*Igus"):
        await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_needs_manipulator_move(mocks):
    mocks["manipulator"].status = AsyncMock(return_value={
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    # Not at TRANSPORT_STEP_2 → must move
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "SOME_OTHER_POSE"}
    )
    mocks["manipulator"].complex_move_with_joints = AsyncMock()
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)
    mocks["manipulator"].complex_move_with_joints.assert_awaited_once()


# ── Coordination gates ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_arm_stowed_transport_safe(mocks):
    """Transport-safe poses should not raise."""
    from app.robot_scripts import _assert_arm_stowed_for_transport

    for pose_name in ("JOB_POSE", "TRANSPORT_STEP_2"):
        mocks["manipulator"].current_joints_position = AsyncMock(return_value={"name": pose_name})
        await _assert_arm_stowed_for_transport()  # should not raise


@pytest.mark.asyncio
async def test_assert_arm_stowed_not_safe(mocks):
    """Non-transport pose should raise DeviceReadyError."""
    from app.robot_scripts import _assert_arm_stowed_for_transport

    mocks["manipulator"].current_joints_position = AsyncMock(return_value={"name": "HOME"})
    with pytest.raises(DeviceReadyError, match="not stowed"):
        await _assert_arm_stowed_for_transport()


@pytest.mark.asyncio
async def test_assert_agv_stopped_ok(mocks):
    """Stopped AGV should not raise."""
    from app.robot_scripts import _assert_agv_stopped

    mocks["agv"].status = AsyncMock(return_value={"velocity": {"vx_m_s": 0.0, "vy_m_s": 0.0}})
    await _assert_agv_stopped()


@pytest.mark.asyncio
async def test_assert_agv_stopped_moving(mocks):
    """Moving AGV should raise DeviceReadyError after retries exhaust."""
    from app.robot_scripts import _assert_agv_stopped

    mocks["agv"].status = AsyncMock(return_value={"velocity": {"vx_m_s": 0.10, "vy_m_s": 0.0}})
    with pytest.raises(DeviceReadyError, match="still moving"):
        await _assert_agv_stopped(retries=2, interval=0.01)


@pytest.mark.asyncio
async def test_check_safety_periodic_rate_limited(mocks):
    """_check_safety_periodic should rate-limit calls via SafetyKernel."""
    from app.robot_scripts import _check_safety_periodic
    from safety.safety_kernel import get_safety_kernel

    kernel = get_safety_kernel()
    # Reset rate-limit timestamp to force fresh check
    kernel._last_check_ts = 0.0

    mock_auth = AsyncMock()
    with patch.object(kernel, "authorize_motion", mock_auth):
        await _check_safety_periodic()
        mock_auth.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_all_devices_verify_retries(mocks):
    """_stop_all_devices retries stop when AGV is still moving after first stop."""
    import app.robot_scripts as rs
    from app.robot_scripts import _stop_all_devices

    rs._xarm_commands = None  # use manipulator.fault_reset path

    mocks["agv"].fault_reset = AsyncMock(return_value={})
    mocks["manipulator"].fault_reset = AsyncMock(return_value={})
    mocks["lift"].fault_reset = AsyncMock(return_value={})
    # First status check: still moving. Second: stopped.
    mocks["agv"].status = AsyncMock(side_effect=[
        {"velocity": {"vx_m_s": 0.1, "vy_m_s": 0.0}},
        {"velocity": {"vx_m_s": 0.0, "vy_m_s": 0.0}},
    ])
    mocks["manipulator"].status = AsyncMock(return_value={"state": 0})  # not moving

    await _stop_all_devices()

    # AGV fault_reset called at least twice (initial + retry)
    assert mocks["agv"].fault_reset.await_count >= 2


@pytest.mark.asyncio
async def test_move_robot_to_product_coordination_gates(mocks):
    """move_robot_to_product calls coordination gates at correct phases."""
    import app.robot_scripts as rs

    mocks["agv"].pose = AsyncMock(return_value={"pose": {"x_m": 0, "y_m": 0, "theta_deg": 0}})
    mocks["agv"].status = AsyncMock(return_value={
        "online": True, "velocity": {"vx_m_s": 0, "vy_m_s": 0}
    })
    mocks["agv"].go_to_pose = AsyncMock(return_value={"transport_id": 1})
    mocks["agv"].disable_all_charging_stations = AsyncMock(return_value={})
    mocks["manipulator"].current_joints_position = AsyncMock(return_value={"name": "JOB_POSE"})
    mocks["lift"].position = AsyncMock(return_value={"position": 100})

    location = SimpleNamespace(x_m=5.0, y_m=5.0, theta_deg=0, map_id=None, place="shelf1")

    with patch.object(rs, "_assert_arm_stowed_for_transport", new_callable=AsyncMock) as mock_arm_check, \
         patch.object(rs, "_assert_agv_stopped", new_callable=AsyncMock) as mock_agv_check, \
         patch.object(rs, "_check_safety_periodic", new_callable=AsyncMock), \
         patch.object(rs, "agv_is_near", new_callable=AsyncMock, return_value=False), \
         patch.object(rs, "_preflight_for_navigation", new_callable=AsyncMock, return_value=30), \
         patch.object(rs, "_wait_agv_arrival", new_callable=AsyncMock):

        params = SimpleNamespace(
            velocity_percent=30,
            location=location,
            product=SimpleNamespace(lift_position_encoder_value=5000, position="AUTO"),
        )
        try:
            await rs.move_robot_to_product.__wrapped__(params)
        except Exception:
            pass  # may fail in Phase 3 positioning — that's OK

        mock_arm_check.assert_awaited()
        mock_agv_check.assert_awaited()


# ── TaskPhase FSM ─────────────────────────────────────────────────


def test_task_phase_enum():
    """TaskPhase enum has all required phases."""
    from models.base_types import TaskPhase
    expected = {"idle", "preflight", "navigate", "position", "execute", "verify", "cleanup"}
    actual = {p.value for p in TaskPhase}
    assert expected == actual


def test_set_phase_updates_state():
    """_set_phase correctly updates the global phase."""
    import app.robot_scripts as rs
    from models.base_types import TaskPhase

    rs._set_phase(TaskPhase.IDLE)
    assert rs._current_phase == TaskPhase.IDLE

    rs._set_phase(TaskPhase.PREFLIGHT, task_id="abc123")
    assert rs._current_phase == TaskPhase.PREFLIGHT
    assert rs._phase_task_id == "abc123"


def test_get_task_phase_snapshot():
    """get_task_phase returns current state as dict."""
    import app.robot_scripts as rs
    from models.base_types import TaskPhase

    rs._set_phase(TaskPhase.NAVIGATE, task_id="xyz789")
    info = rs.get_task_phase()
    assert info == {"phase": "navigate", "task_id": "xyz789"}

    rs._set_phase(TaskPhase.IDLE)


@pytest.mark.asyncio
async def test_move_robot_to_product_phase_transitions(mocks):
    """move_robot_to_product transitions through PREFLIGHT → NAVIGATE → POSITION → VERIFY → IDLE."""
    import app.robot_scripts as rs
    from models.base_types import TaskPhase

    phases_seen: list[str] = []
    _orig_set_phase = rs._set_phase

    def _tracking_set_phase(phase, *, task_id=None):
        phases_seen.append(phase.value)
        _orig_set_phase(phase, task_id=task_id)

    mocks["agv"].pose = AsyncMock(return_value={"pose": {"x_m": 0, "y_m": 0, "theta_deg": 0}})
    mocks["agv"].status = AsyncMock(return_value={
        "online": True, "velocity": {"vx_m_s": 0, "vy_m_s": 0}
    })
    mocks["agv"].go_to_pose = AsyncMock(return_value={"transport_id": 1})
    mocks["agv"].disable_all_charging_stations = AsyncMock(return_value={})
    mocks["manipulator"].current_joints_position = AsyncMock(return_value={
        "name": "JOB_POSE", "joints": {"j1": 0, "j2": 0, "j3": 0, "j4": 0, "j5": 0, "j6": 0}
    })
    mocks["lift"].position = AsyncMock(return_value={"position": 1000})

    location = SimpleNamespace(x_m=5.0, y_m=5.0, theta_deg=0, map_id=None, place="shelf1")
    xarm_joints = SimpleNamespace(joints=SimpleNamespace(
        j1=0.0, j2=0.0, j3=0.0, j4=0.0, j5=0.0, j6=0.0,
    ))
    params = SimpleNamespace(
        location=location,
        lift_position_cm=1.0,
        xarm_joints=xarm_joints,
    )

    with patch.object(rs, "_set_phase", side_effect=_tracking_set_phase), \
         patch.object(rs, "_check_safety_periodic", new_callable=AsyncMock), \
         patch.object(rs, "agv_is_near", new_callable=AsyncMock, return_value=False), \
         patch.object(rs, "_preflight_for_navigation", new_callable=AsyncMock, return_value=30), \
         patch.object(rs, "_wait_agv_arrival", new_callable=AsyncMock), \
         patch.object(rs, "_assert_arm_stowed_for_transport", new_callable=AsyncMock), \
         patch.object(rs, "_assert_agv_stopped", new_callable=AsyncMock):

        await rs.move_robot_to_product.__wrapped__(params)

    # Must have transitioned through these phases in order
    assert "preflight" in phases_seen
    assert "navigate" in phases_seen
    assert "position" in phases_seen
    assert "verify" in phases_seen
    assert phases_seen[-1] == "idle", "must return to idle after completion"


@pytest.mark.asyncio
async def test_phase_returns_to_idle_on_error(mocks):
    """Phase resets to IDLE even when task throws."""
    import app.robot_scripts as rs
    from models.base_types import TaskPhase

    mocks["agv"].status = AsyncMock(return_value={
        "velocity": {"vx_m_s": 0, "vy_m_s": 0}
    })

    with patch.object(rs, "_check_safety_periodic", new_callable=AsyncMock), \
         patch.object(rs, "agv_is_near", new_callable=AsyncMock, return_value=False), \
         patch.object(rs, "_preflight_for_navigation", new_callable=AsyncMock,
                      side_effect=DeviceReadyError("xarm offline")):
        try:
            await rs.move_robot_to_product.__wrapped__(
                SimpleNamespace(location=SimpleNamespace(x_m=1, y_m=1, theta_deg=0, map_id=None),
                                lift_position_cm=0, xarm_joints=None)
            )
        except DeviceReadyError:
            pass

    assert rs._current_phase == TaskPhase.IDLE, "phase must reset to IDLE on error"


@pytest.mark.asyncio
async def test_autotake_phase_execute(mocks):
    """autotake sets phase to EXECUTE and resets to IDLE."""
    import app.robot_scripts as rs
    from models.base_types import TaskPhase

    mocks["agv"].status = AsyncMock(return_value={"velocity": {"vx_m_s": 0, "vy_m_s": 0}})
    mocks["manipulator"].enable_motion = AsyncMock()

    with patch.object(rs, "_assert_agv_stopped", new_callable=AsyncMock), \
         patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value={"baseMove": {"active": False}}), \
         patch.object(rs, "_measure_depth", new_callable=AsyncMock, return_value=100.0), \
         patch.object(rs, "_verify_vacuum", new_callable=AsyncMock, return_value=(True, "ok")):

        phases_seen = []
        _orig = rs._set_phase

        def _track(phase, *, task_id=None):
            phases_seen.append(phase.value)
            _orig(phase, task_id=task_id)

        with patch.object(rs, "_set_phase", side_effect=_track):
            await rs.autotake.__wrapped__(40)

        assert "execute" in phases_seen
        assert rs._current_phase == TaskPhase.IDLE


@pytest.mark.asyncio
async def test_go_to_charging_phase_transitions(mocks):
    """go_to_charging_station transitions through phases and returns to IDLE."""
    import app.robot_scripts as rs
    from models.base_types import TaskPhase

    mocks["agv"].fault_reset = AsyncMock(return_value={})
    mocks["agv"].go_to_charging_station = AsyncMock(return_value={})

    phases_seen = []
    _orig = rs._set_phase

    def _track(phase, *, task_id=None):
        phases_seen.append(phase.value)
        _orig(phase, task_id=task_id)

    with patch.object(rs, "_set_phase", side_effect=_track), \
         patch.object(rs, "_preflight_for_navigation", new_callable=AsyncMock, return_value=30), \
         patch.object(rs, "_assert_arm_stowed_for_transport", new_callable=AsyncMock), \
         patch("aiohttp.ClientSession") as mock_session:

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session_inst = AsyncMock()
        mock_session_inst.put = MagicMock(return_value=mock_resp)
        mock_session_inst.__aenter__ = AsyncMock(return_value=mock_session_inst)
        mock_session_inst.__aexit__ = AsyncMock(return_value=False)
        mock_session.return_value = mock_session_inst

        await rs.go_to_charging_station.__wrapped__(1)

    assert "preflight" in phases_seen
    assert "execute" in phases_seen
    assert phases_seen[-1] == "idle"
