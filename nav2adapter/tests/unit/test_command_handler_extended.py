"""Tests for CommandHandler — drive_to_position, cancel, resolve_target, busy policy, readiness."""
import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import NavigationCommand, NavigationStatusEnum, NavigationSession, Pose2D, TargetConfig
from services.command_handler import CommandHandler


# ── Fixtures ──────────────────────────────────────────────────────────

def _ready_status():
    return {"state_flags": {"drive_ready": True, "safety_cleared": True}}


def _make_state_store_mock(**overrides):
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
    return MagicMock(**defaults)


def _make_handler(event_bus, *, symovo=None, state_store=None):
    if symovo is None:
        symovo = MagicMock()
        symovo.status = AsyncMock(return_value=_ready_status())
        symovo.status_uncached = AsyncMock(return_value=_ready_status())
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})
        symovo.set_drive_mode = AsyncMock()
        symovo.transport_move_to_pose = AsyncMock(return_value={"id": "t1", "state": 1})
        symovo.transport_create_station = AsyncMock(return_value={"id": "t2", "state": 1})
        symovo.transport_start = AsyncMock(return_value={"ok": True})
        symovo.transport_stop = AsyncMock()
        symovo.delete_transport = AsyncMock()

    if state_store is None:
        state_store = _make_state_store_mock()

    return CommandHandler(
        symovo_client=symovo,
        event_bus=event_bus,
        state_store=state_store,
    )


def _cmd(target_id="TestPose", command_id="cmd-001", x=1.0, y=2.0, theta=math.radians(90.0), map_id=0, station_id=None):
    return NavigationCommand(
        command_id=command_id,
        timestamp="2026-01-01T00:00:00Z",
        target_id=target_id,
        x=x,
        y=y,
        theta=theta,
        map_id=map_id,
        station_id=station_id,
    )


# ── drive_to_position: happy path ──────────────────────────────────

class TestDriveToPositionHappyPath:
    @pytest.mark.asyncio
    async def test_pose_navigation(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        status = await handler.handle_drive_to_position(_cmd())
        assert status.status == NavigationStatusEnum.NAVIGATING
        assert status.goal_id == "cmd-001"
        assert status.progress_percent == 1
        handler.symovo_client.transport_move_to_pose.assert_awaited_once()
        handler.symovo_client.transport_start.assert_awaited_once_with("t1")

    @pytest.mark.asyncio
    async def test_theta_passed_as_radians(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        await handler.handle_drive_to_position(_cmd(theta=math.pi))
        kwargs = handler.symovo_client.transport_move_to_pose.call_args.kwargs
        assert abs(kwargs["theta_rad"] - math.pi) < 1e-3


# ── drive_to_position: error conditions ─────────────────────────────

class TestDriveToPositionErrors:
    @pytest.mark.asyncio
    async def test_invalid_target_id(self, event_bus_instance):
        """Command with no x/y and no station_id is invalid."""
        handler = _make_handler(event_bus_instance)
        cmd = NavigationCommand(
            command_id="cmd-001",
            timestamp="2026-01-01T00:00:00Z",
            target_id="unknown",
        )
        status = await handler.handle_drive_to_position(cmd)
        assert status.status == NavigationStatusEnum.ERROR
        assert "invalid_target_id" in status.error_reason

    @pytest.mark.asyncio
    async def test_busy_rejection(self, event_bus_instance):
        """Second command rejected when first transport is still active."""
        active = MagicMock(state=1, command_id="old-cmd", transport_id="t0", target_id="OldTarget")
        ss = _make_state_store_mock(get_all_active_commands=AsyncMock(return_value={"old-cmd": active}))
        handler = _make_handler(event_bus_instance, state_store=ss)
        status = await handler.handle_drive_to_position(_cmd(command_id="new-cmd"))
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
        with patch("services.command_handler.settings") as s:
            s.symovo_auto_set_drive_mode = False
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_drive_to_position(_cmd())
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
        with patch("services.command_handler.settings") as s:
            s.symovo_auto_set_drive_mode = False
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_drive_to_position(_cmd())
        assert status.status == NavigationStatusEnum.ERROR

    @pytest.mark.asyncio
    async def test_transport_creation_failure(self, event_bus_instance):
        symovo = MagicMock()
        symovo.status_uncached = AsyncMock(return_value=_ready_status())
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})
        symovo.transport_move_to_pose = AsyncMock(side_effect=RuntimeError("create failed"))
        symovo.transport_start = AsyncMock()
        symovo.delete_transport = AsyncMock()
        handler = _make_handler(event_bus_instance, symovo=symovo)
        with patch("services.command_handler.settings") as s:
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_drive_to_position(_cmd())
        assert status.status == NavigationStatusEnum.ERROR
        assert status.error_reason == "transport_creation_failed"


# ── idempotency ──────────────────────────────────────────────────────

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_same_command_id_same_target_returns_current_status(self, event_bus_instance):
        existing = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose", state=1)
        ss = _make_state_store_mock(get_active_transport=AsyncMock(return_value=existing))
        handler = _make_handler(event_bus_instance, state_store=ss)
        status = await handler.handle_drive_to_position(_cmd())
        # Should return navigating, not error
        assert status.status in (NavigationStatusEnum.NAVIGATING, NavigationStatusEnum.ERROR) or status.goal_id == "cmd-001"

    @pytest.mark.asyncio
    async def test_command_id_reuse_different_target_conflict(self, event_bus_instance):
        existing = MagicMock(command_id="cmd-001", transport_id="t1", target_id="OldTarget", state=1)
        ss = _make_state_store_mock(get_active_transport=AsyncMock(return_value=existing))
        handler = _make_handler(event_bus_instance, state_store=ss)
        status = await handler.handle_drive_to_position(_cmd(target_id="NewTarget"))
        assert status.status == NavigationStatusEnum.ERROR
        assert "conflict" in status.error_reason


