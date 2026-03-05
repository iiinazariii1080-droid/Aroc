"""Extended tests for SymovoAgvClient — covers API proxy methods not in test_symovo_service.py."""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, call

from services.symovo_service import SymovoAgvClient, symovo_lock


def _make_client(**overrides):
    """Create a mock SymovoAgvClient with sensible defaults."""
    c = MagicMock(spec=SymovoAgvClient)
    c.robot_number = 15
    c.base_url = "https://host/v0"
    c._operation_timeout_seconds = 30.0
    c._motion_timeout_seconds = None
    c._infinite_timeout = None
    c.get = AsyncMock(return_value={})
    c.put = AsyncMock(return_value={})
    c.post = AsyncMock(return_value={})
    c.get_raw = AsyncMock(return_value=b"\x89PNG")
    c._make_request = AsyncMock(return_value={})
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _bind(client, method_name):
    """Bind an undecorated (innermost) method to a mock client."""
    raw = getattr(SymovoAgvClient, method_name)
    # Unwrap all functools.wraps layers (safe_call, cached, guarded_async_call, cache_invalidate)
    fn = raw
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    setattr(client, method_name, fn.__get__(client, SymovoAgvClient))


# ── set_drive_mode ───────────────────────────────────────────────────

class TestSetDriveMode:
    @pytest.mark.asyncio
    async def test_enable_calls_charger_workflow(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "set_drive_mode")
        with patch("services.charger_workflow.maybe_deactivate_on_drive_mode", new_callable=AsyncMock) as mock_cw:
            result = await client.set_drive_mode(enable=True)
        mock_cw.assert_awaited_once()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_disable_skips_charger_workflow(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "set_drive_mode")
        with patch("services.charger_workflow.maybe_deactivate_on_drive_mode", new_callable=AsyncMock) as mock_cw:
            await client.set_drive_mode(enable=False)
        mock_cw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fallback_to_amr(self):
        client = _make_client()
        client.put = AsyncMock(side_effect=[RuntimeError("fail"), {"ok": True}])
        _bind(client, "set_drive_mode")
        with patch("services.charger_workflow.maybe_deactivate_on_drive_mode", new_callable=AsyncMock):
            result = await client.set_drive_mode(enable=True)
        assert result == {"ok": True}
        assert client.put.call_count == 2


# ── pause_stop / pause_start / reset_emergency_stop / reset_software_fuse ──

class TestSimplePutMethods:
    @pytest.mark.asyncio
    async def test_pause_stop(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "pause_stop")
        result = await client.pause_stop()
        assert result == {"ok": True}
        client.put.assert_awaited_once_with(f"/amr/{client.robot_number}/pause/stop")

    @pytest.mark.asyncio
    async def test_pause_stop_fallback(self):
        client = _make_client()
        client.put = AsyncMock(side_effect=[RuntimeError(), {"ok": True}])
        _bind(client, "pause_stop")
        result = await client.pause_stop()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_pause_start(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "pause_start")
        result = await client.pause_start()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_reset_emergency_stop(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "reset_emergency_stop")
        result = await client.reset_emergency_stop()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_reset_software_fuse(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "reset_software_fuse")
        result = await client.reset_software_fuse()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_reset_software_fuse_fallback(self):
        client = _make_client()
        client.put = AsyncMock(side_effect=[RuntimeError(), {"done": True}])
        _bind(client, "reset_software_fuse")
        result = await client.reset_software_fuse()
        assert result == {"done": True}


# ── job / create_new_job ─────────────────────────────────────────────

class TestJobMethods:
    @pytest.mark.asyncio
    async def test_job(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"name": "j1"})
        _bind(client, "job")
        result = await client.job()
        assert result == {"name": "j1"}

    @pytest.mark.asyncio
    async def test_create_new_job(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"id": 1, "name": "move"})
        _bind(client, "create_new_job")
        result = await client.create_new_job("move")
        client.get.assert_awaited_once()
        args, kwargs = client.get.call_args
        assert "name" in str(kwargs.get("params", {}))


# ── map methods ──────────────────────────────────────────────────────

