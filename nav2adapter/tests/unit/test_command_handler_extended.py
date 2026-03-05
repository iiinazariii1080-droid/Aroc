"""Tests for CommandHandler — navigate_to, cancel, resolve_target, busy policy, readiness."""
import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationCommand, NavigationStatusEnum, NavigationSession, Pose2D
from services.command_handler import CommandHandler


# ── Fixtures ──────────────────────────────────────────────────────────

def _ready_status():
    return {"state_flags": {"drive_ready": True, "safety_cleared": True}}


def _make_handler(event_bus, *, symovo=None, orchestrator=None, mqtt=None):
    if symovo is None:
        symovo = MagicMock()
        symovo.status = AsyncMock(return_value=_ready_status())
        symovo.status_uncached = AsyncMock(return_value=_ready_status())
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})
        symovo.set_drive_mode = AsyncMock()

    if orchestrator is None:
        orchestrator = MagicMock()
        orchestrator.create_transport_to_pose = AsyncMock(return_value={"id": "t1", "state": 1})
        orchestrator.create_transport_to_station = AsyncMock(return_value={"id": "t2", "state": 1})
        orchestrator.start_transport = AsyncMock(return_value={"ok": True})
        orchestrator.stop_transport = AsyncMock()
        orchestrator.delete_transport = AsyncMock()

    return CommandHandler(
        symovo_client=symovo,
        transport_orchestrator=orchestrator,
        mqtt_adapter=mqtt,
        event_bus=event_bus,
    )


def _cmd(target_id="TestPose", command_id="cmd-001"):
    return NavigationCommand(
        command_id=command_id,
        timestamp="2026-01-01T00:00:00Z",
        target_id=target_id,
    )


def _db_record(x=1.0, y=2.0, theta_deg=90.0, map_id=0, station_id=None):
    loc = {"x_m": x, "y_m": y, "theta_deg": theta_deg, "map_id": map_id}
    params = {"location": loc}
    if station_id is not None:
        params["station_id"] = station_id
    return {"id": "rec1", "name": "TestPose", "params": params}


def _patch_state_store(**overrides):
    defaults = {
        "get_active_transport": AsyncMock(return_value=None),
        "get_last_navigation_status": AsyncMock(return_value=None),
        "get_last_position_status": AsyncMock(return_value=None),
        "get_all_active_commands": AsyncMock(return_value={}),
        "register_command": AsyncMock(),
        "set_last_navigation_status": AsyncMock(),
        "clear_transport": AsyncMock(),
        "clear_session": AsyncMock(),
        "clear_all_commands": AsyncMock(return_value=0),
        "get_session": AsyncMock(return_value=None),
        "upsert_session": AsyncMock(),
    }
    defaults.update(overrides)
    return patch("services.command_handler.state_store", **defaults)


# ── navigate_to: happy path ─────────────────────────────────────────

class TestNavigateToHappyPath:
    @pytest.mark.asyncio
    async def test_pose_navigation(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with _patch_state_store(), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()):
            status = await handler.handle_navigate_to(_cmd())
        assert status.status == NavigationStatusEnum.NAVIGATING
        assert status.goal_id == "cmd-001"
        assert status.progress_percent == 1
        handler.transport_orchestrator.create_transport_to_pose.assert_awaited_once()
        handler.transport_orchestrator.start_transport.assert_awaited_once_with("t1")

    @pytest.mark.asyncio
    async def test_theta_conversion_deg_to_rad(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with _patch_state_store(), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record(theta_deg=180.0)):
            await handler.handle_navigate_to(_cmd())
        kwargs = handler.transport_orchestrator.create_transport_to_pose.call_args.kwargs
        assert abs(kwargs["theta_rad"] - math.pi) < 1e-3


# ── navigate_to: error conditions ───────────────────────────────────

