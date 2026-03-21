"""Tests for JsonPersistenceStore resilience against corrupt files.

Covers:
- Empty file returns empty dict
- Invalid JSON does not crash (returns empty, quarantines file)
- Valid JSON with wrong schema is handled gracefully
- Write + read roundtrip preserves data
"""

import json
import os
from pathlib import Path

import pytest

from services.persistence_store import (
    JsonPersistenceStore,
    PersistedCommand,
    PersistedSession,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


@pytest.fixture
def store(store_path: Path) -> JsonPersistenceStore:
    return JsonPersistenceStore(str(store_path))


# ---------------------------------------------------------------------------
# Empty / missing file
# ---------------------------------------------------------------------------

class TestEmptyFile:

    @pytest.mark.asyncio
    async def test_load_missing_file_returns_empty(self, store: JsonPersistenceStore):
        """When the persistence file does not exist, load() returns empty dict."""
        result = await store.load()
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_sessions_missing_file_returns_empty(self, store: JsonPersistenceStore):
        result = await store.load_sessions()
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_empty_file_returns_empty(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """An empty (0-byte) file must not crash — treated as corrupt."""
        store_path.write_text("")

        result = await store.load()
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_sessions_empty_file_returns_empty(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        store_path.write_text("")
        result = await store.load_sessions()
        assert result == {}


# ---------------------------------------------------------------------------
# Invalid JSON
# ---------------------------------------------------------------------------

class TestInvalidJson:

    @pytest.mark.asyncio
    async def test_load_invalid_json_returns_empty(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """Garbage content must not crash — returns empty and quarantines."""
        store_path.write_text("{not valid json!!!")

        result = await store.load()
        assert result == {}

    @pytest.mark.asyncio
    async def test_invalid_json_quarantines_file(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """After reading corrupt JSON the original file should be quarantined."""
        store_path.write_text("<<<garbage>>>")

        await store.load()

        corrupt_path = store_path.parent / f"{store_path.name}.corrupt"
        # The original file should have been moved aside
        assert not store_path.exists() or corrupt_path.exists()

    @pytest.mark.asyncio
    async def test_load_after_corruption_allows_fresh_writes(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """After quarantining a corrupt file, new upserts must work normally."""
        store_path.write_text("NOT-JSON")
        await store.load()  # triggers quarantine

        cmd = PersistedCommand(command_id="c1", transport_id="t1")
        await store.upsert(cmd)

        loaded = await store.load()
        assert "c1" in loaded
        assert loaded["c1"].transport_id == "t1"


# ---------------------------------------------------------------------------
# Valid JSON but wrong schema
# ---------------------------------------------------------------------------

class TestWrongSchema:

    @pytest.mark.asyncio
    async def test_flat_json_no_commands_key(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """A valid JSON object lacking 'commands' key returns empty."""
        store_path.write_text(json.dumps({"foo": "bar"}))
        result = await store.load()
        assert result == {}

    @pytest.mark.asyncio
    async def test_commands_is_list_instead_of_dict(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """If 'commands' is a list instead of dict, load returns empty."""
        store_path.write_text(json.dumps({"commands": [1, 2, 3]}))
        result = await store.load()
        assert result == {}

    @pytest.mark.asyncio
    async def test_command_entry_is_non_dict_skipped(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """Non-dict entries inside commands are silently skipped."""
        store_path.write_text(json.dumps({
            "commands": {"good": {"transport_id": "t1"}, "bad": "string-value"},
            "sessions": {},
        }))
        result = await store.load()
        assert "good" in result
        assert "bad" not in result

    @pytest.mark.asyncio
    async def test_json_is_array_not_object(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """A top-level JSON array is not a valid schema — returns empty."""
        store_path.write_text(json.dumps([1, 2, 3]))
        result = await store.load()
        assert result == {}

    @pytest.mark.asyncio
    async def test_sessions_wrong_schema(
        self, store: JsonPersistenceStore, store_path: Path
    ):
        """Sessions key holding a non-dict value returns empty sessions."""
        store_path.write_text(json.dumps({"commands": {}, "sessions": "nope"}))
        result = await store.load_sessions()
        assert result == {}


# ---------------------------------------------------------------------------
# Write + read roundtrip
# ---------------------------------------------------------------------------

class TestRoundtrip:

    @pytest.mark.asyncio
    async def test_upsert_then_load_roundtrip(self, store: JsonPersistenceStore):
        cmd = PersistedCommand(
            command_id="cmd-42",
            transport_id="tr-99",
            target_id="pos-A",
            last_state=3,
            last_result={"code": 0},
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:01:00Z",
        )
        await store.upsert(cmd)

        loaded = await store.load()
        assert "cmd-42" in loaded
        c = loaded["cmd-42"]
        assert c.transport_id == "tr-99"
        assert c.target_id == "pos-A"
        assert c.last_state == 3
        assert c.last_result == {"code": 0}

    @pytest.mark.asyncio
    async def test_upsert_session_then_load_roundtrip(self, store: JsonPersistenceStore):
        session = PersistedSession(
            command_id="cmd-7",
            target_id="goal-B",
            start={"x": 0.0, "y": 0.0},
            goal={"x": 5.0, "y": 10.0},
            total_dist_m=11.18,
            progress_percent=50,
        )
        await store.upsert_session(session)

        loaded = await store.load_sessions()
        assert "cmd-7" in loaded
        s = loaded["cmd-7"]
        assert s.target_id == "goal-B"
        assert s.start == {"x": 0.0, "y": 0.0}
        assert s.goal == {"x": 5.0, "y": 10.0}
        assert s.total_dist_m == 11.18
        assert s.progress_percent == 50

    @pytest.mark.asyncio
    async def test_delete_removes_command_and_session(self, store: JsonPersistenceStore):
        cmd = PersistedCommand(command_id="del-me", transport_id="t1")
        session = PersistedSession(command_id="del-me", target_id="x")
        await store.upsert(cmd)
        await store.upsert_session(session)

        await store.delete("del-me")

        assert "del-me" not in await store.load()
        # Session should also be removed by delete()
        assert "del-me" not in await store.load_sessions()

    @pytest.mark.asyncio
    async def test_multiple_upserts_merge_fields(self, store: JsonPersistenceStore):
        """Upserting with partial fields merges with existing entry."""
        await store.upsert(PersistedCommand(
            command_id="m1", transport_id="t1", target_id="pos-A",
        ))
        await store.upsert(PersistedCommand(
            command_id="m1", transport_id="t1", last_state=5,
        ))

        loaded = await store.load()
        c = loaded["m1"]
        assert c.target_id == "pos-A"  # preserved from first upsert
        assert c.last_state == 5       # set by second upsert
