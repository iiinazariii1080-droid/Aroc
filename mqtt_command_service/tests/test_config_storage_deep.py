"""Tests for config_storage.py — CRUD, history, batch, JSON, pruning.

Uses a temp SQLite DB per test for isolation.
"""
from __future__ import annotations

import pytest

from config_storage import ConfigStorage


@pytest.fixture
def storage(tmp_path):
    """Create a ConfigStorage with a temporary DB."""
    db_path = str(tmp_path / "test_config.db")
    return ConfigStorage(db_path=db_path)


class TestConfigStorageBasic:
    def test_get_default(self, storage):
        assert storage.get("missing_key") is None

    def test_get_with_default(self, storage):
        assert storage.get("missing_key", "fallback") == "fallback"

    def test_set_and_get(self, storage):
        assert storage.set("MQTT_BROKER", "broker.example.com", updated_by="test") is True
        assert storage.get("MQTT_BROKER") == "broker.example.com"

    def test_set_overwrites(self, storage):
        storage.set("KEY", "v1", updated_by="test")
        storage.set("KEY", "v2", updated_by="test")
        assert storage.get("KEY") == "v2"

    def test_get_all(self, storage):
        storage.set("A", "1", updated_by="test")
        storage.set("B", "2", updated_by="test")
        all_cfg = storage.get_all()
        assert all_cfg == {"A": "1", "B": "2"}


class TestConfigStorageHistory:
    def test_history_recorded(self, storage):
        storage.set("KEY", "val1", updated_by="user1", reason="initial")
        storage.set("KEY", "val2", updated_by="user2", reason="update")
        history = storage.get_history("KEY")
        assert len(history) == 2
        assert history[0]["value"] == "val2"
        assert history[0]["previous_value"] == "val1"

    def test_history_all_keys(self, storage):
        storage.set("A", "1", updated_by="test")
        storage.set("B", "2", updated_by="test")
        history = storage.get_history(limit=10)
        assert len(history) == 2

    def test_history_limit(self, storage):
        for i in range(5):
            storage.set("KEY", str(i), updated_by="test")
        history = storage.get_history("KEY", limit=3)
        assert len(history) == 3

    def test_get_latest_update_time(self, storage):
        storage.set("A", "1", updated_by="test")
        storage.set("B", "2", updated_by="test")
        latest = storage.get_latest_update_time(["A", "B"])
        assert latest is not None

    def test_get_latest_update_time_empty(self, storage):
        assert storage.get_latest_update_time([]) is None

    def test_get_latest_update_time_no_entries(self, storage):
        result = storage.get_latest_update_time(["NONEXISTENT"])
        assert result is None


class TestConfigStorageJSON:
    def test_set_and_get_json(self, storage):
        data = {"broker": "localhost", "port": 1883}
        assert storage.set_json("MQTT_CONFIG", data, updated_by="test") is True
        result = storage.get_json("MQTT_CONFIG")
        assert result == data

    def test_get_json_missing(self, storage):
        assert storage.get_json("MISSING") is None

    def test_get_json_default(self, storage):
        default = {"default": True}
        assert storage.get_json("MISSING", default=default) == default

    def test_get_json_invalid(self, storage):
        storage.set("BROKEN", "not{json", updated_by="test")
        result = storage.get_json("BROKEN", default={"fallback": True})
        assert result == {"fallback": True}

    def test_set_json_non_serializable(self, storage):
        result = storage.set_json("BAD", {"obj": object()}, updated_by="test")
        assert result is False


class TestConfigStorageBatch:
    def test_batch_update(self, storage):
        updates = {"A": "1", "B": "2", "C": "3"}
        assert storage.batch_update(updates, updated_by="test", reason="batch") is True
        assert storage.get("A") == "1"
        assert storage.get("B") == "2"
        assert storage.get("C") == "3"

    def test_batch_update_empty(self, storage):
        assert storage.batch_update({}, updated_by="test") is True

    def test_batch_update_preserves_history(self, storage):
        storage.set("A", "old", updated_by="test")
        storage.batch_update({"A": "new"}, updated_by="batch")
        history = storage.get_history("A")
        assert len(history) == 2
        assert history[0]["previous_value"] == "old"


class TestConfigStoragePruning:
    def test_pruning_runs(self, storage):
        storage._last_prune_ts = 0.0
        for i in range(5):
            storage.set("KEY", str(i), updated_by="test")
        history = storage.get_history("KEY")
        assert len(history) <= ConfigStorage._MAX_HISTORY_PER_KEY


class TestModuleLevelHelpers:
    def test_save_and_get_config_from_db(self, storage):
        import config_storage
        from config_storage import get_config_from_db, save_config_to_db
        orig = config_storage._storage
        try:
            config_storage._storage = storage
            assert save_config_to_db("TEST_KEY", "test_val") is True
            assert get_config_from_db("TEST_KEY") == "test_val"
        finally:
            config_storage._storage = orig

    def test_get_all_config_from_db(self, storage):
        import config_storage
        from config_storage import get_all_config_from_db
        orig = config_storage._storage
        try:
            config_storage._storage = storage
            storage.set("X", "1", updated_by="test")
            result = get_all_config_from_db()
            assert "X" in result
        finally:
            config_storage._storage = orig

    def test_get_config_history(self, storage):
        import config_storage
        from config_storage import get_config_history
        orig = config_storage._storage
        try:
            config_storage._storage = storage
            storage.set("Y", "1", updated_by="test")
            history = get_config_history("Y")
            assert len(history) == 1
        finally:
            config_storage._storage = orig