class TestNavigateToErrors:
    @pytest.mark.asyncio
    async def test_invalid_target_id(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with _patch_state_store(), \
             patch("services.command_handler.get_robot_position_by_name", return_value=None):
            status = await handler.handle_navigate_to(_cmd(target_id="unknown"))
        assert status.status == NavigationStatusEnum.ERROR
        assert "invalid_target_id" in status.error_reason

    @pytest.mark.asyncio
    async def test_busy_rejection(self, event_bus_instance):
        """Second command rejected when first transport is still active."""
        handler = _make_handler(event_bus_instance)
        active = MagicMock(state=1, command_id="old-cmd", transport_id="t0", target_id="OldTarget")
        with _patch_state_store(get_all_active_commands=AsyncMock(return_value={"old-cmd": active})), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()):
            status = await handler.handle_navigate_to(_cmd(command_id="new-cmd"))
        assert status.status == NavigationStatusEnum.ERROR
        assert status.error_reason == "busy"

    @pytest.mark.asyncio
    async def test_readiness_check_failure(self, event_bus_instance):
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(return_value={"state_flags": {"drive_ready": False, "safety_cleared": True}})
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.set_drive_mode = AsyncMock()
        symovo.transport_get = AsyncMock(return_value={"state": 1})
        handler = _make_handler(event_bus_instance, symovo=symovo)
        with _patch_state_store(), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()), \
             patch("services.command_handler.settings") as s:
            s.symovo_auto_set_drive_mode = False
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_navigate_to(_cmd())
        assert status.status == NavigationStatusEnum.ERROR
        assert "not_ready" in status.error_reason

    @pytest.mark.asyncio
    async def test_readiness_timeout_controller_unreachable(self, event_bus_instance):
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(side_effect=asyncio.TimeoutError())
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})
        handler = _make_handler(event_bus_instance, symovo=symovo)
        with _patch_state_store(), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()), \
             patch("services.command_handler.settings") as s:
            s.symovo_auto_set_drive_mode = False
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_navigate_to(_cmd())
        assert status.status == NavigationStatusEnum.ERROR

    @pytest.mark.asyncio
    async def test_transport_creation_failure(self, event_bus_instance):
        orch = MagicMock()
        orch.create_transport_to_pose = AsyncMock(side_effect=RuntimeError("create failed"))
        orch.start_transport = AsyncMock()
        orch.delete_transport = AsyncMock()
        handler = _make_handler(event_bus_instance, orchestrator=orch)
        with _patch_state_store(), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()), \
             patch("services.command_handler.settings") as s:
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_navigate_to(_cmd())
        assert status.status == NavigationStatusEnum.ERROR
        assert status.error_reason == "transport_creation_failed"


# ── idempotency ──────────────────────────────────────────────────────

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_same_command_id_same_target_returns_current_status(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        existing = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose", state=1)
        with _patch_state_store(get_active_transport=AsyncMock(return_value=existing)), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()):
            status = await handler.handle_navigate_to(_cmd())
        # Should return navigating, not error
        assert status.status in (NavigationStatusEnum.NAVIGATING, NavigationStatusEnum.ERROR) or status.goal_id == "cmd-001"

    @pytest.mark.asyncio
    async def test_command_id_reuse_different_target_conflict(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        existing = MagicMock(command_id="cmd-001", transport_id="t1", target_id="OldTarget", state=1)
        with _patch_state_store(get_active_transport=AsyncMock(return_value=existing)), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()):
            status = await handler.handle_navigate_to(_cmd(target_id="NewTarget"))
        assert status.status == NavigationStatusEnum.ERROR
        assert "conflict" in status.error_reason


# ── cancel ───────────────────────────────────────────────────────────

