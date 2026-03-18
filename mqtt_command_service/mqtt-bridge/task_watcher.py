"""TaskWatcher: background task polling and lifecycle management.

Standalone class (no mixin). Receives explicit dependencies via constructor.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from payload_models import ErrorDetail, ResultPayload

from shared.config_types import BridgeServices, ServiceConfig
from shared.constants import (
    MAX_TASK_WATCHERS,
    TASK_FAILURE_STATES,
    TASK_POLL_TIMEOUT_SECONDS,
    NavigationState,
)
from shared.metrics import active_task_watchers
from shared.utils import is_task_done

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


class TaskWatcher:
    """Manages background task-status polling threads.

    Dependencies are injected via constructor — no implicit coupling
    to the bridge object.
    """

    def __init__(
        self,
        *,
        task_poll_interval: float,
        task_poll_timeout: float,
        http_timeout: float,
        shutdown_event: threading.Event,
        deps: BridgeServices,
    ) -> None:
        self._task_poll_interval = task_poll_interval
        self._task_poll_timeout = task_poll_timeout
        self._http_timeout = http_timeout
        self._shutdown = shutdown_event
        self._get_http_session = deps.get_http_session
        self._auth_headers = deps.auth_headers
        self._send_response = deps.send_response
        self._store_command_history = deps.store_command_history
        self._publish_navigation_status = deps.publish_navigation_status

        self._tasks_lock = threading.Lock()
        self._active_tasks: dict[str, TaskInfo] = {}
        self._task_watcher_sem = threading.Semaphore(MAX_TASK_WATCHERS)

    # ---- Public API ---------------------------------------------------------

    def start_watcher(
        self,
        service_cfg: ServiceConfig,
        task_id: str,
        origin_request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._task_watcher_sem.acquire(blocking=False):
            logger.warning(
                "[bridge] Max task watchers (%d) reached, rejecting watcher for %s",
                MAX_TASK_WATCHERS,
                task_id,
            )
            result = ResultPayload(
                request_id=origin_request_id,
                task_id=task_id,
                service=service_cfg.name,
                success=False,
                status_code=None,
                error=ErrorDetail.routing_error(
                    f"Max concurrent task watchers ({MAX_TASK_WATCHERS}) reached, command rejected"
                ),
            )
            error_payload = result.to_dict()
            self._send_response(service_cfg.name, error_payload)
            if metadata and metadata.get("command_id"):
                self._store_command_history(metadata["command_id"], error_payload)
            self._publish_navigation_status(
                state=NavigationState.FAILED.value,
                success=False,
                detail=error_payload,
                context=metadata,
            )
            return

        with self._tasks_lock:
            if task_id in self._active_tasks:
                logger.info("[bridge] Task watcher already running for %s, skip.", task_id)
                self._task_watcher_sem.release()
                return

            watcher_metadata = dict(metadata or {})

            thread = threading.Thread(
                target=self._watcher_loop,
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
            active_task_watchers.labels(service="mqtt-bridge").set(len(self._active_tasks))

    def get_task_info(self, task_id: str) -> TaskInfo | None:
        with self._tasks_lock:
            info = self._active_tasks.get(task_id)
            if info is None:
                return None
            return copy.copy(info)

    def get_active_tasks(self) -> dict[str, TaskInfo]:
        with self._tasks_lock:
            return dict(self._active_tasks)

    @property
    def active_task_count(self) -> int:
        with self._tasks_lock:
            return len(self._active_tasks)

    @property
    def active_task_ids(self) -> list[str]:
        with self._tasks_lock:
            return list(self._active_tasks.keys())

    def stop_all(self) -> None:
        with self._tasks_lock:
            watchers = list(self._active_tasks.values())
        if not watchers:
            return

        logger.info("[bridge] Stopping %s active task watcher(s)", len(watchers))
        join_timeout = max(self._task_poll_interval * 2, 5.0)
        for info in watchers:
            thread = info.thread
            if thread.is_alive():
                thread.join(timeout=join_timeout)
                if thread.is_alive():
                    logger.warning(
                        "[bridge] Task watcher thread %s did not stop within %.1fs",
                        thread.name,
                        join_timeout,
                    )

        # Only clear tasks whose threads have actually finished.
        # Orphaned threads will clean themselves up via _remove_task.
        with self._tasks_lock:
            finished = [tid for tid, info in self._active_tasks.items() if not info.thread.is_alive()]
            for tid in finished:
                del self._active_tasks[tid]

    # ---- Internal -----------------------------------------------------------

    def _watcher_loop(
        self,
        service_cfg: ServiceConfig,
        task_id: str,
        origin_request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        logger.info(
            "[bridge] Starting task watcher for service=%s task_id=%s (req_id=%s)",
            service_cfg.name,
            task_id,
            origin_request_id,
            extra={
                "request_id": origin_request_id,
                "command_id": (metadata or {}).get("command_id"),
                "service": service_cfg.name,
            },
        )

        status_url = f"{service_cfg.base_url}/tasks/status/{task_id}"
        start_time = time.time()
        last_body: Any = None
        last_status_code: int | None = None
        last_error: str | None = None
        base_interval = self._task_poll_interval
        poll_timeout = self._task_poll_timeout
        current_interval = base_interval
        max_interval = base_interval * 8

        try:
            while not self._shutdown.is_set():
                elapsed = time.time() - start_time
                if elapsed >= poll_timeout:
                    self._publish_task_timeout(
                        service_cfg.name,
                        task_id,
                        origin_request_id,
                        last_status_code,
                        last_body,
                        last_error,
                        metadata,
                        poll_timeout=poll_timeout,
                    )
                    return

                try:
                    request_timeout = min(self._http_timeout, TASK_POLL_TIMEOUT_SECONDS)
                    resp = self._get_http_session().get(
                        status_url,
                        headers=self._auth_headers(),
                        timeout=request_timeout,
                    )
                    last_status_code = resp.status_code
                    try:
                        last_body = resp.json()
                    except ValueError:
                        last_body = resp.text

                    logger.debug(
                        "[bridge] Task watcher [%s] polled %s -> %s",
                        task_id,
                        status_url,
                        str(last_body)[:200],
                    )

                    current_interval = base_interval

                    if is_task_done(last_body):
                        task_success = True
                        if isinstance(last_body, dict):
                            status_val = str(last_body.get("state", "") or last_body.get("status", "")).lower()
                            if (
                                last_body.get("error")
                                or last_body.get("success") is False
                                or status_val in TASK_FAILURE_STATES
                            ):
                                task_success = False
                        with self._tasks_lock:
                            if task_id in self._active_tasks:
                                self._active_tasks[task_id].status = "completed" if task_success else "failed"
                        task_error: ErrorDetail | None = None
                        if not task_success:
                            raw_err = last_body.get("error") if isinstance(last_body, dict) else None
                            if isinstance(raw_err, str):
                                task_error = ErrorDetail.http_error(raw_err)
                            elif isinstance(raw_err, dict):
                                task_error = ErrorDetail(
                                    type=raw_err.get("type", "error"),
                                    message=raw_err.get("message", str(raw_err)),
                                )
                            else:
                                task_error = ErrorDetail.http_error("Task failed")
                        result = ResultPayload(
                            request_id=origin_request_id,
                            task_id=task_id,
                            service=service_cfg.name,
                            success=task_success,
                            status_code=last_status_code,
                            body=last_body,
                            error=task_error,
                        )
                        payload = result.to_dict()

                        if metadata and metadata.get("command_id"):
                            self._store_command_history(metadata["command_id"], payload)
                        self._send_response(service_cfg.name, payload)
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
                    effective_timeout = min(self._http_timeout, TASK_POLL_TIMEOUT_SECONDS)
                    logger.warning("[bridge] Task watcher [%s] poll timeout after %.1fs", task_id, effective_timeout)
                    current_interval = min(current_interval * 2, max_interval)
                except requests.RequestException as exc:
                    last_error = str(exc)
                    logger.warning("[bridge] Task watcher [%s] poll error: %s", task_id, exc)
                    current_interval = min(current_interval * 2, max_interval)

                self._shutdown.wait(timeout=current_interval)
        finally:
            self._remove_task(task_id)
            self._task_watcher_sem.release()
            logger.info(
                "[bridge] Task watcher finished for task_id=%s",
                task_id,
                extra={"request_id": origin_request_id, "command_id": (metadata or {}).get("command_id")},
            )

    def _publish_task_timeout(
        self,
        service: str,
        task_id: str,
        origin_request_id: str,
        last_status_code: int | None,
        last_body: Any,
        last_error: str | None,
        metadata: dict[str, Any] | None = None,
        poll_timeout: float | None = None,
    ) -> None:
        effective_timeout = poll_timeout if poll_timeout is not None else self._task_poll_timeout
        with self._tasks_lock:
            if task_id in self._active_tasks:
                self._active_tasks[task_id].status = "timeout"
        result = ResultPayload(
            request_id=origin_request_id,
            task_id=task_id,
            service=service,
            success=False,
            status_code=last_status_code,
            body=last_body,
            error=ErrorDetail.http_error(
                f"Task {task_id} did not finish in {effective_timeout} seconds",
                last_error=last_error,
            ),
        )
        payload = result.to_dict()
        if metadata and metadata.get("command_id"):
            self._store_command_history(metadata["command_id"], payload)
        self._send_response(service, payload)
        self._publish_navigation_status(
            state=NavigationState.TIMEOUT.value,
            success=False,
            detail=payload,
            context=metadata,
        )

    def _remove_task(self, task_id: str) -> None:
        with self._tasks_lock:
            self._active_tasks.pop(task_id, None)
            active_task_watchers.labels(service="mqtt-bridge").set(len(self._active_tasks))
