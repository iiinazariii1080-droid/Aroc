"""Extended tests for JsonPersistenceStore — covers sessions, delete, corrupt files, cleanup."""
import pytest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from services.persistence_store import JsonPersistenceStore, PersistedCommand, PersistedSession


@pytest.fixture
async def store(tmp_path):
    path = str(tmp_path / "test_state.json")
    return JsonPersistenceStore(path)


# ── Session CRUD ─────────────────────────────────────────────────────

class TestSessions:
    @pytest.mark.asyncio
    async def test_upsert_and_load_session(self, store):
        s = PersistedSession(
            command_id="c1",
            target_id="pos_A",
            start={"x": 0, "y": 0},
            goal={"x": 1, "y": 2},
            total_dist_m=3.0,
            min_remaining_dist_m=1.5,
            progress_percent=50,
            created_at="2024-01-01T00:00:00Z",
        )
        await store.upsert_session(s)
        loaded = await store.load_sessions()
        assert "c1" in loaded
        assert loaded["c1"].total_dist_m == 3.0
        assert loaded["c1"].start == {"x": 0, "y": 0}
        assert loaded["c1"].goal == {"x": 1, "y": 2}

    @pytest.mark.asyncio
    async def test_upsert_session_partial_update(self, store):
        s1 = PersistedSession(command_id="c1", target_id="A", total_dist_m=5.0)
        await store.upsert_session(s1)
        # Partial update — only update progress
        s2 = PersistedSession(command_id="c1", progress_percent=80)
        await store.upsert_session(s2)
        loaded = await store.load_sessions()
        assert loaded["c1"].target_id == "A"          # kept from first write
        assert loaded["c1"].total_dist_m == 5.0        # kept
        assert loaded["c1"].progress_percent == 80     # updated

    @pytest.mark.asyncio
    async def test_delete_session(self, store):
        s = PersistedSession(command_id="c1", target_id="A")
        await store.upsert_session(s)
        await store.delete_session("c1")
        loaded = await store.load_sessions()
        assert "c1" not in loaded

    @pytest.mark.asyncio
    async def test_delete_session_nonexistent(self, store):
        """Delete of nonexistent session should not error."""
        await store.delete_session("nonexistent")
        loaded = await store.load_sessions()
        assert len(loaded) == 0

    @pytest.mark.asyncio
    async def test_load_sessions_empty(self, store):
        loaded = await store.load_sessions()
        assert loaded == {}

    @pytest.mark.asyncio
    async def test_session_non_dict_start_goal_filtered(self, store):
        """Non-dict start/goal should be treated as None."""
        # Write raw JSON with non-dict start/goal
        data = {
            "commands": {},
            "sessions": {
                "c1": {
                    "target_id": "A",
                    "start": "not-a-dict",
                    "goal": [1, 2],
                    "total_dist_m": 1.0,
                }
            }
        }
        os.makedirs(os.path.dirname(store.path) or ".", exist_ok=True)
        with open(store.path, "w") as f:
            json.dump(data, f)
        loaded = await store.load_sessions()
        assert loaded["c1"].start is None
        assert loaded["c1"].goal is None
        assert loaded["c1"].total_dist_m == 1.0


# ── Delete command ───────────────────────────────────────────────────

class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_command(self, store):
        cmd = PersistedCommand(command_id="c1", transport_id="t1")
        await store.upsert(cmd)
        await store.delete("c1")
        loaded = await store.load()
        assert "c1" not in loaded

    @pytest.mark.asyncio
    async def test_delete_also_removes_session(self, store):
        cmd = PersistedCommand(command_id="c1", transport_id="t1")
        await store.upsert(cmd)
        s = PersistedSession(command_id="c1", target_id="A")
        await store.upsert_session(s)
        await store.delete("c1")
        loaded_cmds = await store.load()
        loaded_sess = await store.load_sessions()
        assert "c1" not in loaded_cmds
        assert "c1" not in loaded_sess

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self, store):
        await store.delete("nonexistent")


# ── Upsert partial update ───────────────────────────────────────────

class TestUpsertPartial:
    @pytest.mark.asyncio
    async def test_partial_update_keeps_previous(self, store):
        cmd1 = PersistedCommand(
            command_id="c1", transport_id="t1", target_id="A",
            last_state=1, created_at="2024-01-01"
        )
        await store.upsert(cmd1)
        # Only update last_state
        cmd2 = PersistedCommand(command_id="c1", transport_id="t1", last_state=8)
        await store.upsert(cmd2)
        loaded = await store.load()
        assert loaded["c1"].target_id == "A"       # kept from first write
        assert loaded["c1"].last_state == 8         # updated
        assert loaded["c1"].created_at == "2024-01-01"  # kept


# ── Corrupt file handling ────────────────────────────────────────────

class TestCorruptFile:
    @pytest.mark.asyncio
    async def test_corrupt_json_returns_empty(self, store):
        """Corrupt JSON is quarantined and empty state returned."""
        os.makedirs(os.path.dirname(store.path) or ".", exist_ok=True)
        with open(store.path, "w") as f:
            f.write("{invalid json!!!")
        loaded = await store.load()
        assert loaded == {}
        # Verify quarantine file was created
        quarantine = store.path + ".corrupt"
        assert os.path.exists(quarantine)

    @pytest.mark.asyncio
    async def test_non_dict_root_returns_empty(self, store):
        """JSON that is not a dict at root level."""
        os.makedirs(os.path.dirname(store.path) or ".", exist_ok=True)
        with open(store.path, "w") as f:
            json.dump([1, 2, 3], f)
        loaded = await store.load()
        # Should be empty since root is not a dict with "commands"
        assert loaded == {}

    @pytest.mark.asyncio
    async def test_load_ignores_non_dict_command(self, store):
        """Non-dict values inside 'commands' are skipped."""
        data = {
            "commands": {
                "good": {"transport_id": "t1", "target_id": "A"},
                "bad": "not-a-dict",
            },
            "sessions": {}
        }
        os.makedirs(os.path.dirname(store.path) or ".", exist_ok=True)
        with open(store.path, "w") as f:
            json.dump(data, f)
        loaded = await store.load()
        assert "good" in loaded
        assert "bad" not in loaded


# ── Orphan tmp cleanup ───────────────────────────────────────────────

class TestOrphanCleanup:
    def test_cleanup_orphan_tmp_files(self, tmp_path):
        state_path = str(tmp_path / "state.json")
        # Create orphan tmp files
        (tmp_path / ".state.json.abc123.tmp").write_text("orphan1")
        (tmp_path / ".state.json.def456.tmp").write_text("orphan2")
        (tmp_path / "unrelated.txt").write_text("keep")
        store = JsonPersistenceStore(state_path)
        # Orphan tmp files should be cleaned up
        assert not (tmp_path / ".state.json.abc123.tmp").exists()
        assert not (tmp_path / ".state.json.def456.tmp").exists()
        assert (tmp_path / "unrelated.txt").exists()