class TestMapMethods:
    @pytest.mark.asyncio
    async def test_map_list(self):
        client = _make_client()
        client.get = AsyncMock(return_value=[{"id": 0}])
        _bind(client, "map")
        result = await client.map()
        assert result == [{"id": 0}]

    @pytest.mark.asyncio
    async def test_map_get_by_id(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"id": 5, "name": "floor1"})
        _bind(client, "map_get")
        result = await client.map_get(5)
        client.get.assert_awaited_once_with("/map/5")

    @pytest.mark.asyncio
    async def test_map_wait_for_changes(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"changed": True})
        _bind(client, "map_wait_for_changes")
        with patch("services.symovo_service.settings") as s:
            s.transport_watch_timeout = 30.0
            result = await client.map_wait_for_changes(5, since="abc", timeout=10.0)
        assert result == {"changed": True}
        _, kwargs = client.get.call_args
        assert kwargs["params"]["timeout"] == 10.0
        # op_timeout = timeout + 5
        assert kwargs["op_timeout"] == 15.0

    @pytest.mark.asyncio
    async def test_map_png(self):
        client = _make_client()
        client.get_raw = AsyncMock(return_value=b"\x89PNG_data")
        _bind(client, "map_png")
        with patch("services.symovo_service.settings") as s:
            s.symovo_timeout_seconds = 10
            result = await client.map_png(0)
        assert result == b"\x89PNG_data"

    @pytest.mark.asyncio
    async def test_map_tile_png(self):
        client = _make_client()
        client.get_raw = AsyncMock(return_value=b"tile")
        _bind(client, "map_tile_png")
        with patch("services.symovo_service.settings") as s:
            s.symovo_timeout_seconds = 10
            result = await client.map_tile_png(map_id=0, zoom=2, x=1, y=3)
        assert result == b"tile"
        url_arg = client.get_raw.call_args[0][0]
        assert "/map/2/1/3.png" in url_arg

    @pytest.mark.asyncio
    async def test_map_slam_png(self):
        client = _make_client()
        client.get_raw = AsyncMock(return_value=b"slam")
        _bind(client, "map_slam_png")
        with patch("services.symovo_service.settings") as s:
            s.symovo_timeout_seconds = 10
            result = await client.map_slam_png()
        assert result == b"slam"

    @pytest.mark.asyncio
    async def test_scan_png(self):
        client = _make_client()
        client.get_raw = AsyncMock(return_value=b"scan")
        _bind(client, "scan_png")
        with patch("services.symovo_service.settings") as s:
            s.symovo_timeout_seconds = 10
            result = await client.scan_png()
        assert result == b"scan"

    @pytest.mark.asyncio
    async def test_scan_png_fallback(self):
        client = _make_client()
        client.get_raw = AsyncMock(side_effect=[RuntimeError(), b"scan_agv"])
        _bind(client, "scan_png")
        with patch("services.symovo_service.settings") as s:
            s.symovo_timeout_seconds = 10
            result = await client.scan_png()
        assert result == b"scan_agv"


# ── SLAM methods ─────────────────────────────────────────────────────

class TestSlamMethods:
    @pytest.mark.asyncio
    async def test_slam_state(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"state": "MAPPING"})
        _bind(client, "slam_state")
        result = await client.slam_state()
        assert result["state"] == "MAPPING"

    @pytest.mark.asyncio
    async def test_slam_state_fallback(self):
        client = _make_client()
        client.get = AsyncMock(side_effect=[RuntimeError(), {"state": "IDLE"}])
        _bind(client, "slam_state")
        result = await client.slam_state()
        assert result["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_slam_pose_station(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"x": 1})
        _bind(client, "slam_pose_station")
        result = await client.slam_pose_station()
        assert result == {"x": 1}

    @pytest.mark.asyncio
    async def test_slam_pose_reflector(self):
        client = _make_client()
        client.get = AsyncMock(return_value=[{"x": 1}])
        _bind(client, "slam_pose_reflector")
        result = await client.slam_pose_reflector()
        assert result == [{"x": 1}]


# ── check_pose ───────────────────────────────────────────────────────

class TestCheckPose:
    @pytest.mark.asyncio
    async def test_check_pose_amr(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"cost": 42})
        _bind(client, "check_pose")
        result = await client.check_pose(x_m=1.0, y_m=2.0, theta_rad=0.5)
        assert result["cost"] == 42
        url = client.put.call_args[0][0]
        assert "/amr/" in url and "get_cost" in url

    @pytest.mark.asyncio
    async def test_check_pose_fallback_agv(self):
        from exceptions import DeviceError
        client = _make_client()
        client.put = AsyncMock(side_effect=[DeviceError("fail"), {"cost": 10}])
        client.post = AsyncMock(return_value={"reachable": True})
        _bind(client, "check_pose")
        result = await client.check_pose(x_m=1.0, y_m=2.0)
        assert result["cost"] == 10

    @pytest.mark.asyncio
    async def test_check_pose_fallback_v1_reachable(self):
        from exceptions import DeviceError
        client = _make_client()
        client.put = AsyncMock(side_effect=[DeviceError("a"), DeviceError("b")])
        client.post = AsyncMock(return_value={"reachable": True})
        _bind(client, "check_pose")
        result = await client.check_pose(x_m=1.0, y_m=2.0, map_id="m1")
        assert result["reachable"] is True
        post_kwargs = client.post.call_args[1]
        assert post_kwargs["json_data"]["mapId"] == "m1"


