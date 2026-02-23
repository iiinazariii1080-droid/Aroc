"""Task watcher lifecycle, polling and timeout management.

Manages background threads that poll HTTP task-status endpoints
and publish MQTT results when tasks complete or time out.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests

from constants import (
    MAX_TASK_WATCHERS,
    TASK_POLL_TIMEOUT_SECONDS,
    ErrorType,
    MessageType,
    NavigationState,
)
from utils import is_task_done

if TYPE_CHECKING:
    from bridge_protocol import BridgeProtocol

logger = logging.getLogger(__name__)


@dataclass
class TaskInfo:
    service: str
    request_id: str
    task_id: str
    thread: threading.Thread
    started_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    command_id: str | None = None


class TaskManagerMixin:
    """Task-watcher lifecycle, polling, timeout and cleanup.

    Mixed into :class:`MqttCommandBridge` — all ``self.*`` references
    are typed via :class:`BridgeProtocol`.
    """

    # ---- Task watchers ---------------------------------------------------

    def _start_task_watcher(
        self: BridgeProtocol,
        service_cfg: Any,
        task_id: str,
        origin_request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._task_watcher_sem.acquire(blocking=False):
            logger.warning(
                "Max task watchers (%d) reached, rejecting watcher for %s",
                MAX_TASK_WATCHERS, task_id,
            )
            return

        with self._tasks_lock:
            if task_id in self._active_tasks:
                logger.info("Task watcher already running for %s, skip.", task_id)
                self._task_watcher_sem.release()
                return

            watcher_metadata = dict(metadata or {})

            thread = threading.Thread(
                target=self._task_watcher_loop,
                name=f"task-watcher-{service_cfg.name}-{task_id}",
                args=(service_cfg, task_id, origin_request_id, watcher_metadata),
                daemon=True,
            )
            self._active_tasks[task_id] = TaskInfo(
                service=service_cfg.name,
                request_id=origin_request_id,
                task_id=task_id,
                thread=thread,
                started_at=time.time(),
                metadata=watcher_metadata,
                status="running",
                command_id=watcher_metadata.get("command_id"),
            )
            thread.start()

    def _task_watcher_loop(
        self: BridgeProtocol,
        service_cfg: Any,
        task_id: str,
        origin_request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        logger.info(
            "Starting task watcher for service=%s task_id=%s (req_id=%s)",
            service_cfg.name,
            task_id,
            origin_request_id,
        )

        status_url = f"{service_cfg.base_url}/tasks/status/{task_id}"
        start_time = time.time()
        last_body: Any = None
        last_status_code: int | None = None
        last_error: str | None = None
        base_interval = self.config.task_poll_interval
        current_interval = base_interval
        max_interval = base_interval * 8  # cap at 8× normal interval

        try:
            while not self._shutdown.is_set():
                elapsed = time.time() - start_time
                if elapsed >= self.config.task_poll_timeout:
                    self._publish_task_timeout(
                        service_cfg.name,
                        task_id,
                        origin_request_id,
                        last_status_code,
                        last_body,
                        last_error,
                        metadata,
                    )
                    return

                try:
                    poll_timeout = min(self.config.http_timeout, TASK_POLL_TIMEOUT_SECONDS)
                    resp = self._get_http_session().get(
                        status_url,
                        headers=self._auth_headers(),
                        timeout=poll_timeout,
                    )
                    last_status_code = resp.status_code
                    try:
                        last_body = resp.json()
                    except ValueError:
                        last_body = resp.text

                    logger.debug(
                        "Task watcher [%s] polled %s -> %s",
                        task_id,
                        status_url,
                        str(last_body)[:200],
                    )

                    # Reset backoff on successful poll
                    current_interval = base_interval

                    if is_task_done(last_body):
                        task_success = True
                        if isinstance(last_body, dict) and last_body.get("error"):
                            task_success = False
                        # Update status on TaskInfo before removal
                        with self._tasks_lock:
                            if task_id in self._active_tasks:
                                self._active_tasks[task_id].status = (
                                    "completed" if task_success else "failed"
                                )
                        payload: dict[str, Any] = {
                            "type": MessageType.RESULT.value,
                            "request_id": origin_request_id,
                            "task_id": task_id,
                            "service": service_cfg.name,
                            "success": task_success,
                            "status_code": last_status_code,
                            "body": last_body,
                            "error": None,
                        }
                        if not task_success:
                            payload["error"] = last_body.get("error")

                        self._send_response(service_cfg.name, payload)
                        if metadata and metadata.get("command_id"):
                            self._store_command_history(metadata["command_id"], payload)
                        nav_state = NavigationState.COMPLETED if payload.get("success") else NavigationState.FAILED
                        self._publish_navigation_status(
                            state=nav_state.value,
                            success=payload.get("success"),
                            detail=payload,
                            context=metadata,
                        )
                        return

                except requests.exceptions.Timeout:
                    last_error = "timeout"
                    poll_timeout = min(self.config.http_timeout, TASK_POLL_TIMEOUT_SECONDS)
                    logger.warning("Task watcher [%s] poll timeout after %.1fs", task_id, poll_timeout)
                    current_interval = min(current_interval * 2, max_interval)
                except requests.RequestException as exc:
                    last_error = str(exc)
                    logger.warning("Task watcher [%s] poll error: %s", task_id, exc)
                    current_interval = min(current_interval * 2, max_interval)

                time.sleep(current_interval)
        finally:
            self._remove_task(task_id)
            self._task_watcher_sem.release()
            logger.info("Task watcher finished for task_id=%s", task_id)

    def _publish_task_timeout(
        self: BridgeProtocol,
        service: str,
        task_id: str,
        origin_request_id: str,
        last_status_code: int | None,
        last_body: Any,
        last_error: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._tasks_lock:
            if task_id in self._active_tasks:
                self._active_tasks[task_id].status = "timeout"
        payload = {
            "type": MessageType.RESULT.value,
            "request_id": origin_request_id,
            "task_id": task_id,
            "service": service,
            "success": False,
            "status_code": last_status_code,
            "body": last_body,
            "error": {
                "type": ErrorType.HTTP_ERROR.value,
                "message": f"Task {task_id} did not finish in {self.config.task_poll_timeout} seconds",
                "last_error": last_error,
            },
        }
        self._send_response(service, payload)
        self._publish_navigation_status(
            state=NavigationState.TIMEOUT.value,
            success=False,
            detail=payload,
            context=metadata,
        )
        if metadata and metadata.get("command_id"):
            self._store_command_history(metadata["command_id"], payload)

    def _remove_task(self: BridgeProtocol, task_id: str) -> None:
        with self._tasks_lock:
            self._active_tasks.pop(task_id, None)

    # ---- Public task-tracking API ----------------------------------------

    def get_task_info(self: BridgeProtocol, task_id: str) -> TaskInfo | None:
        """Return a *copy* of TaskInfo for *task_id*, or ``None``."""
        with self._tasks_lock:
            info = self._active_tasks.get(task_id)
            if info is None:
                return None
            return copy.copy(info)

    def get_active_tasks(self: BridgeProtocol) -> dict[str, TaskInfo]:
        """Return a shallow copy of the active-tasks dict (thread-safe)."""
        with self._tasks_lock:
            return dict(self._active_tasks)

    def _stop_all_watchers(self: BridgeProtocol) -> None:
        with self._tasks_lock:
            watchers = list(self._active_tasks.values())
        if not watchers:
            return

        logger.info("Stopping %s active task watcher(s)", len(watchers))
        for info in watchers:
            thread = info.thread
            if thread.is_alive():
                thread.join(timeout=self.config.task_poll_interval * 2)

        with self._tasks_lock:
            self._active_tasks.clear()