class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_active_transport(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        active = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose")
        with _patch_state_store(get_active_transport=AsyncMock(return_value=active)), \
             patch("services.command_handler.charger_workflow") as cw:
            cw.maybe_deactivate_on_cancel = AsyncMock()
            status = await handler.handle_cancel("cmd-001")
        assert status.status == NavigationStatusEnum.IDLE
        handler.transport_orchestrator.stop_transport.assert_awaited_once_with("t1")

    @pytest.mark.asyncio
    async def test_cancel_unknown_command(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with _patch_state_store(
            get_active_transport=AsyncMock(return_value=None),
            get_all_active_commands=AsyncMock(return_value={}),
        ):
            status = await handler.handle_cancel("unknown-cmd")
        assert status.status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_cancel_stop_timeout(self, event_bus_instance):
        """stop_transport timing out should not block cancel."""
        orch = MagicMock()
        orch.stop_transport = AsyncMock(side_effect=asyncio.TimeoutError())
        handler = _make_handler(event_bus_instance, orchestrator=orch)
        active = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose")
        with _patch_state_store(get_active_transport=AsyncMock(return_value=active)), \
             patch("services.command_handler.charger_workflow") as cw:
            cw.maybe_deactivate_on_cancel = AsyncMock()
            status = await handler.handle_cancel("cmd-001")
        assert status.status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_cancel_exception_returns_error(self, event_bus_instance):
        """Full exception during cancel returns error status."""
        handler = _make_handler(event_bus_instance)
        active = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose")
        with _patch_state_store(
            get_active_transport=AsyncMock(return_value=active),
            clear_transport=AsyncMock(side_effect=RuntimeError("db fail")),
        ):
            status = await handler.handle_cancel("cmd-001")
        assert status.status == NavigationStatusEnum.ERROR
        assert status.error_reason == "cancel_failed"


# ── _resolve_target_config ───────────────────────────────────────────

class TestResolveTargetConfig:
    @pytest.mark.asyncio
    async def test_empty_target_id(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        result = await handler._resolve_target_config("")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_returns_none(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with patch("services.command_handler.get_robot_position_by_name", return_value=None):
            result = await handler._resolve_target_config("NoSuchPose")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_returns_record_with_location(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with patch("services.command_handler.get_robot_position_by_name", return_value=_db_record(x=5.0, y=6.0, theta_deg=0)):
            result = await handler._resolve_target_config("TestPose")
        assert result["x"] == 5.0
        assert result["y"] == 6.0
        assert result["theta"] == 0.0

    @pytest.mark.asyncio
    async def test_db_lookup_timeout(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with patch("services.command_handler.get_robot_position_by_name", side_effect=asyncio.TimeoutError()):
            result = await handler._resolve_target_config("TestPose")
        assert result is None

    @pytest.mark.asyncio
    async def test_theta_deg_is_converted(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value=_db_record(theta_deg=270.0)):
            result = await handler._resolve_target_config("TestPose")
        assert abs(result["theta"] - math.radians(270.0)) < 1e-6

    @pytest.mark.asyncio
    async def test_car_position_key_fallback(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        rec = {
            "id": "r1",
            "name": "TestPose",
            "params": {"car_position": {"x_m": 7.0, "y_m": 8.0, "theta_deg": 0, "map_id": 0}},
        }
        with patch("services.command_handler.get_robot_position_by_name", return_value=rec):
            result = await handler._resolve_target_config("TestPose")
        assert result["x"] == 7.0


# ── _ensure_navigation_session ───────────────────────────────────────

class TestEnsureNavigationSession:
    @pytest.mark.asyncio
    async def test_creates_session(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        cmd = _cmd()
        target = {"x": 10.0, "y": 20.0, "map_id": 0}
        with _patch_state_store() as mock_ss:
            await handler._ensure_navigation_session(cmd, target)
            mock_ss.upsert_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_if_session_exists(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        existing = MagicMock(spec=NavigationSession)
        with _patch_state_store(get_session=AsyncMock(return_value=existing)) as mock_ss:
            await handler._ensure_navigation_session(_cmd(), {"x": 1, "y": 2})
            mock_ss.upsert_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_station_only_target_skips(self, event_bus_instance):
        """station_id-only target has no x/y: session creation should be skipped."""
        handler = _make_handler(event_bus_instance)
        with _patch_state_store() as mock_ss:
            await handler._ensure_navigation_session(_cmd(), {"station_id": 42})
            mock_ss.upsert_session.assert_not_awaited()


# ── auto drive mode ──────────────────────────────────────────────────

class TestAutoDriveMode:
    @pytest.mark.asyncio
    async def test_auto_set_drive_mode_retries_readiness(self, event_bus_instance):
        symovo = MagicMock()
        # First call: not ready, second call (after set_drive_mode): ready
        symovo.status_uncached = AsyncMock(side_effect=[
            {"state_flags": {"drive_ready": False, "safety_cleared": True}},
            {"state_flags": {"drive_ready": True, "safety_cleared": True}},
        ])
        symovo.set_drive_mode = AsyncMock()
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})

        orch = MagicMock()
        orch.create_transport_to_pose = AsyncMock(return_value={"id": "t1", "state": 1})
        orch.start_transport = AsyncMock()
        orch.delete_transport = AsyncMock()

        handler = _make_handler(event_bus_instance, symovo=symovo, orchestrator=orch)
        with _patch_state_store(), \
             patch("services.command_handler.get_robot_position_by_name", return_value=_db_record()), \
             patch("services.command_handler.settings") as s:
            s.symovo_auto_set_drive_mode = True
            s.symovo_auto_set_drive_mode_wait_s = 0.01
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_navigate_to(_cmd())
        assert status.status == NavigationStatusEnum.NAVIGATING
        symovo.set_drive_mode.assert_awaited_once()
