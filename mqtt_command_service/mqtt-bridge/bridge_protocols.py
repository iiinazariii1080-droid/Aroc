"""Typed dependency adapter satisfying the BridgeServices protocol.

Routes calls directly to the appropriate component (resp_publisher, http_executor,
dedup) — accepts components directly instead of the bridge object.
"""

from collections.abc import Callable, Mapping
from typing import Any

from command_dedup import CommandDeduplicator
from path_validator import build_http_url
from response_publisher import BridgeResponsePublisher

from shared.config_types import ServiceConfig


class BridgeDependencies:
    """Adapts bridge components to the BridgeServices protocol.

    Accepts individual components directly instead of the entire bridge
    object, preventing Law of Demeter violations through private attributes.

    ``http_executor`` may be ``None`` at construction time (circular
    dependency) — call :meth:`set_http_executor` before first use.
    """

    def __init__(
        self,
        resp_publisher: BridgeResponsePublisher,
        dedup: CommandDeduplicator,
        http_executor: Any | None,
        auth_headers_fn: Callable[[], dict[str, str]],
        services: Mapping[str, ServiceConfig],
        allowed_hosts: frozenset[str],
    ) -> None:
        self._resp_publisher = resp_publisher
        self._dedup = dedup
        self._http_executor = http_executor
        self._auth_headers_fn = auth_headers_fn
        self._services = services
        self._allowed_hosts = allowed_hosts

    def set_http_executor(self, executor: Any) -> None:
        """Wire the HTTP executor after construction (breaks circular dep).

        Must be called exactly once before any ``get_http_session`` or
        ``submit_http`` call.  Raises if called twice.
        """
        if self._http_executor is not None:
            raise RuntimeError("http_executor already wired")
        self._http_executor = executor

    # ---- HTTP session & auth ----
    def get_http_session(self) -> Any:
        if self._http_executor is None:
            raise RuntimeError("http_executor not wired — call set_http_executor() first")
        return self._http_executor.get_http_session()

    def auth_headers(self) -> dict[str, str]:
        return self._auth_headers_fn()

    # ---- Response publishing ----
    def send_response(self, service: str, payload: dict[str, Any]) -> None:
        self._resp_publisher.send_response(service, payload)

    def publish_navigation_status(
        self,
        state: str,
        success: bool | None,
        detail: Any,
        context: dict[str, Any] | None,
    ) -> None:
        self._resp_publisher.publish_navigation_status(state, success, detail, context)

    # ---- Command lifecycle ----
    def store_command_history(self, command_id: str, payload: dict[str, Any]) -> None:
        self._dedup.store(command_id, payload)

    def finish_command(self, command_id: str | None) -> None:
        self._dedup.finish(command_id)

    # ---- HTTP dispatching ----
    def build_http_url(self, service: str, path: str) -> str | None:
        return build_http_url(service, path, self._services, self._allowed_hosts)

    def submit_http(self, **kwargs: Any) -> None:
        if self._http_executor is None:
            raise RuntimeError("http_executor not wired — call set_http_executor() first")
        self._http_executor.submit(**kwargs)

    def publish_command_error(self, cmd: str, cid: str | None, msg: str) -> None:
        self._resp_publisher.publish_command_error(cmd, cid, msg)
