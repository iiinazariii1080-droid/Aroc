"""Tests for app/state._recover_state — persistence recovery paths."""

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.state import _recover_state


def _make_cmd(transport_id="T1", target_id="S1"):
    return SimpleNamespace(transport_id=transport_id, target_id=target_id)


@pytest.mark.asyncio
async def test_recover_no_commands():
    """No persisted commands → early return, nothing crashes."""
    mock_client = AsyncMock()
    with patch("app.state.state_store") as ss:
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={})
        await _recover_state(mock_client)
    mock_client.transport_get.assert_not_called()


@pytest.mark.asyncio
async def test_recover_active_command():
    """Active transport (state in active range) → register_command."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 3, "id": "T1"})

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus") as eb,
        patch("domain.state_machine.NavigationStateMachine") as nsm,
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD1": _make_cmd("T1", "S1")}
        )
        ss.register_command = AsyncMock()
        nsm.is_terminal_state.return_value = False
        nsm.is_active_state.return_value = True

        await _recover_state(mock_client)

    ss.register_command.assert_awaited_once()
    call_kwargs = ss.register_command.call_args
    assert call_kwargs.kwargs.get("command_id") == "CMD1" or call_kwargs[1].get("command_id") == "CMD1"


@pytest.mark.asyncio
async def test_recover_terminal_finished():
    """Terminal state=8 (FINISHED) → publish success, clear transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 8, "id": "T1"})

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus") as eb,
        patch("domain.state_machine.NavigationStateMachine") as nsm,
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD2": _make_cmd("T2")}
        )
        ss.clear_transport = AsyncMock()
        eb.publish = AsyncMock()
        nsm.is_terminal_state.return_value = True

        await _recover_state(mock_client)

    ss.clear_transport.assert_awaited()
    eb.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_terminal_canceled():
    """Terminal state=6 (CANCELED) → publish canceled event."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 6, "id": "T1"})

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus") as eb,
        patch("domain.state_machine.NavigationStateMachine") as nsm,
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD3": _make_cmd("T3")}
        )
        ss.clear_transport = AsyncMock()
        eb.publish = AsyncMock()
        nsm.is_terminal_state.return_value = True

        await _recover_state(mock_client)

    eb.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_terminal_error_state():
    """Terminal error state → publish error event with reason."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 5, "id": "T1"})
    mock_client.status = AsyncMock(return_value={"state_flags": {"error": True}})

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus") as eb,
        patch("app.state.ErrorMapper") as em,
        patch("domain.state_machine.NavigationStateMachine") as nsm,
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD4": _make_cmd("T4")}
        )
        ss.clear_transport = AsyncMock()
        eb.publish = AsyncMock()
        em.get_error_reason.return_value = "obstacle_detected"
        nsm.is_terminal_state.return_value = True

        await _recover_state(mock_client)

    em.get_error_reason.assert_called_once()
    eb.publish.assert_awaited()


@pytest.mark.asyncio
async def test_recover_not_found():
    """transport_get returns non-dict → clear_transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value=None)

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus"),
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD5": _make_cmd("T5")}
        )
        ss.clear_transport = AsyncMock()

        await _recover_state(mock_client)

    ss.clear_transport.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_timeout():
    """transport_get times out → clear_transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(side_effect=asyncio.TimeoutError)

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus"),
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD6": _make_cmd("T6")}
        )
        ss.clear_transport = AsyncMock()

        await _recover_state(mock_client)

    ss.clear_transport.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_inactive_state():
    """Non-active, non-terminal state → clear_transport."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(return_value={"state": 0})

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus"),
        patch("domain.state_machine.NavigationStateMachine") as nsm,
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD7": _make_cmd("T7")}
        )
        ss.clear_transport = AsyncMock()
        nsm.is_terminal_state.return_value = False
        nsm.is_active_state.return_value = False

        await _recover_state(mock_client)

    ss.clear_transport.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_http_404():
    """HTTP 404 for transport → clear_transport, treated as not_found."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(
        side_effect=RuntimeError("HTTP 404 on /transport/T8")
    )

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus"),
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD8": _make_cmd("T8")}
        )
        ss.clear_transport = AsyncMock()

        await _recover_state(mock_client)

    ss.clear_transport.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_generic_error():
    """Generic exception → counted as error."""
    mock_client = AsyncMock()
    mock_client.transport_get = AsyncMock(side_effect=ValueError("unexpected"))

    with (
        patch("app.state.state_store") as ss,
        patch("app.state.event_bus"),
    ):
        ss.get_persisted_commands_for_recovery = AsyncMock(
            return_value={"CMD9": _make_cmd("T9")}
        )
        ss.clear_transport = AsyncMock()

        await _recover_state(mock_client)

    # clear_transport NOT called for generic errors (not 404)
    ss.clear_transport.assert_not_awaited()
