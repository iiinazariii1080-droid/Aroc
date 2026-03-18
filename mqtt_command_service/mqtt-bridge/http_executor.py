"""HttpExecutor: HTTP request execution with backpressure and session management.

Handles the HTTP execution pipeline including session lifecycle, header preparation,
timeout resolution, semaphore-based backpressure, and service dedup integration.
"""

import concurrent.futures
import contextlib
import logging
import os
import threading
import time
from typing import Any

import requests
from command_dedup import CommandDeduplicator
from payload_models import AckPayload, CommandContext, ErrorDetail
from task_watcher import TaskWatcher

from shared.config_types import BridgeConfig, BridgeServices, ServiceConfig
from shared.constants import BRIDGE_HTTP_EXECUTOR_MAX_WORKERS, NavigationState
from shared.metrics import command_processing_duration_seconds, http_request_duration_seconds

logger = logging.getLogger(__name__)

_FORBIDDEN_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "proxy-authorization",
        "proxy-connection",
        "transfer-encoding",
        "content-length",
    }
)


class HttpExecutor:
    """Manages HTTP execution pipeline with backpressure and session management."""

    def __init__(
        self,
        config: BridgeConfig,
        shutdown_event: threading.Event,
        deps: BridgeServices,
        service_dedup: CommandDeduplicator,
        task_watcher: TaskWatcher,
    ) -> None:
        self._config = config
        self._shutdown = shutdown_event
        self._deps = deps
        self._service_dedup = service_dedup
        self._task_watcher = task_watcher
        self._long_operations = config.long_operations

        self._thread_local = threading.local()
        self._http_sessions: set[requests.Session] = set()
        self._http_sessions_lock = threading.Lock()
        self._http_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=BRIDGE_HTTP_EXECUTOR_MAX_WORKERS, thread_name_prefix="bridge-http"
        )
        self._http_semaphore = threading.Semaphore(BRIDGE_HTTP_EXECUTOR_MAX_WORKERS * 5)

    def get_http_session(self) -> requests.Session:
        if not hasattr(self._thread_local, "session"):
            if self._shutdown.is_set():
                raise RuntimeError("Cannot create HTTP session during shutdown")
            session = requests.Session()
            ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE")
            if ca_bundle:
                session.verify = ca_bundle
            self._thread_local.session = session
            with self._http_sessions_lock:
                self._http_sessions.add(session)
        return self._thread_local.session

    def prepare_request_headers(self, user_headers: dict[str, Any]) -> dict[str, str]:
        merged: dict[str, str] = {}
        merged.update(self._deps.auth_headers())
        for key, value in (user_headers or {}).items():
            if value is not None and key.lower() not in _FORBIDDEN_HEADERS:
                merged[key] = str(value)
        return merged

    def resolve_timeout(
        self,
        data_dict: dict[str, Any],
        service: str,
        path: str,
    ) -> float | None:
        """Resolve HTTP timeout from request data or long-operation config.

        Returns:
            float -- explicit or long-op timeout (clamped to 0.1..300s)
            None  -- use default http_timeout

        Raises:
            ValueError -- if timeout value in request data is not a valid number.
        """
        timeout = data_dict.get("timeout")
        if timeout is not None:
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
                raise ValueError(f"timeout must be a number, got {type(timeout).__name__}")
            return max(0.1, min(timeout, 300.0))
        if service in self._long_operations:
            service_ops = self._long_operations.get(service, {})
            if path in service_ops:
                return max(self._config.http_timeout, service_ops[path])
        return None

    def submit(
        self,
        service: str,
        request_id: str | None = None,
        method: str = "GET",
        url: str = "",
        headers: dict[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
        context: CommandContext | None = None,
    ) -> None:
        command_id = context.command_id if context else None
        if self._shutdown.is_set():
            self._deps.finish_command(command_id)
            return

        # Backpressure: reject immediately if executor queue is overloaded.
        if not self._http_semaphore.acquire(blocking=False):
            logger.warning(
                "[bridge] HTTP executor overloaded, rejecting request (service=%s, request_id=%s)",
                service,
                request_id,
                extra={"request_id": request_id, "command_id": command_id, "service": service},
            )
            ack = AckPayload(
                request_id=request_id,
                service=service,
                success=False,
                status_code=503,
                body={"detail": "Service overloaded, try again later"},
                error=ErrorDetail.processing_error("Executor queue full"),
                command_id=command_id,
            )
            self._deps.send_response(service, ack.to_dict())
            self._deps.finish_command(command_id)
            return

        service_cfg = self._config.services.get(service)
        try:
            self._http_executor.submit(
                self._execute_with_backpressure,
                service=service,
                service_cfg=service_cfg,
                request_id=request_id,
                method=method,
                url=url,
                headers=headers,
                body=body,
                timeout=timeout,
                context=context,
            )
        except RuntimeError:
            self._http_semaphore.release()
            self._deps.finish_command(command_id)

    def _execute_with_backpressure(self, **kwargs: Any) -> None:
        """Wrapper that releases the backpressure semaphore after execution."""
        try:
            self._execute(**kwargs)
        finally:
            self._http_semaphore.release()

    def _execute(
        self,
        service: str,
        request_id: str | None,
        method: str,
        url: str,
        headers: dict[str, Any],
        body: Any,
        timeout: float | None = None,
        context: CommandContext | None = None,
        service_cfg: ServiceConfig | None = None,
    ) -> None:

        command_id: str | None = context.command_id if context else None
        _cmd_start = time.time() if context else None
        _cmd_success = False
        logger.info(
            "[bridge] HTTP %s %s (request_id=%s, command_id=%s, service=%s)",
            method,
            url,
            request_id,
            command_id,
            service,
            extra={"request_id": request_id, "command_id": command_id, "service": service},
        )
        request_headers = self.prepare_request_headers(headers)
        if request_id:
            request_headers.setdefault("X-Request-ID", request_id)
        request_timeout = timeout if timeout is not None else self._config.http_timeout

        try:
            try:
                _http_start = time.time()
                response = self.get_http_session().request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    json=body if body is not None else None,
                    timeout=request_timeout,
                )
                http_request_duration_seconds.labels(
                    service=service,
                    method=method,
                    endpoint=service,
                    status_code=str(response.status_code),
                ).observe(time.time() - _http_start)
            except requests.RequestException as exc:
                logger.exception(
                    "[bridge] HTTP request failed (service=%s, url=%s, request_id=%s, command_id=%s)",
                    service,
                    url,
                    request_id,
                    command_id,
                    extra={"request_id": request_id, "command_id": command_id, "service": service},
                )
                error_ack = AckPayload(
                    request_id=request_id,
                    service=service,
                    success=False,
                    status_code=0,
                    error=ErrorDetail.http_error(str(exc)),
                    command_id=command_id,
                    command_name=context.command_name if context else None,
                )
                http_error_payload = error_ack.to_dict()
                self._deps.send_response(service, http_error_payload)
                if command_id:
                    self._deps.store_command_history(command_id, http_error_payload)
                if request_id and context is None:
                    self._service_dedup.store(request_id, http_error_payload)
                    self._service_dedup.finish(request_id)
                return

            try:
                response_body = response.json()
            except ValueError:
                response_body = response.text

            ack = AckPayload(
                request_id=request_id,
                service=service,
                success=response.ok,
                status_code=response.status_code,
                body=response_body,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() in {"content-type", "content-length", "x-request-id"}
                },
                command_id=command_id,
                command_name=context.command_name if context else None,
                error=ErrorDetail.http_error(f"HTTP {response.status_code}") if not response.ok else None,
            )

            task_id: str | None = None
            if service_cfg and service_cfg.watch_tasks and isinstance(response_body, dict):
                task_id = response_body.get("task_id")
                if task_id:
                    ack.task_id = task_id

            _cmd_success = ack.success
            ack_payload = ack.to_dict()
            self._deps.send_response(service, ack_payload)

            if task_id and self._task_watcher:
                origin_request_id = request_id or f"task-{task_id}"
                metadata: dict[str, Any] | None = None
                if context:
                    metadata = {
                        "command_id": command_id,
                        "command_name": context.command_name,
                        "target_id": context.target_id,
                        "task_id": context.task_id or task_id,
                        "status_type": context.status_type,
                    }
                self._task_watcher.start_watcher(service_cfg, task_id, origin_request_id, metadata=metadata)

            if command_id:
                self._deps.store_command_history(command_id, ack_payload)

            # Store service request result for dedup (non-command requests)
            if request_id and context is None:
                self._service_dedup.store(request_id, ack_payload)
                self._service_dedup.finish(request_id)

            if context and context.publish_navigation:
                navigation_state = NavigationState.ACKNOWLEDGED if response.ok else NavigationState.REJECTED
                ctx_dict = {
                    "command_name": context.command_name,
                    "command_id": context.command_id,
                    "target_id": context.target_id,
                    "task_id": context.task_id,
                    "status_type": context.status_type,
                }
                self._deps.publish_navigation_status(
                    state=navigation_state.value,
                    success=response.ok,
                    detail=ack_payload,
                    context=ctx_dict,
                )
        finally:
            if _cmd_start is not None and context:
                command_processing_duration_seconds.labels(
                    command_type=context.command_name or "unknown",
                    success=str(_cmd_success),
                ).observe(time.time() - _cmd_start)
            self._deps.finish_command(command_id)

    def shutdown(self) -> None:
        """Shutdown executor and close all HTTP sessions."""
        self._http_executor.shutdown(wait=True)
        with self._http_sessions_lock:
            for session in self._http_sessions:
                with contextlib.suppress(Exception):
                    session.close()
            self._http_sessions.clear()
