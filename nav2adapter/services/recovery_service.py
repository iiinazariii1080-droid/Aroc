"""State recovery after restart -- syncs with the Symovo controller.

Only restores commands that are actually active on the controller.
Terminal, pre-run, and not-found commands are cleaned up without
republishing events (at-most-once semantics).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from domain.state_machine import NavigationStateMachine
from services.state_store import StateStore
from services.symovo_service import SymovoAgvClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class RecoverySummary:
    """Result of a recovery run."""
    recovered: int = 0
    cleaned: int = 0
    not_found: int = 0
    terminal: int = 0
    pre_run: int = 0
    inactive: int = 0
    errors: int = 0


class RecoveryService:
    """Recover persisted commands by verifying their state on the controller."""

    def __init__(self, symovo_client: SymovoAgvClient, state_store: StateStore) -> None:
        self._client = symovo_client
        self._store = state_store

    async def recover(self) -> RecoverySummary:
        persisted_commands = await self._store.get_persisted_commands_for_recovery()
        if not persisted_commands:
            _LOGGER.info("No persisted commands to recover")
            return RecoverySummary()

        command_count = len(persisted_commands)
        _LOGGER.info("Checking %s persisted commands for recovery ...", command_count)

        semaphore = asyncio.Semaphore(5)
        summary = RecoverySummary()

        async def check_command(command_id: str, cmd) -> tuple[str, str, dict]:
            async with semaphore:
                try:
                    transport = await asyncio.wait_for(
                        self._client.transport_get(cmd.transport_id), timeout=3.0,
                    )
                    if not isinstance(transport, dict):
                        await self._store.clear_transport(command_id)
                        return (command_id, "not_found", {})

                    state = transport.get("state", 0)

                    if NavigationStateMachine.is_terminal_state(state):
                        await self._store.clear_transport(command_id)
                        return (command_id, "terminal", {"state": state})

                    if NavigationStateMachine.is_pre_run_state(state):
                        _LOGGER.info(
                            "Clearing pre-run command %s (state=%s): controller will resolve it",
                            command_id, state,
                        )
                        await self._store.clear_transport(command_id)
                        return (command_id, "pre_run", {"state": state})

                    if NavigationStateMachine.is_active_state(state):
                        current = await self._store.get_current_command_id()
                        if current is not None and current != command_id:
                            _LOGGER.info(
                                "Skipping recovery of %s: a new command (%s) is already active",
                                command_id, current,
                            )
                            await self._store.clear_transport(command_id)
                            return (command_id, "skipped_new_active", {"state": state})

                        await self._store.register_command(
                            command_id=command_id,
                            transport_id=cmd.transport_id,
                            state=state,
                            target_id=cmd.target_id,
                        )
                        return (command_id, "recovered", {"state": state})

                    await self._store.clear_transport(command_id)
                    return (command_id, "inactive", {"state": state})

                except asyncio.TimeoutError:
                    await self._store.clear_transport(command_id)
                    return (command_id, "timeout", {})
                except Exception as e:
                    if getattr(e, "http_status", None) == 404:
                        await self._store.clear_transport(command_id)
                        return (command_id, "not_found", {})
                    return (command_id, "error", {"error": str(e)})

        results = await asyncio.gather(
            *[check_command(cmd_id, cmd) for cmd_id, cmd in persisted_commands.items()],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                summary.errors += 1
                continue

            command_id, result_type, details = result
            if result_type == "recovered":
                summary.recovered += 1
                _LOGGER.info("Recovered active command %s (state=%s)", command_id, details.get("state"))
            elif result_type == "terminal":
                summary.cleaned += 1
                summary.terminal += 1
            elif result_type == "pre_run":
                summary.cleaned += 1
                summary.pre_run += 1
            elif result_type == "inactive":
                summary.cleaned += 1
                summary.inactive += 1
            elif result_type in ("not_found", "timeout"):
                summary.cleaned += 1
                summary.not_found += 1
            elif result_type == "error":
                summary.errors += 1
                _LOGGER.debug("Recovery check error for command %s: %s", command_id, details.get("error"))

        self._log_summary(summary)
        return summary

    @staticmethod
    def _log_summary(s: RecoverySummary) -> None:
        parts: list[str] = []
        if s.recovered:
            parts.append(f"{s.recovered} recovered")
        if s.cleaned:
            cleanup = []
            if s.not_found:
                cleanup.append(f"{s.not_found} not found")
            if s.terminal:
                cleanup.append(f"{s.terminal} terminal")
            if s.pre_run:
                cleanup.append(f"{s.pre_run} pre-run")
            if s.inactive:
                cleanup.append(f"{s.inactive} inactive")
            parts.append(f"{s.cleaned} cleaned ({', '.join(cleanup)})")
        if s.errors:
            parts.append(f"{s.errors} errors")

        if parts:
            _LOGGER.info("State recovery completed: %s", ", ".join(parts))
        else:
            _LOGGER.info("State recovery completed: no commands to process")
