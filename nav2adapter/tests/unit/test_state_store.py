"""
Unit tests for StateStore.
"""
import pytest
from datetime import datetime
from domain.models import ActiveTransport, NavigationStatus, PositionStatus, NavigationStatusEnum


@pytest.mark.asyncio
async def test_register_command(state_store, sample_command_id, sample_transport_id):
    """Test registering a command."""
    transport = await state_store.register_command(
        command_id=sample_command_id,
        transport_id=sample_transport_id,
        state=1,
        target_id="position_A",
    )
    
    assert transport is not None
    assert transport.command_id == sample_command_id
    assert transport.transport_id == sample_transport_id
    assert transport.state == 1
    assert transport.target_id == "position_A"
    
    # Verify it's in registry
    active = await state_store.get_all_active_commands()
    assert sample_command_id in active
    assert active[sample_command_id].transport_id == sample_transport_id


@pytest.mark.asyncio
async def test_register_command_idempotent(state_store, sample_command_id, sample_transport_id):
    """Test that registering the same command twice is idempotent."""
    transport1 = await state_store.register_command(
        command_id=sample_command_id,
        transport_id=sample_transport_id,
        state=1,
        target_id="position_A",
    )
    
    # Register again with same command_id
    transport2 = await state_store.register_command(
        command_id=sample_command_id,
        transport_id=sample_transport_id,
        state=1,
        target_id="position_A",
    )
    
    # Should return the same transport
    assert transport1.transport_id == transport2.transport_id
    assert transport1.command_id == transport2.command_id


@pytest.mark.asyncio
async def test_update_transport_state(state_store, sample_command_id, sample_transport_id):
    """Test updating transport state."""
    await state_store.register_command(
        command_id=sample_command_id,
        transport_id=sample_transport_id,
        state=1,
        target_id="position_A",
    )
    
    await state_store.update_transport_state(sample_command_id, 8)
    
    active = await state_store.get_all_active_commands()
    assert active[sample_command_id].state == 8


@pytest.mark.asyncio
async def test_clear_transport(state_store, sample_command_id, sample_transport_id):
    """Test clearing a transport."""
    await state_store.register_command(
        command_id=sample_command_id,
        transport_id=sample_transport_id,
        state=1,
        target_id="position_A",
    )
    
    await state_store.clear_transport(sample_command_id)
    
    active = await state_store.get_all_active_commands()
    assert sample_command_id not in active


@pytest.mark.asyncio
async def test_get_active_transport(state_store, sample_command_id, sample_transport_id):
    """Test getting active transport by command ID."""
    await state_store.register_command(
        command_id=sample_command_id,
        transport_id=sample_transport_id,
        state=1,
        target_id="position_A",
    )
    
    transport = await state_store.get_active_transport(sample_command_id)
    assert transport is not None
    assert transport.transport_id == sample_transport_id


@pytest.mark.asyncio
async def test_get_active_transport_not_found(state_store):
    """Test getting transport for non-existent command ID."""
    transport = await state_store.get_active_transport("non-existent")
    assert transport is None


@pytest.mark.asyncio
async def test_set_get_navigation_status(state_store, sample_navigation_status):
    """Test setting and getting navigation status."""
    await state_store.set_last_navigation_status(sample_navigation_status)
    
    status = await state_store.get_last_navigation_status()
    assert status is not None
    assert status.status == NavigationStatusEnum.IDLE
    assert status.goal_id is None


@pytest.mark.asyncio
async def test_set_get_position_status(state_store, sample_position_status):
    """Test setting and getting position status."""
    await state_store.set_last_position_status(sample_position_status)
    
    position = await state_store.get_last_position_status()
    assert position is not None
    assert position.x == 1.0
    assert position.y == 2.0
    assert position.theta == 0.5
    assert position.frame_id == "map"


@pytest.mark.asyncio
async def test_get_all_active_commands(state_store):
    """Test getting all active commands."""
    # Register multiple commands
    for i in range(3):
        await state_store.register_command(
            command_id=f"cmd_{i}",
            transport_id=f"transport_{i}",
            state=1,
            target_id=f"position_{i}",
        )
    
    active = await state_store.get_all_active_commands()
    assert len(active) == 3
    assert "cmd_0" in active
    assert "cmd_1" in active
    assert "cmd_2" in active


@pytest.mark.asyncio
async def test_clear_all_commands(state_store):
    """Clearing all commands removes registry and resets current command pointer."""
    await state_store.register_command(command_id="cmd_1", transport_id="t_1", state=1, target_id="pos_1")
    await state_store.register_command(command_id="cmd_2", transport_id="t_2", state=1, target_id="pos_2")
    assert len(await state_store.get_all_active_commands()) == 2

    cleared = await state_store.clear_all_commands()
    assert cleared == 2
    assert len(await state_store.get_all_active_commands()) == 0
    assert await state_store.get_current_command_id() is None
