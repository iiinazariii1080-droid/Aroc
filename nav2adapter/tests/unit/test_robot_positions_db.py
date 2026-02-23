"""
Unit tests for db.robot_positions validation rules.
"""
import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_save_requires_name(tmp_path: Path, monkeypatch):
    import db.robot_positions as rp

    monkeypatch.setattr(rp, "DB_PATH", str(tmp_path / "test.db"))
    rp.init_robot_positions_table()

    with pytest.raises(ValueError) as e:
        rp.save_robot_position({"id": "a1", "name": "", "params": {"x": 1}})
    assert "name" in str(e.value).lower()


@pytest.mark.asyncio
async def test_save_enforces_unique_name_case_insensitive(tmp_path: Path, monkeypatch):
    import db.robot_positions as rp

    monkeypatch.setattr(rp, "DB_PATH", str(tmp_path / "test.db"))
    rp.init_robot_positions_table()

    assert rp.save_robot_position({"id": "a1", "name": "Shampoo", "params": {"x": 1}}) is True

    with pytest.raises(ValueError) as e:
        rp.save_robot_position({"id": "a2", "name": "shampoo", "params": {"x": 2}})
    assert "duplicate" in str(e.value).lower()


@pytest.mark.asyncio
async def test_get_by_name(tmp_path: Path, monkeypatch):
    import db.robot_positions as rp

    monkeypatch.setattr(rp, "DB_PATH", str(tmp_path / "test.db"))
    rp.init_robot_positions_table()

    rp.save_robot_position({"id": "a1", "name": "TABLE", "params": {"location": {"x_m": 1}}})
    got = rp.get_robot_position_by_name("table")
    assert got is not None
    assert got["id"] == "a1"
    assert got["name"] == "TABLE"
    assert isinstance(got["params"], dict)

