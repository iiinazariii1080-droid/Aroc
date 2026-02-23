"""Protocol defining the bridge interface used by TaskManagerMixin and CommandHandlerMixin.

Provides type safety for mixin classes that access attributes/methods
defined on MqttCommandBridge, eliminating all ``# type: ignore[attr-defined]`` comments.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

import requests

from config import BridgeConfig
from task_manager import TaskInfo


@runtime_checkable
class BridgeProtocol(Protocol):
    """Structural interface that MqttCommandBridge satisfies.

    Mixins should annotate ``self`` with this protocol so that
    attribute accesses are statically checked by mypy.
    """

    # --- Attributes ---
    config: BridgeConfig
    _shutdown: threading.Event
    _tasks_lock: threading.Lock
    _task_watcher_sem: threading.Semaphore
    _active_tasks: dict[str, TaskInfo]

    # --- Methods used by TaskManagerMixin ---
    def _get_http_session(self) -> requests.Session: ...
    def _auth_headers(self) -> dict[str, str]: ...
    def _send_response(self, service: str, payload: dict[str, Any]) -> None: ...
    def _store_command_history(self, command_id: str, payload: dict[str, Any]) -> None: ...
    def _publish_navigation_status(
        self,
        state: str,
        success: bool | None,
        detail: Any,
        context: dict[str, Any] | None,
    ) -> None: ...

    # --- Methods used by CommandHandlerMixin ---
    def _publish_command_error(
        self, command: str, command_id: str | None, message: str
    ) -> None: ...
    def _finish_command(self, command_id: str | None) -> None: ...
    def _build_http_url(self, service: str, path: str) -> str | None: ...
    def _submit_http(self, service: str, **kwargs: Any) -> None: ...

    # --- Methods used by TaskManagerMixin (self-referential) ---
    def _task_watcher_loop(
        self, service_cfg: Any, task_id: str, origin_request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...
    def _publish_task_timeout(
        self, service: str, task_id: str, origin_request_id: str,
        last_status_code: int | None, last_body: Any, last_error: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...
    def _remove_task(self, task_id: str) -> None: ...
