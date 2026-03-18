"""Tests for routes/symovo_agv.py — cover remaining uncovered lines/branches."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager

from main import app
from app.dependencies import get_symovo_client, get_state_store
from models.api_types import ErrorStatus


@contextmanager
def _override_symovo(mock_client):
    app.dependency_overrides[get_symovo_client] = lambda: mock_client
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_symovo_client, None)


@contextmanager
def _override_store(mock_store):
    app.dependency_overrides[get_state_store] = lambda: mock_store
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_state_store, None)


# ── /fault_reset — non-list result branch (line 52) ─────────────────

class TestFaultResetNonList:
    def test_non_list_result_returns_500(self, test_client):
        mock_client = MagicMock()
        mock_client.clear_all_transports = AsyncMock(return_value="unexpected")
        with _override_symovo(mock_client):
            resp = test_client.post("/fault_reset")
        assert resp.status_code == 500


# ── /pose — cache miss & error branches ──────────────────────────────

class TestGetPoseGaps:
    def test_pose_cache_miss_non_dict_from_client(self, test_client):
        """When cache is stale and client.pose() returns non-dict -> 500."""
        mock_client = MagicMock()
        mock_client.pose = AsyncMock(return_value="not-a-dict")
        mock_store = MagicMock()
        mock_store.get_last_raw_pose = AsyncMock(return_value=None)
        mock_store.get_last_raw_pose_age_s = AsyncMock(return_value=999)
        with _override_store(mock_store), \
             patch("routes.symovo_agv.settings") as s, \
             _override_symovo(mock_client):
            s.cache_max_age_s = 5.0
            resp = test_client.get("/pose")
        assert resp.status_code == 500

    def test_pose_error_status_returns_502(self, test_client):
        """When normalize returns ErrorStatus -> 502."""
        raw = {"some": "data"}
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.get_last_raw_pose = AsyncMock(return_value=raw)
        mock_store.get_last_raw_pose_age_s = AsyncMock(return_value=0.1)
        with _override_store(mock_store), \
             patch("routes.symovo_agv.settings") as s, \
             patch("routes.symovo_agv.normalize_symovo_status") as norm, \
             _override_symovo(mock_client):
            s.cache_max_age_s = 5.0
            norm.return_value = ErrorStatus(error={"type": "test", "msg": "bad data"})
            resp = test_client.get("/pose")
        assert resp.status_code == 502

    def test_pose_normalized_has_model_dump(self, test_client):
        """When normalized object has model_dump method."""
        raw = {"pose": {"x": 0, "y": 0, "theta": 0}, "id": 1}
        result_obj = MagicMock(spec=[])
        result_obj.model_dump = MagicMock(return_value={"x": 0, "y": 0})
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.get_last_raw_pose = AsyncMock(return_value=raw)
        mock_store.get_last_raw_pose_age_s = AsyncMock(return_value=0.1)
        with _override_store(mock_store), \
             patch("routes.symovo_agv.settings") as s, \
             patch("routes.symovo_agv.normalize_symovo_status") as norm, \
             _override_symovo(mock_client):
            s.cache_max_age_s = 5.0
            norm.return_value = result_obj
            resp = test_client.get("/pose")
        assert resp.status_code == 200


# ── /status — cache miss & error branches ────────────────────────────

class TestGetStatusGaps:
    def test_status_cache_miss_non_dict_from_client(self, test_client):
        mock_client = MagicMock()
        mock_client.status = AsyncMock(return_value="not-dict")
        mock_store = MagicMock()
        mock_store.get_last_raw_status = AsyncMock(return_value=None)
        mock_store.get_last_raw_status_age_s = AsyncMock(return_value=999)
        with _override_store(mock_store), \
             patch("routes.symovo_agv.settings") as s, \
             _override_symovo(mock_client):
            s.cache_max_age_s = 5.0
            resp = test_client.get("/status")
        assert resp.status_code == 500

    def test_status_error_status_returns_502(self, test_client):
        raw = {"id": 1}
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.get_last_raw_status = AsyncMock(return_value=raw)
        mock_store.get_last_raw_status_age_s = AsyncMock(return_value=0.1)
        with _override_store(mock_store), \
             patch("routes.symovo_agv.settings") as s, \
             patch("routes.symovo_agv.normalize_symovo_status") as norm, \
             _override_symovo(mock_client):
            s.cache_max_age_s = 5.0
            norm.return_value = ErrorStatus(error={"type": "test", "msg": "device error"})
            resp = test_client.get("/status")
        assert resp.status_code == 502

    def test_status_normalized_has_model_dump(self, test_client):
        raw = {"id": 1}
        result_obj = MagicMock(spec=[])
        result_obj.model_dump = MagicMock(return_value={"state": "ok"})
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.get_last_raw_status = AsyncMock(return_value=raw)
        mock_store.get_last_raw_status_age_s = AsyncMock(return_value=0.1)
        with _override_store(mock_store), \
             patch("routes.symovo_agv.settings") as s, \
             patch("routes.symovo_agv.normalize_symovo_status") as norm, \
             _override_symovo(mock_client):
            s.cache_max_age_s = 5.0
            norm.return_value = result_obj
            resp = test_client.get("/status")
        assert resp.status_code == 200


# ── Non-dict return branches for various CRUD routes ─────────────────

class TestNonDictBranches:
    def test_charging_stations_returns_dict(self, test_client):
        """When get_charging_stations returns dict -> pass through."""
        mock_client = MagicMock()
        mock_client.get_charging_stations = AsyncMock(return_value={"stations": [{"id": 1}]})
        with _override_symovo(mock_client):
            resp = test_client.get("/charging_stations")
        assert resp.status_code == 200
        assert resp.json()["stations"] == [{"id": 1}]

    def test_go_to_charging_station_non_dict(self, test_client):
        mock_client = MagicMock()
        mock_client.set_charging_station_enabled = AsyncMock(return_value=True)
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_charging_station/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["station_id"] == 1
        assert data["activated"] is True

    def test_get_transport_non_dict(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_get = AsyncMock(return_value="raw-string")
        with _override_symovo(mock_client):
            resp = test_client.get("/transport/1")
        assert resp.status_code == 200
        assert resp.json()["transport"] == "raw-string"

    def test_wait_transport_changes_non_dict(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_wait_for_changes = AsyncMock(return_value="changed")
        with _override_symovo(mock_client):
            resp = test_client.get("/transport/1/wait_for_changes")
        assert resp.status_code == 200
        assert resp.json()["transport"] == "changed"

    def test_get_map_non_dict(self, test_client):
        mock_client = MagicMock()
        mock_client.map = AsyncMock(return_value=["map-data"])
        with _override_symovo(mock_client):
            resp = test_client.get("/map")
        assert resp.status_code == 200
        assert resp.json()["maps"] == ["map-data"]


# ── /go_to_pose — _wait_transport_id branch (line 354) ──────────────

class TestGoToPoseWait:
    def test_go_to_pose_wait_transport_id(self, test_client):
        """When transport returns _wait_transport_id, poll_transport_completion is called."""
        mock_client = MagicMock()
        mock_client.transport_move_to_pose = AsyncMock(
            return_value={"_wait_transport_id": 42, "state": 0}
        )
        mock_client.poll_transport_completion = AsyncMock(
            return_value={"id": 42, "state": 5, "completed": True}
        )
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_pose", json={
                "x_m": 1.0, "y_m": 2.0, "theta_deg": 90.0
            })
        assert resp.status_code == 200
        mock_client.poll_transport_completion.assert_awaited_once_with(42)

    def test_go_to_pose_non_dict_result(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_move_to_pose = AsyncMock(return_value="raw")
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_pose", json={
                "x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0
            })
        assert resp.status_code == 200
        assert resp.json()["result"] == "raw"


# ── v1 map/png error branches ───────────────────────────────────────

class TestV1PngErrors:
    def test_map_png_v1_error(self, test_client):
        mock_client = MagicMock()
        mock_client.map_png = AsyncMock(side_effect=ConnectionError("timeout"))
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/map/0/full.png")
        assert resp.status_code == 503

    def test_map_tile_png_v1_error(self, test_client):
        mock_client = MagicMock()
        mock_client.map_tile_png = AsyncMock(side_effect=RuntimeError("fail"))
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/map/0/1/0/0.png")
        assert resp.status_code == 503

    def test_slam_png_v1_success(self, test_client):
        mock_client = MagicMock()
        mock_client.map_slam_png = AsyncMock(return_value=b"\x89PNG")
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/map/slam/slam.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_slam_png_v1_error(self, test_client):
        mock_client = MagicMock()
        mock_client.map_slam_png = AsyncMock(side_effect=IOError("no slam"))
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/map/slam/slam.png")
        assert resp.status_code == 503

    def test_lidar_scan_png_v1_error(self, test_client):
        mock_client = MagicMock()
        mock_client.scan_png = AsyncMock(side_effect=RuntimeError("nope"))
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/lidar/scan.png")
        assert resp.status_code == 503


# ── v1 map/slam endpoints ───────────────────────────────────────────

class TestV1SlamEndpoints:
    def test_v1_map_list(self, test_client):
        mock_client = MagicMock()
        mock_client.map = AsyncMock(return_value=[{"id": 0}])
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/map")
        assert resp.status_code == 200

    def test_slam_state(self, test_client):
        mock_client = MagicMock()
        mock_client.slam_state = AsyncMock(return_value={"state": "IDLE"})
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/slam/state")
        assert resp.status_code == 200

    def test_slam_pose_station(self, test_client):
        mock_client = MagicMock()
        mock_client.slam_pose_station = AsyncMock(return_value={"x": 0, "y": 0})
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/slam/pose/station")
        assert resp.status_code == 200

    def test_slam_pose_reflector(self, test_client):
        mock_client = MagicMock()
        mock_client.slam_pose_reflector = AsyncMock(return_value={"reflectors": []})
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/slam/pose/reflector")
        assert resp.status_code == 200
