"""Tests for db/robot_positions.py — CRUD operations with temp SQLite."""
import json
import os
import sqlite3
import tempfile
import pytest
from unittest.mock import patch

import db.robot_positions as mod


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    """Redirect DB_PATH to a temp file and reset the lazy-init flag."""
    db_file = str(tmp_path / "test_positions.db")
    with patch.object(mod, "DB_PATH", db_file), \
         patch.object(mod, "_table_initialized", False):
        yield db_file


# ── init / ensure_initialized ────────────────────────────────────────

class TestInit:
    def test_creates_table(self, _tmp_db):
        mod.init_robot_positions_table()
        conn = sqlite3.connect(_tmp_db)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='robot_positions'")
        assert cur.fetchone() is not None
        conn.close()

    def test_ensure_initialized_idempotent(self, _tmp_db):
        mod._table_initialized = False
        mod.ensure_initialized()
        assert mod._table_initialized is True
        # Second call is a no-op (flag already set).
        mod.ensure_initialized()
        assert mod._table_initialized is True

    def test_init_creates_directory(self, tmp_path):
        nested = str(tmp_path / "sub" / "dir" / "test.db")
        with patch.object(mod, "DB_PATH", nested), \
             patch.object(mod, "_table_initialized", False):
            mod.init_robot_positions_table()
            assert os.path.isdir(os.path.dirname(nested))

    def test_init_not_writable_dir_raises(self, tmp_path):
        """If directory is not writable, should raise RuntimeError."""
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(0o444)
        db_file = str(ro_dir / "test.db")
        try:
            with patch.object(mod, "DB_PATH", db_file), \
                 patch.object(mod, "_table_initialized", False):
                with pytest.raises(RuntimeError, match="not writable"):
                    mod.init_robot_positions_table()
        finally:
            ro_dir.chmod(0o755)

    def test_init_makedirs_oserror(self, tmp_path):
        """Cover the OSError branch when makedirs fails."""
        db_file = str(tmp_path / "ghost" / "test.db")
        with patch.object(mod, "DB_PATH", db_file), \
             patch.object(mod, "_table_initialized", False), \
             patch("os.makedirs", side_effect=OSError("boom")):
            with pytest.raises(RuntimeError, match="Failed to create"):
                mod.init_robot_positions_table()


# ── save_robot_position ──────────────────────────────────────────────

class TestSave:
    def test_insert_new(self):
        result = mod.save_robot_position({
            "id": "p1", "name": "Station A",
            "params": {"location": {"x_m": 1.0, "y_m": 2.0}},
        })
        assert result is True

    def test_upsert_existing(self):
        mod.save_robot_position({
            "id": "p1", "name": "Station A",
            "params": {"location": {"x_m": 1.0}},
        })
        mod.save_robot_position({
            "id": "p1", "name": "Updated",
            "params": {"location": {"x_m": 9.0}},
        })
        row = mod.get_robot_position("p1")
        assert row["name"] == "Updated"

    def test_not_dict_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            mod.save_robot_position("not a dict")

    def test_missing_id_raises(self):
        with pytest.raises(ValueError, match="'id'"):
            mod.save_robot_position({"name": "X", "params": {}})

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="'name'"):
            mod.save_robot_position({"id": "p1", "name": "  ", "params": {}})

    def test_none_name_raises(self):
        with pytest.raises(ValueError, match="'name'"):
            mod.save_robot_position({"id": "p1", "name": None, "params": {}})

    def test_missing_params_raises(self):
        with pytest.raises(ValueError, match="'params'"):
            mod.save_robot_position({"id": "p1", "name": "A", "params": "not_dict"})

    def test_duplicate_name_case_insensitive(self):
        mod.save_robot_position({"id": "p1", "name": "alpha", "params": {"x": 1}})
        with pytest.raises(ValueError, match="Duplicate name"):
            mod.save_robot_position({"id": "p2", "name": "Alpha", "params": {"x": 2}})


# ── get_robot_positions_list ─────────────────────────────────────────

class TestGetList:
    def test_empty_list(self):
        result = mod.get_robot_positions_list()
        assert result == []

    def test_returns_all(self):
        mod.save_robot_position({"id": "p1", "name": "A", "params": {"x": 1}})
        mod.save_robot_position({"id": "p2", "name": "B", "params": {"x": 2}})
        rows = mod.get_robot_positions_list()
        assert len(rows) == 2

    def test_corrupt_json_returns_none_params(self, _tmp_db):
        """If params json is corrupt, it should still return the row with params=None."""
        mod.ensure_initialized()
        conn = sqlite3.connect(_tmp_db)
        conn.execute(
            "INSERT INTO robot_positions (id, name, params) VALUES (?, ?, ?)",
            ("bad", "BadRow", "not-valid-json{{{"),
        )
        conn.commit()
        conn.close()
        rows = mod.get_robot_positions_list()
        assert len(rows) == 1
        assert rows[0]["params"] is None


# ── get_robot_position ───────────────────────────────────────────────

class TestGetById:
    def test_found(self):
        mod.save_robot_position({"id": "p1", "name": "X", "params": {"k": "v"}})
        row = mod.get_robot_position("p1")
        assert row is not None
        assert row["name"] == "X"

    def test_not_found(self):
        assert mod.get_robot_position("nope") is None

    def test_empty_id_returns_none(self):
        assert mod.get_robot_position("") is None

    def test_corrupt_params_json(self, _tmp_db):
        mod.ensure_initialized()
        conn = sqlite3.connect(_tmp_db)
        conn.execute(
            "INSERT INTO robot_positions (id, name, params) VALUES (?, ?, ?)",
            ("cp", "Corrupt", "{invalid"),
        )
        conn.commit()
        conn.close()
        row = mod.get_robot_position("cp")
        assert row["params"] is None


# ── get_robot_position_by_name ───────────────────────────────────────

class TestGetByName:
    def test_found_case_insensitive(self):
        mod.save_robot_position({"id": "p1", "name": "Station", "params": {"a": 1}})
        row = mod.get_robot_position_by_name("station")
        assert row is not None
        assert row["id"] == "p1"

    def test_not_found(self):
        assert mod.get_robot_position_by_name("ghost") is None

    def test_empty_name_returns_none(self):
        assert mod.get_robot_position_by_name("") is None

    def test_none_returns_none(self):
        assert mod.get_robot_position_by_name(None) is None

    def test_whitespace_only_returns_none(self):
        assert mod.get_robot_position_by_name("   ") is None

    def test_corrupt_params_json(self, _tmp_db):
        mod.ensure_initialized()
        conn = sqlite3.connect(_tmp_db)
        conn.execute(
            "INSERT INTO robot_positions (id, name, params) VALUES (?, ?, ?)",
            ("cp2", "CorruptName", "<<bad>>"),
        )
        conn.commit()
        conn.close()
        row = mod.get_robot_position_by_name("CorruptName")
        assert row["params"] is None


# ── delete_robot_position ────────────────────────────────────────────

class TestDelete:
    def test_delete_existing(self):
        mod.save_robot_position({"id": "p1", "name": "X", "params": {"a": 1}})
        assert mod.delete_robot_position("p1") is True
        assert mod.get_robot_position("p1") is None

    def test_delete_nonexistent(self):
        assert mod.delete_robot_position("nope") is False

    def test_empty_id_raises(self):
        with pytest.raises(ValueError, match="position_id is required"):
            mod.delete_robot_position("")