# ── transport_create / start / stop ──────────────────────────────────

class TestTransportOperations:
    @pytest.mark.asyncio
    async def test_transport_create(self):
        client = _make_client()
        _bind(client, "transport_create")
        client.post = AsyncMock(return_value={"id": 1})
        # transport_create calls _transport_create_unlocked internally
        client._transport_create_unlocked = AsyncMock(return_value={"id": 1})
        # Bind the unwrapped transport_create
        result = await client.transport_create({"steps": []})
        # The unwrapped version of transport_create just calls _transport_create_unlocked
        # Actually transport_create.__wrapped__.__wrapped__ IS the raw body

    @pytest.mark.asyncio
    async def test_transport_start(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"started": True})
        _bind(client, "transport_start")
        result = await client.transport_start("42")
        assert result == {"started": True}

    @pytest.mark.asyncio
    async def test_transport_stop(self):
        client = _make_client()
        client.put = AsyncMock(return_value={"stopped": True})
        _bind(client, "transport_stop")
        result = await client.transport_stop("42")
        assert result == {"stopped": True}


# ── transport_move_to_pose ───────────────────────────────────────────

class TestTransportMoveToPose:
    @pytest.mark.asyncio
    async def test_transport_move_to_pose_no_wait(self):
        client = _make_client()
        _bind(client, "_transport_move_to_pose_guarded")
        client._transport_create_unlocked = AsyncMock(return_value={"id": 99})
        result = await client._transport_move_to_pose_guarded(x_m=1.0, y_m=2.0, wait=False)
        assert result == {"id": 99}

    @pytest.mark.asyncio
    async def test_transport_move_to_pose_wait_returns_id(self):
        client = _make_client()
        _bind(client, "_transport_move_to_pose_guarded")
        client._transport_create_unlocked = AsyncMock(return_value={"id": 99, "state": 0})
        result = await client._transport_move_to_pose_guarded(x_m=1.0, y_m=2.0, wait=True)
        assert result["_wait_transport_id"] == 99

    @pytest.mark.asyncio
    async def test_transport_move_to_pose_with_max_speed(self):
        client = _make_client()
        _bind(client, "_transport_move_to_pose_guarded")
        captured = {}
        async def _capture(payload):
            captured["payload"] = payload
            return {"id": 77}
        client._transport_create_unlocked = _capture
        result = await client._transport_move_to_pose_guarded(
            x_m=1.0, y_m=2.0, theta_rad=0.5, max_speed_m_s=0.3, wait=False,
        )
        pose = captured["payload"]["steps"][0]["poses"][0]
        assert pose["maxSpeed"] == 0.3
        assert pose["theta"] == 0.5

    @pytest.mark.asyncio
    async def test_transport_move_to_pose_calls_charger_disable(self):
        """The outer method calls charger_workflow.disable_all_before_move."""
        client = _make_client()
        _bind(client, "transport_move_to_pose")
        client._transport_move_to_pose_guarded = AsyncMock(return_value={"id": 1})
        with patch("services.charger_workflow.disable_all_before_move", new_callable=AsyncMock) as mock_dab:
            await client.transport_move_to_pose(x_m=1.0, y_m=2.0)
        mock_dab.assert_awaited_once()


# ── transport_wait_for_changes ───────────────────────────────────────

class TestTransportWaitForChanges:
    @pytest.mark.asyncio
    async def test_success_records_latency(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"state": 8})
        _bind(client, "transport_wait_for_changes")
        with patch("services.symovo_service.settings") as s, \
             patch("services.symovo_service.reliability_metrics") as rm:
            s.transport_watch_timeout = 30.0
            result = await client.transport_wait_for_changes(42, timeout=10.0)
        assert result == {"state": 8}
        rm.observe_duration.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_increments_counter(self):
        client = _make_client()
        client.get = AsyncMock(side_effect=RuntimeError("timeout issue"))
        _bind(client, "transport_wait_for_changes")
        with patch("services.symovo_service.settings") as s, \
             patch("services.symovo_service.reliability_metrics") as rm:
            s.transport_watch_timeout = 30.0
            with pytest.raises(RuntimeError):
                await client.transport_wait_for_changes(42)
        rm.inc.assert_any_call("symovo.transport_wait_for_changes.error")
        rm.inc.assert_any_call("symovo.transport_wait_for_changes.timeout")