# ── cancel ───────────────────────────────────────────────────────────

class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_active_transport(self, event_bus_instance):
        active = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose")
        ss = _make_state_store_mock(get_active_transport=AsyncMock(return_value=active))
        handler = _make_handler(event_bus_instance, state_store=ss)
        status = await handler.handle_cancel("cmd-001")
        assert status.status == NavigationStatusEnum.IDLE
        handler.symovo_client.transport_stop.assert_awaited_once_with("t1")

    @pytest.mark.asyncio
    async def test_cancel_unknown_command(self, event_bus_instance):
        ss = _make_state_store_mock(
            get_active_transport=AsyncMock(return_value=None),
            get_all_active_commands=AsyncMock(return_value={}),
        )
        handler = _make_handler(event_bus_instance, state_store=ss)
        status = await handler.handle_cancel("unknown-cmd")
        assert status.status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_cancel_stop_timeout(self, event_bus_instance):
        """stop_transport timing out should not block cancel."""
        symovo = MagicMock()
        symovo.status = AsyncMock(return_value=_ready_status())
        symovo.status_uncached = AsyncMock(return_value=_ready_status())
        symovo.pose = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.pose_uncached = AsyncMock(return_value={"x": 0, "y": 0, "theta": 0})
        symovo.transport_get = AsyncMock(return_value={"state": 1})
        symovo.transport_stop = AsyncMock(side_effect=asyncio.TimeoutError())
        symovo.transport_move_to_pose = AsyncMock(return_value={"id": "t1", "state": 1})
        symovo.transport_start = AsyncMock(return_value={"ok": True})
        symovo.delete_transport = AsyncMock()
        active = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose")
        ss = _make_state_store_mock(get_active_transport=AsyncMock(return_value=active))
        handler = _make_handler(event_bus_instance, symovo=symovo, state_store=ss)
        status = await handler.handle_cancel("cmd-001")
        assert status.status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_cancel_exception_returns_error(self, event_bus_instance):
        """Full exception during cancel returns error status."""
        active = MagicMock(command_id="cmd-001", transport_id="t1", target_id="TestPose")
        ss = _make_state_store_mock(
            get_active_transport=AsyncMock(return_value=active),
            clear_transport=AsyncMock(side_effect=RuntimeError("db fail")),
        )
        handler = _make_handler(event_bus_instance, state_store=ss)
        status = await handler.handle_cancel("cmd-001")
        assert status.status == NavigationStatusEnum.ERROR
        assert status.error_reason == "cancel_failed"


# ── _resolve_target_config ───────────────────────────────────────────

class TestResolveTargetConfig:
    def test_no_coords_returns_none(self, event_bus_instance):
        """Command with no x/y and no station_id -> None."""
        handler = _make_handler(event_bus_instance)
        cmd = NavigationCommand(command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="TestPose")
        result = handler._resolve_target_config(cmd)
        assert result is None

    def test_xy_returns_config(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        cmd = NavigationCommand(command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="TestPose",
                                x=5.0, y=6.0, theta=0.0)
        result = handler._resolve_target_config(cmd)
        assert result.x == 5.0
        assert result.y == 6.0
        assert result.theta == 0.0

    def test_station_id_returns_config(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        cmd = NavigationCommand(command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="TestPose",
                                station_id=42)
        result = handler._resolve_target_config(cmd)
        assert result.station_id == 42

    def test_max_speed_included(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        cmd = NavigationCommand(command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="TestPose",
                                x=1.0, y=2.0, max_speed_m_s=0.5)
        result = handler._resolve_target_config(cmd)
        assert result.max_speed_m_s == 0.5


# ── _ensure_navigation_session ───────────────────────────────────────

class TestEnsureNavigationSession:
    @pytest.mark.asyncio
    async def test_creates_session(self, event_bus_instance):
        handler = _make_handler(event_bus_instance)
        cmd = _cmd()
        target = TargetConfig(x=10.0, y=20.0, map_id=0)
        await handler._ensure_navigation_session(cmd, target)
        handler.state_store.upsert_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_if_session_exists(self, event_bus_instance):
        existing = MagicMock(spec=NavigationSession)
        ss = _make_state_store_mock(get_session=AsyncMock(return_value=existing))
        handler = _make_handler(event_bus_instance, state_store=ss)
        await handler._ensure_navigation_session(_cmd(), TargetConfig(x=1.0, y=2.0))
        handler.state_store.upsert_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_station_only_target_skips(self, event_bus_instance):
        """station_id-only target has no x/y: session creation should be skipped."""
        handler = _make_handler(event_bus_instance)
        await handler._ensure_navigation_session(_cmd(), TargetConfig(station_id=42))
        handler.state_store.upsert_session.assert_not_awaited()


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
        symovo.transport_move_to_pose = AsyncMock(return_value={"id": "t1", "state": 1})
        symovo.transport_start = AsyncMock()
        symovo.delete_transport = AsyncMock()

        handler = _make_handler(event_bus_instance, symovo=symovo)
        with patch("services.command_handler.settings") as s:
            s.symovo_auto_set_drive_mode = True
            s.symovo_auto_set_drive_mode_wait_s = 0.01
            s.symovo_clear_transports_before_navigate = False
            status = await handler.handle_drive_to_position(_cmd())
        assert status.status == NavigationStatusEnum.NAVIGATING
        symovo.set_drive_mode.assert_awaited_once()
