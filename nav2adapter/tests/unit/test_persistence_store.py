"""
Unit tests for JsonPersistenceStore.
"""
import pytest
import json
from services.persistence_store import JsonPersistenceStore, PersistedCommand


@pytest.mark.asyncio
async def test_save_and_load(persistence_store):
    """Test saving and loading persisted commands."""
    store = persistence_store
    
    # Create test data
    commands = {
        "cmd_1": PersistedCommand(
            command_id="cmd_1",
            transport_id="transport_1",
            target_id="position_A",
            last_state=1,
            last_result=None,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
        "cmd_2": PersistedCommand(
            command_id="cmd_2",
            transport_id="transport_2",
            target_id="position_B",
            last_state=8,
            last_result=None,
            created_at="2024-01-01T00:01:00Z",
            updated_at="2024-01-01T00:01:00Z",
        ),
    }
    
    # Save
    for cmd in commands.values():
        await store.upsert(cmd)
    
    # Load
    loaded = await store.load()
    
    assert len(loaded) == 2
    assert "cmd_1" in loaded
    assert "cmd_2" in loaded
    assert loaded["cmd_1"].transport_id == "transport_1"
    assert loaded["cmd_2"].transport_id == "transport_2"


@pytest.mark.asyncio
async def test_upsert_updates_existing(persistence_store):
    """Test that upsert updates existing commands."""
    store = persistence_store
    
    # Create initial command
    cmd1 = PersistedCommand(
        command_id="cmd_1",
        transport_id="transport_1",
        target_id="position_A",
        last_state=1,
        last_result=None,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    await store.upsert(cmd1)
    
    # Update command
    cmd2 = PersistedCommand(
        command_id="cmd_1",
        transport_id="transport_1",
        target_id="position_A",
        last_state=8,  # Updated state
        last_result=None,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:01:00Z",  # Updated timestamp
    )
    await store.upsert(cmd2)
    
    # Load and verify
    loaded = await store.load()
    assert len(loaded) == 1
    assert loaded["cmd_1"].last_state == 8


@pytest.mark.asyncio
async def test_load_empty_file(persistence_store):
    """Test loading from non-existent file returns empty dict."""
    store = persistence_store
    
    loaded = await store.load()
    assert isinstance(loaded, dict)
    assert len(loaded) == 0


@pytest.mark.asyncio
async def test_atomic_write(persistence_store):
    """Test that writes are atomic (no partial writes)."""
    store = persistence_store
    
    # Create multiple commands
    commands = [
        PersistedCommand(
            command_id=f"cmd_{i}",
            transport_id=f"transport_{i}",
            target_id=f"position_{i}",
            last_state=1,
            last_result=None,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        for i in range(10)
    ]
    
    # Save all concurrently
    import asyncio
    await asyncio.gather(*[store.upsert(cmd) for cmd in commands])
    
    # Load and verify all are present
    loaded = await store.load()
    assert len(loaded) == 10
    for i in range(10):
        assert f"cmd_{i}" in loaded
