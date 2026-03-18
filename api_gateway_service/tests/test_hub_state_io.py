"""Real file I/O tests for HubStateStore — not mocked.

Uses local HubStateStore instances with tmp_path, independent of the
global singleton (which is mocked in conftest).
"""

import json

import pytest

from app.core.hub_state import HubStateStore


class TestWriteReadRoundtrip:
    @pytest.mark.asyncio
    async def test_set_and_reload(self, tmp_path):
        path = tmp_path / "hub_state.json"
        s1 = HubStateStore(path=path)
        await s1.load()
        await s1.set_hub_config({"base_url": "http://example.com"})

        s2 = HubStateStore(path=path)
        await s2.load()
        cfg = await s2.get_hub_config()
        assert cfg["base_url"] == "http://example.com"
        assert "updated_at" in cfg

    @pytest.mark.asyncio
    async def test_file_is_valid_json(self, tmp_path):
        path = tmp_path / "hub_state.json"
        store = HubStateStore(path=path)
        await store.load()
        await store.set_hub_config({"base_url": "http://test.local"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["hub_config"]["base_url"] == "http://test.local"


class TestCorruptFileRecovery:
    @pytest.mark.asyncio
    async def test_corrupt_json_falls_back_to_defaults(self, tmp_path):
        path = tmp_path / "hub_state.json"
        path.write_text("{invalid json!!!", encoding="utf-8")

        store = HubStateStore(path=path)
        await store.load()
        # Should not raise — falls back to defaults
        cfg = await store.get_hub_config()
        # Default is None unless DEFAULT_HUB_BASE_URL is set
        assert cfg is None or isinstance(cfg, dict)

    @pytest.mark.asyncio
    async def test_missing_file_uses_defaults(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        store = HubStateStore(path=path)
        await store.load()
        assert await store.get_hub_config() is None or isinstance(await store.get_hub_config(), dict)


class TestBackupCreation:
    @pytest.mark.asyncio
    async def test_backup_file_created_on_second_write(self, tmp_path):
        path = tmp_path / "hub_state.json"
        store = HubStateStore(path=path)
        await store.load()

        await store.set_hub_config({"base_url": "http://first.com"})
        await store.set_hub_config({"base_url": "http://second.com"})

        bak = path.with_suffix(".bak")
        assert bak.exists(), ".bak file should be created before second write"
        bak_data = json.loads(bak.read_text(encoding="utf-8"))
        assert bak_data["hub_config"]["base_url"] == "http://first.com"


class TestPersistenceAcrossRestart:
    @pytest.mark.asyncio
    async def test_identity_survives_restart(self, tmp_path):
        path = tmp_path / "hub_state.json"
        s1 = HubStateStore(path=path)
        await s1.load()
        await s1.set_robot_identity({"robot_id": "r1", "display_name": "Robot"})

        s2 = HubStateStore(path=path)
        await s2.load()
        identity = await s2.get_robot_identity()
        assert identity["robot_id"] == "r1"
        assert identity["display_name"] == "Robot"

    @pytest.mark.asyncio
    async def test_credentials_meta_survives_restart(self, tmp_path):
        path = tmp_path / "hub_state.json"
        s1 = HubStateStore(path=path)
        await s1.load()
        await s1.set_robot_credentials_meta({"robot_id": "bot-x"})

        s2 = HubStateStore(path=path)
        await s2.load()
        meta = await s2.get_robot_credentials_meta()
        assert meta["robot_id"] == "bot-x"


class TestNoTmpLeftover:
    @pytest.mark.asyncio
    async def test_tmp_file_cleaned_up(self, tmp_path):
        path = tmp_path / "hub_state.json"
        store = HubStateStore(path=path)
        await store.load()
        await store.set_hub_config({"base_url": "http://clean.test"})
        assert not path.with_suffix(".tmp").exists()
