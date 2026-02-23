"""
Integration tests for /robot_positions endpoints in main.py.
"""
import pytest
from unittest.mock import patch


def test_robot_positions_save_requires_name(test_client):
    resp = test_client.post("/robot_positions/save", json={"params": {"x": 1}})
    assert resp.status_code == 422  # pydantic validation error


def test_robot_positions_save_requires_params_object(test_client):
    resp = test_client.post("/robot_positions/save", json={"name": "Pose1", "params": "not-a-dict"})
    assert resp.status_code == 422


def test_robot_positions_save_calls_db(test_client):
    # Patch DB writer to avoid touching real DB in test
    with patch("routes.robot_positions.save_robot_position") as mock_save:
        mock_save.return_value = True
        resp = test_client.post("/robot_positions/save", json={"name": "Pose1", "params": {"x": 1}})
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ok"
        assert "id" in data
        mock_save.assert_called_once()