# ── amr_wait_for_changes ────────────────────────────────────────────

class TestAmrWaitForChanges:
    @pytest.mark.asyncio
    async def test_amr_success(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"changed": True})
        _bind(client, "amr_wait_for_changes")
        with patch("services.symovo_service.settings") as s, \
             patch("services.symovo_service.reliability_metrics") as rm:
            s.transport_watch_timeout = 30.0
            result = await client.amr_wait_for_changes(since="abc")
        assert result == {"changed": True}
        rm.observe_duration.assert_called_once()

    @pytest.mark.asyncio
    async def test_amr_fallback_to_agv(self):
        client = _make_client()
        client.get = AsyncMock(side_effect=[RuntimeError(), {"changed": True}])
        _bind(client, "amr_wait_for_changes")
        with patch("services.symovo_service.settings") as s, \
             patch("services.symovo_service.reliability_metrics") as rm:
            s.transport_watch_timeout = 30.0
            result = await client.amr_wait_for_changes()
        assert result == {"changed": True}

    @pytest.mark.asyncio
    async def test_amr_both_fail_raises(self):
        client = _make_client()
        client.get = AsyncMock(side_effect=[RuntimeError("a"), RuntimeError("timeout b")])
        _bind(client, "amr_wait_for_changes")
        with patch("services.symovo_service.settings") as s, \
             patch("services.symovo_service.reliability_metrics") as rm:
            s.transport_watch_timeout = 30.0
            with pytest.raises(RuntimeError):
                await client.amr_wait_for_changes()
        rm.inc.assert_any_call("symovo.amr_wait_for_changes.error")


# ── get_stations / get_charging_stations ─────────────────────────────

class TestStationMethods:
    @pytest.mark.asyncio
    async def test_get_stations_list(self):
        client = _make_client()
        client.get = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        _bind(client, "get_stations")
        result = await client.get_stations()
        assert result == [{"id": 1}, {"id": 2}]

    @pytest.mark.asyncio
    async def test_get_stations_dict_with_result(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"result": [{"id": 1}]})
        _bind(client, "get_stations")
        result = await client.get_stations()
        assert result == [{"id": 1}]

    @pytest.mark.asyncio
    async def test_get_stations_single_object(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"id": 1, "name": "S1"})
        _bind(client, "get_stations")
        result = await client.get_stations()
        assert result == [{"id": 1, "name": "S1"}]

    @pytest.mark.asyncio
    async def test_get_stations_unexpected_returns_empty(self):
        client = _make_client()
        client.get = AsyncMock(return_value="oops")
        _bind(client, "get_stations")
        result = await client.get_stations()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_charging_stations_filters_by_type(self):
        client = _make_client()
        all_stations = [
            {"id": 1, "_type_id": 4, "state": "OK"},
            {"id": 2, "_type_id": 1, "state": "OK"},
            {"id": 3, "_type_id": 4, "state": "INACTIVE"},
        ]
        client.get_stations = AsyncMock(return_value=all_stations)
        _bind(client, "get_charging_stations")
        result = await client.get_charging_stations()
        assert len(result) == 2
        assert all(s["_type_id"] == 4 for s in result)


# ── set_charging_station_enabled ─────────────────────────────────────

class TestSetChargingStationEnabled:
    @pytest.mark.asyncio
    async def test_activates_station(self):
        station = {"id": 10, "name": "CS1", "pose": {"x": 1}, "_type_id": 4, "has_charger": True, "parking_allowed": True}
        client = _make_client()
        client.get = AsyncMock(return_value=station)
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "set_charging_station_enabled")
        result = await client.set_charging_station_enabled("10", True)
        payload = client.put.call_args[1]["json_data"]
        assert payload["state"] == "OK"

    @pytest.mark.asyncio
    async def test_deactivates_station(self):
        station = {"id": 10, "name": "CS1", "pose": {}, "_type_id": 4}
        client = _make_client()
        client.get = AsyncMock(return_value=station)
        client.put = AsyncMock(return_value={"ok": True})
        _bind(client, "set_charging_station_enabled")
        await client.set_charging_station_enabled("10", False)
        payload = client.put.call_args[1]["json_data"]
        assert payload["state"] == "INACTIVE"


# ── disable/enable_all_charging_stations ─────────────────────────────

