"""Unit tests for RecoveryService."""

import asyncio
import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from services.recovery_service import RecoveryService, RecoverySummary
from domain.state_machine import NavigationStateMachine


@dataclass
class _FakeCmd:
    """Minimal stand-in for PersistedCommand."""
    transport_id: str
    target_id: str = "pos_A"


@pytest.fixture
def mock_state_store():
    store = MagicMock()
    store.get_persisted_commands_for_recovery = AsyncMock(return_value={})
    store.clear_transport = AsyncMock()
    store.register_command = AsyncMock()
    store.get_current_command_id = AsyncMock(return_value=None)
    return store


@pytest.fixture
def mock_symovo():
    client = MagicMock()
    client.transport_get = AsyncMock(return_value={"state": NavigationStateMachine.RUNNING})
    return client


@pytest.fixture
def recovery_service(mock_symovo, mock_state_store):
    return RecoveryService(mock_symovo, mock_state_store)


class TestNoPersistedCommands:
    @pytest.mark.asyncio
    async def test_empty_persisted_returns_zero(self, recovery_service):
        summary = await recovery_service.recover()
        assert summary.recovered == 0
        assert summary.cleaned == 0
        assert summary.errors == 0


class TestActiveTransportRecovered:
    @pytest.mark.asyncio
    async def test_active_transport_is_recovered(self, recovery_service, mock_state_store, mock_symovo):
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }
        mock_symovo.transport_get.return_value = {"state": NavigationStateMachine.RUNNING}

        summary = await recovery_service.recover()
        assert summary.recovered == 1
        mock_state_store.register_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_starting_state_is_recovered(self, recovery_service, mock_state_store, mock_symovo):
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }
        mock_symovo.transport_get.return_value = {"state": NavigationStateMachine.STARTING}

        summary = await recovery_service.recover()
        assert summary.recovered == 1


class TestTerminalStateCleaned:
    @pytest.mark.asyncio
    async def test_finished_transport_cleaned(self, recovery_service, mock_state_store, mock_symovo):
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }
        mock_symovo.transport_get.return_value = {"state": NavigationStateMachine.FINISHED}

        summary = await recovery_service.recover()
        assert summary.terminal == 1
        assert summary.cleaned == 1
        assert summary.recovered == 0
        mock_state_store.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_error_transport_cleaned(self, recovery_service, mock_state_store, mock_symovo):
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }
        mock_symovo.transport_get.return_value = {"state": NavigationStateMachine.ERROR}

        summary = await recovery_service.recover()
        assert summary.terminal == 1
        assert summary.cleaned == 1


class TestTransportNotFound:
    @pytest.mark.asyncio
    async def test_transport_not_found_404_cleaned(self, recovery_service, mock_state_store, mock_symovo):
        exc = Exception("not found")
        exc.http_status = 404
        mock_symovo.transport_get.side_effect = exc
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }

        summary = await recovery_service.recover()
        assert summary.not_found == 1
        assert summary.cleaned == 1
        mock_state_store.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_transport_get_returns_non_dict_cleaned(self, recovery_service, mock_state_store, mock_symovo):
        mock_symovo.transport_get.return_value = None
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }

        summary = await recovery_service.recover()
        assert summary.not_found == 1
        assert summary.cleaned == 1


class TestControllerTimeout:
    @pytest.mark.asyncio
    async def test_timeout_cleaned(self, recovery_service, mock_state_store, mock_symovo):
        mock_symovo.transport_get.side_effect = asyncio.TimeoutError()
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }

        summary = await recovery_service.recover()
        assert summary.not_found == 1
        assert summary.cleaned == 1
        mock_state_store.clear_transport.assert_awaited()


class TestPreRunState:
    @pytest.mark.asyncio
    async def test_pre_run_transport_cleaned(self, recovery_service, mock_state_store, mock_symovo):
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }
        mock_symovo.transport_get.return_value = {"state": NavigationStateMachine.UNASSIGNED}

        summary = await recovery_service.recover()
        assert summary.pre_run == 1
        assert summary.cleaned == 1
        assert summary.recovered == 0


class TestMultipleCommands:
    @pytest.mark.asyncio
    async def test_mixed_results(self, recovery_service, mock_state_store, mock_symovo):
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-active": _FakeCmd(transport_id="t-1"),
            "cmd-done": _FakeCmd(transport_id="t-2"),
        }

        async def transport_get_side_effect(tid):
            if tid == "t-1":
                return {"state": NavigationStateMachine.RUNNING}
            return {"state": NavigationStateMachine.FINISHED}

        mock_symovo.transport_get.side_effect = transport_get_side_effect

        summary = await recovery_service.recover()
        assert summary.recovered == 1
        assert summary.terminal == 1


class TestUnexpectedError:
    @pytest.mark.asyncio
    async def test_generic_error_counted(self, recovery_service, mock_state_store, mock_symovo):
        exc = RuntimeError("connection reset")
        mock_symovo.transport_get.side_effect = exc
        mock_state_store.get_persisted_commands_for_recovery.return_value = {
            "cmd-1": _FakeCmd(transport_id="t-1"),
        }

        summary = await recovery_service.recover()
        assert summary.errors == 1
