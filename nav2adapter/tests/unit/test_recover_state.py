"""Tests for services/recovery_service.py — persistence recovery paths."""

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.recovery_service import RecoveryService


def _make_cmd(transport_id="T1", target_id="S1"):
    return SimpleNamespace(transport_id=transport_id, target_id=target_id)


@pytest.mark.asyncio
async def test_recover_no_commands():
    """No persisted commands -> early return, nothing crashes."""
    mock_client = AsyncMock()
    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(return_value={})
    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()
    mock_client.transport_get.assert_not_called()
    assert result.recovered == 0


@pytest.mark.asyncio
async def test_recover_active_command():
    """Active transport (state in active range) -> register_command."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 5, "id": "T1"})  # RUNNING

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD1": _make_cmd("T1", "S1")}
    )
    ss.register_command = AsyncMock()
    ss.get_current_command_id = AsyncMock(return_value=None)
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    ss.register_command.assert_awaited_once()
    assert result.recovered == 1


@pytest.mark.asyncio
async def test_recover_terminal_finished():
    """Terminal state=8 (FINISHED) -> clear transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 8, "id": "T1"})

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD2": _make_cmd("T2")}
    )
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    ss.clear_transport.assert_awaited()
    assert result.terminal == 1


@pytest.mark.asyncio
async def test_recover_terminal_canceled():
    """Terminal state=6 (CANCELED) -> clear transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 6, "id": "T1"})

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD3": _make_cmd("T3")}
    )
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    ss.clear_transport.assert_awaited()
    assert result.terminal == 1


@pytest.mark.asyncio
async def test_recover_not_found():
    """transport_get returns non-dict -> clear_transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value=None)

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD5": _make_cmd("T5")}
    )
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    ss.clear_transport.assert_awaited_once()
    assert result.not_found == 1


@pytest.mark.asyncio
async def test_recover_timeout():
    """transport_get times out -> clear_transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(side_effect=asyncio.TimeoutError)

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD6": _make_cmd("T6")}
    )
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    ss.clear_transport.assert_awaited_once()
    assert result.not_found == 1


@pytest.mark.asyncio
async def test_recover_inactive_state():
    """Non-active, non-terminal state -> clear_transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 0})

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD7": _make_cmd("T7")}
    )
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    ss.clear_transport.assert_awaited_once()
    assert result.inactive == 1


@pytest.mark.asyncio
async def test_recover_http_404():
    """HTTP 404 for transport -> clear_transport, treated as not_found."""
    mock_client = AsyncMock()
    err = Exception("not found")
    err.http_status = 404
    mock_client.transport_get = AsyncMock(side_effect=err)

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD8": _make_cmd("T8")}
    )
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    ss.clear_transport.assert_awaited_once()
    assert result.not_found == 1


@pytest.mark.asyncio
async def test_recover_generic_error():
    """Generic exception -> counted as error."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(side_effect=ValueError("unexpected"))

    ss = AsyncMock()
    ss.get_persisted_commands_for_recovery = AsyncMock(
        return_value={"CMD9": _make_cmd("T9")}
    )
    ss.clear_transport = AsyncMock()

    recovery = RecoveryService(mock_client, ss)
    result = await recovery.recover()

    assert result.errors == 1
    # clear_transport NOT called for generic errors (not 404)
    ss.clear_transport.assert_not_awaited()
