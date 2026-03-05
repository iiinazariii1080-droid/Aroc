"""Tests for routes/robot_positions.py — CRUD route handlers."""
import pytest
from unittest.mock import patch, MagicMock
import sqlite3

from fastapi.testclient import TestClient
from main import app


# ── GET /robot_positions/list ────────────────────────────────────────

class TestGetList:
    def test_returns_list(self, test_client):
        with patch("routes.robot_positions.get_robot_positions_list", return_value=[
            {"id": "p1", "name": "A", "params": {"x": 1}},
        ]):
            resp = test_client.get("/robot_positions/list")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_limit_param(self, test_client):
        rows = [{"id": f"p{i}", "name": f"N{i}", "params": {}} for i in range(10)]
        with patch("routes.robot_positions.get_robot_positions_list", return_value=rows):
            resp = test_client.get("/robot_positions/list?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_empty_list(self, test_client):
        with patch("routes.robot_positions.get_robot_positions_list", return_value=[]):
            resp = test_client.get("/robot_positions/list")
        assert resp.status_code == 200
        assert resp.json() == []


# ── POST /robot_positions/save ───────────────────────────────────────

class TestSave:
    def test_save_with_id(self, test_client):
        with patch("routes.robot_positions.save_robot_position", return_value=True):
            resp = test_client.post("/robot_positions/save", json={
                "id": "p1",
                "name": "Test Position",
                "params": {"location": {"x_m": 1.0, "y_m": 2.0}},
            })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ok"
        assert data["id"] == "p1"

    def test_save_without_id_generates_one(self, test_client):
        with patch("routes.robot_positions.save_robot_position", return_value=True):
            resp = test_client.post("/robot_positions/save", json={
                "name": "Auto ID",
                "params": {"location": {"x_m": 0.0, "y_m": 0.0}},
            })
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_save_db_error_503(self, test_client):
        with patch("routes.robot_positions.save_robot_position",
                    side_effect=sqlite3.OperationalError("locked")):
            resp = test_client.post("/robot_positions/save", json={
                "name": "Test",
                "params": {"location": {"x_m": 0.0, "y_m": 0.0}},
            })
        assert resp.status_code == 503

    def test_save_value_error_400(self, test_client):
        with patch("routes.robot_positions.save_robot_position",
                    side_effect=ValueError("duplicate name")):
            resp = test_client.post("/robot_positions/save", json={
                "name": "Test",
                "params": {"location": {"x_m": 0.0, "y_m": 0.0}},
            })
        assert resp.status_code == 400

    def test_save_empty_name_422(self, test_client):
        """Empty name should fail validation."""
        resp = test_client.post("/robot_positions/save", json={
            "name": "",
            "params": {"location": {"x_m": 0.0, "y_m": 0.0}},
        })
        assert resp.status_code == 422


# ── DELETE /robot_positions/{position_id} ────────────────────────────

class TestDelete:
    def test_delete_success(self, test_client):
        with patch("routes.robot_positions.delete_robot_position", return_value=True):
            resp = test_client.delete("/robot_positions/p1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_delete_not_found(self, test_client):
        with patch("routes.robot_positions.delete_robot_position", return_value=False):
            resp = test_client.delete("/robot_positions/nope")
        assert resp.status_code == 404

    def test_delete_db_error_503(self, test_client):
        with patch("routes.robot_positions.delete_robot_position",
                    side_effect=sqlite3.OperationalError("locked")):
            resp = test_client.delete("/robot_positions/p1")
        assert resp.status_code == 503

    def test_delete_generic_error_400(self, test_client):
        with patch("routes.robot_positions.delete_robot_position",
                    side_effect=RuntimeError("bad")):
            resp = test_client.delete("/robot_positions/p1")
        assert resp.status_code == 400