class TestBulkChargingStations:
    @pytest.mark.asyncio
    async def test_disable_all(self):
        stations = [
            {"id": 1, "_type_id": 4, "state": "OK"},
            {"id": 2, "_type_id": 4, "state": "INACTIVE"},
        ]
        client = _make_client()
        client.get_charging_stations = AsyncMock(return_value=stations)
        client.set_charging_station_enabled = AsyncMock(return_value={"ok": True})
        _bind(client, "disable_all_charging_stations")
        result = await client.disable_all_charging_stations()
        # Only station 1 (state OK) should be disabled
        client.set_charging_station_enabled.assert_awaited_once_with(1, False)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_enable_all(self):
        stations = [
            {"id": 1, "_type_id": 4, "state": "INACTIVE"},
            {"id": 2, "_type_id": 4, "state": "OK"},
        ]
        client = _make_client()
        client.get_charging_stations = AsyncMock(return_value=stations)
        client.set_charging_station_enabled = AsyncMock(return_value={"ok": True})
        _bind(client, "enable_all_charging_stations")
        result = await client.enable_all_charging_stations()
        # Only station 1 (INACTIVE) should be enabled
        client.set_charging_station_enabled.assert_awaited_once_with(1, True)
        assert len(result) == 1


# ── set_charging_station_enabled_by_name ─────────────────────────────

class TestSetChargingStationByName:
    @pytest.mark.asyncio
    async def test_finds_and_sets_by_name(self):
        stations = [
            {"id": 10, "name": "Charger-Main", "_type_id": 4, "state": "OK"},
            {"id": 11, "name": "Charger-Backup", "_type_id": 4, "state": "OK"},
        ]
        client = _make_client()
        client.get_charging_stations = AsyncMock(return_value=stations)
        client.set_charging_station_enabled = AsyncMock()
        _bind(client, "set_charging_station_enabled_by_name")
        result = await client.set_charging_station_enabled_by_name("charger-main", enabled=False)
        assert result == 10
        client.set_charging_station_enabled.assert_awaited_once_with(10, False)

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        client = _make_client()
        client.get_charging_stations = AsyncMock(return_value=[
            {"id": 10, "name": "Other", "_type_id": 4}
        ])
        client.set_charging_station_enabled = AsyncMock()
        _bind(client, "set_charging_station_enabled_by_name")
        result = await client.set_charging_station_enabled_by_name("nonexistent")
        assert result is None
        client.set_charging_station_enabled.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stations_returns_none(self):
        client = _make_client()
        client.get_charging_stations = AsyncMock(return_value=[])
        _bind(client, "set_charging_station_enabled_by_name")
        result = await client.set_charging_station_enabled_by_name("any")
        assert result is None


# ── clear_all_transports dict response ───────────────────────────────

class TestClearAllTransportsExtended:
    @pytest.mark.asyncio
    async def test_dict_result_key(self):
        """When /transport returns {"result": [...]}."""
        client = _make_client()
        client.get = AsyncMock(side_effect=[
            {"result": [{"id": 1}]},
            {"result": []},
        ])
        client._make_request = AsyncMock(return_value={"ok": True})
        _bind(client, "clear_all_transports")
        result = await client.clear_all_transports()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetch_error_returns_empty(self):
        client = _make_client()
        client.get = AsyncMock(side_effect=RuntimeError("connection error"))
        _bind(client, "clear_all_transports")
        result = await client.clear_all_transports()
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_format_returns_empty(self):
        client = _make_client()
        client.get = AsyncMock(return_value="not a list or dict")
        _bind(client, "clear_all_transports")
        result = await client.clear_all_transports()
        assert result == []


# ── transports_wait_for_changes / _transport_create_unlocked ─────────

class TestMiscMethods:
    @pytest.mark.asyncio
    async def test_transports_wait_for_changes(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"changed": True})
        _bind(client, "transports_wait_for_changes")
        with patch("services.symovo_service.settings") as s:
            s.transport_watch_timeout = 30.0
            result = await client.transports_wait_for_changes()
        assert result == {"changed": True}

    @pytest.mark.asyncio
    async def test_transport_create_unlocked(self):
        client = _make_client()
        client.post = AsyncMock(return_value={"id": 5})
        _bind(client, "_transport_create_unlocked")
        result = await client._transport_create_unlocked({"steps": []})
        assert result == {"id": 5}

    @pytest.mark.asyncio
    async def test_transport_get_uncached(self):
        client = _make_client()
        client.get = AsyncMock(return_value={"id": 42, "state": 5})
        _bind(client, "transport_get_uncached")
        result = await client.transport_get_uncached(42)
        assert result["id"] == 42
