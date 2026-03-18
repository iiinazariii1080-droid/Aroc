"""CommandDispatcher: position / navigateTo / cancel / estop command processing.

Standalone class (no mixin). Receives explicit dependencies via constructor.
Each command specifies its target service via CommandSpec.service.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests
from payload_models import AckPayload, CommandContext, ErrorDetail
from payload_validation import validate_headers

from shared.config_types import BridgeServices
from shared.constants import DEFAULT_COMMAND_SERVICE, NavigationState, StatusType
from shared.metrics import command_processing_duration_seconds, estop_attempts_total

logger = logging.getLogger(__name__)

# E-stop retry parameters (safety-critical: at-least-once delivery)
_ESTOP_MAX_RETRIES = 6
_ESTOP_BASE_BACKOFF_S = 0.1
_ESTOP_MAX_BACKOFF_S = 2.0
_ESTOP_HTTP_TIMEOUT = 3.0  # Shorter than general http_timeout to bound worst-case thread lifetime
_ESTOP_MAX_CONCURRENT = 5  # Cap concurrent e-stop threads to prevent leak under fault loops


@dataclass(frozen=True)
class CommandSpec:
    """Specification for a robot command dispatch."""

    name: str
    http_path: str
    required_fields: list[str] = field(default_factory=list)
    field_aliases: dict[str, list[str]] = field(default_factory=dict)
    service: str = "robot"

    def extract_required(self, data: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        values: dict[str, Any] = {}
        for field_name in self.required_fields:
            aliases = self.field_aliases.get(field_name, [field_name])
            value = None
            found = False
            for alias in aliases:
                if alias in data and data[alias] is not None:
                    value = data[alias]
                    found = True
                    break
            if not found:
                return {}, f"{field_name} is required for {self.name} command."
            values[field_name] = value
        return values, None


COMMAND_SPECS: dict[str, CommandSpec] = {
    "position": CommandSpec(
        name="position",
        http_path="/tasks/navigate",
        required_fields=["x", "y", "theta"],
        service="symovo",
    ),
    "navigateto": CommandSpec(
        name="navigateTo",
        http_path="/tasks/navigate",
        required_fields=["target_id"],
        service="robot",
    ),
    "cancel": CommandSpec(
        name="cancel",
        http_path="/tasks/cancel",
        required_fields=["task_id"],
        field_aliases={"task_id": ["task_id", "current_task_id"]},
        service="robot",
    ),
    "estop": CommandSpec(
        name="estop",
        http_path="/tasks/estop",
        service="robot",
    ),
}

SAFETY_GATED_COMMANDS = frozenset({"navigateto", "position", "cancel"})
TIMESTAMP_VALIDATED_COMMANDS = frozenset({"navigateto", "position", "cancel"})


def _build_navigate_body(
    command_id: str,
    data: dict[str, Any],
    extracted: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    target_id = extracted["target_id"]
    body = {
        "command_id": command_id,
        "target_id": target_id,
        "priority": data.get("priority", "normal"),
        "metadata": data.get("metadata"),
    }
    return body, {"target_id": target_id}


def _build_position_body(
    command_id: str,
    data: dict[str, Any],
    extracted: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {
        "command_id": command_id,
        "x": extracted["x"],
        "y": extracted["y"],
        "theta": extracted["theta"],
        "metadata": data.get("metadata"),
    }
    return body, {"target_id": f"({extracted['x']}, {extracted['y']}, {extracted['theta']})"}


def _build_cancel_body(
    command_id: str,
    data: dict[str, Any],
    extracted: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    task_id = extracted["task_id"]
    body = {
        "command_id": command_id,
        "task_id": task_id,
        "reason": data.get("reason"),
    }
    return body, {"task_id": task_id}


# Per-command body builders: return (body_dict, extra_context_dict).
# Adding a new command only requires a new spec + builder entry.
_BODY_BUILDERS: dict[str, Callable[..., tuple[dict[str, Any], dict[str, Any]]]] = {
    "position": _build_position_body,
    "navigateto": _build_navigate_body,
    "cancel": _build_cancel_body,
}


class CommandDispatcher:
    """Dispatches commands (navigateTo, position, cancel, estop) to backend services via HTTP.

    Each command specifies its target service via CommandSpec.service.
    Dependencies are injected via constructor.
    """

    def __init__(
        self,
        *,
        deps: BridgeServices,
        http_timeout: float = 5.0,
        estop_alert_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._build_http_url = deps.build_http_url
        self._submit_http = deps.submit_http
        self._publish_command_error = deps.publish_command_error
        self._finish_command = deps.finish_command
        self._get_http_session = deps.get_http_session
        self._auth_headers = deps.auth_headers
        self._send_response = deps.send_response
        self._store_command_history = deps.store_command_history
        self._publish_navigation_status = deps.publish_navigation_status
        self._http_timeout = http_timeout
        self._estop_alert_callback = estop_alert_callback
        self._shutdown_event: threading.Event | None = None
        self._estop_threads: list[threading.Thread] = []
        self._estop_threads_lock = threading.Lock()
        self._estop_semaphore = threading.Semaphore(_ESTOP_MAX_CONCURRENT)

    def set_shutdown_event(self, event: threading.Event) -> None:
        """Set shutdown event so e-stop threads can be interrupted during shutdown."""
        self._shutdown_event = event

    def join_estop_threads(self, timeout: float = 25.0) -> None:
        """Join all tracked e-stop threads with a total timeout budget."""
        with self._estop_threads_lock:
            threads = list(self._estop_threads)
        if not threads:
            return
        logger.info("[bridge] Waiting for %d e-stop thread(s) to finish...", len(threads))
        deadline = time.monotonic() + timeout
        for t in threads:
            remaining = max(0.0, deadline - time.monotonic())
            t.join(timeout=remaining)
            if t.is_alive():
                logger.warning("[bridge] E-stop thread %s did not finish in time", t.name)

    def dispatch(
        self,
        command_key: str,
        command_id: str,
        data: dict[str, Any],
        validated_timestamp: str | None = None,
    ) -> None:
        """Generic dispatch for standard commands (navigate, cancel).

        Looks up the CommandSpec and body builder by command_key,
        validates required fields, then dispatches via _dispatch_command.
        """
        spec = COMMAND_SPECS.get(command_key)
        if not spec:
            self._publish_command_error(command_key, command_id, f"Unknown command '{command_key}'")
            self._finish_command(command_id)
            return

        extracted, field_error = spec.extract_required(data)
        if field_error:
            self._publish_command_error(spec.name, command_id, field_error)
            self._finish_command(command_id)
            return

        builder = _BODY_BUILDERS.get(command_key)
        if builder:
            body, extra_context = builder(command_id, data, extracted)
        else:
            body = {"command_id": command_id, **extracted}
            extra_context = dict(extracted)

        self._dispatch_command(
            spec,
            command_id,
            data,
            body,
            validated_timestamp=validated_timestamp,
            extra_context=extra_context,
        )

    def handle_estop(self, command_id: str, data: dict[str, Any]) -> None:
        """E-stop with dedicated thread and retry — safety-critical, at-least-once delivery.

        Unlike navigate/cancel, estop does NOT go through the async executor.
        Spawns a dedicated daemon thread so it never blocks the MQTT handler pool.
        Retries up to _ESTOP_MAX_RETRIES times with exponential backoff
        to ensure the stop command reaches the robot service.
        """
        spec = COMMAND_SPECS["estop"]
        url = self._build_http_url(DEFAULT_COMMAND_SERVICE, spec.http_path)
        if not url:
            self._publish_command_error(spec.name, command_id, "Robot service is not configured.")
            self._finish_command(command_id)
            return

        headers, headers_error = validate_headers(data.get("headers"), error_context="Command")
        if headers_error:
            self._publish_command_error(spec.name, command_id, headers_error)
            self._finish_command(command_id)
            return

        if not self._estop_semaphore.acquire(blocking=False):
            logger.error(
                "[bridge] E-stop rejected: %d concurrent e-stop threads already running (command_id=%s)",
                _ESTOP_MAX_CONCURRENT,
                command_id,
            )
            self._publish_command_error(
                spec.name, command_id,
                f"Too many concurrent e-stop requests ({_ESTOP_MAX_CONCURRENT}). Retry shortly.",
            )
            self._finish_command(command_id)
            return

        t = threading.Thread(
            target=self._estop_retry_loop,
            args=(command_id, data, url, headers),
            name=f"estop-{command_id[:8]}",
            daemon=True,
        )
        with self._estop_threads_lock:
            self._estop_threads.append(t)
        t.start()

    def _estop_retry_loop(
        self,
        command_id: str,
        data: dict[str, Any],
        url: str,
        user_headers: dict[str, str] | None,
    ) -> None:
        """Retry loop for e-stop delivery (runs in dedicated thread)."""
        spec = COMMAND_SPECS["estop"]
        body = {
            "command_id": command_id,
            "reason": data.get("reason"),
        }

        request_headers = dict(self._auth_headers())
        if user_headers:
            request_headers.update(user_headers)

        # Dedicated session for e-stop — isolated from shared thread-local sessions
        session = requests.Session()
        try:
            last_error: str | None = None
            shutdown_detected = False
            for attempt in range(_ESTOP_MAX_RETRIES):
                try:
                    response = session.post(
                        url,
                        json=body,
                        headers=request_headers,
                        timeout=_ESTOP_HTTP_TIMEOUT,
                    )
                    if response.ok:
                        estop_attempts_total.labels(result="success").inc()
                        logger.info(
                            "[bridge] E-stop delivered successfully (attempt %d/%d, command_id=%s)",
                            attempt + 1,
                            _ESTOP_MAX_RETRIES,
                            command_id,
                        )
                    else:
                        logger.warning(
                            "[bridge] E-stop HTTP %d (attempt %d/%d, command_id=%s)",
                            response.status_code,
                            attempt + 1,
                            _ESTOP_MAX_RETRIES,
                            command_id,
                        )
                    try:
                        response_body = response.json()
                    except ValueError:
                        response_body = response.text

                    ack = AckPayload(
                        request_id=command_id,
                        service=DEFAULT_COMMAND_SERVICE,
                        success=response.ok,
                        status_code=response.status_code,
                        body=response_body,
                        command_id=command_id,
                        command_name=spec.name,
                        error=ErrorDetail.http_error(f"HTTP {response.status_code}") if not response.ok else None,
                    )
                    ack_payload = ack.to_dict()
                    self._send_response(DEFAULT_COMMAND_SERVICE, ack_payload)
                    self._store_command_history(command_id, ack_payload)

                    nav_state = NavigationState.ACKNOWLEDGED if response.ok else NavigationState.REJECTED
                    self._publish_navigation_status(
                        state=nav_state.value,
                        success=response.ok,
                        detail=ack_payload,
                        context={
                            "command_name": spec.name,
                            "command_id": command_id,
                            "status_type": StatusType.NAVIGATION.value,
                        },
                    )
                    self._finish_command(command_id)
                    return
                except requests.RequestException as exc:
                    estop_attempts_total.labels(result="retry").inc()
                    last_error = str(exc)
                    logger.error(
                        "[bridge] E-stop attempt %d/%d FAILED (command_id=%s): %s",
                        attempt + 1,
                        _ESTOP_MAX_RETRIES,
                        command_id,
                        exc,
                    )
                    if attempt < _ESTOP_MAX_RETRIES - 1:
                        if shutdown_detected:
                            logger.warning(
                                "[bridge] Shutdown: abandoning e-stop retries after extra attempt (command_id=%s)",
                                command_id,
                            )
                            break
                        backoff = min(_ESTOP_BASE_BACKOFF_S * (2**attempt), _ESTOP_MAX_BACKOFF_S)
                        if self._shutdown_event and self._shutdown_event.wait(timeout=backoff):
                            logger.warning(
                                "[bridge] Shutdown during e-stop retry (attempt %d/%d, command_id=%s)",
                                attempt + 1,
                                _ESTOP_MAX_RETRIES,
                                command_id,
                            )
                            shutdown_detected = True
                            # Continue to next attempt — try once more before giving up
                        else:
                            if not self._shutdown_event:
                                time.sleep(backoff)

            # All retries exhausted
            estop_attempts_total.labels(result="failure").inc()
            logger.critical(
                "[bridge] E-STOP FAILED after %d attempts (command_id=%s). "
                "Robot may still be in motion! Last error: %s",
                _ESTOP_MAX_RETRIES,
                command_id,
                last_error,
            )
            self._publish_command_error(
                spec.name,
                command_id,
                f"E-stop failed after {_ESTOP_MAX_RETRIES} attempts: {last_error}",
            )
            self._finish_command(command_id)

            if self._estop_alert_callback:
                try:
                    self._estop_alert_callback(command_id, last_error or "unknown")
                except Exception:
                    logger.error("[bridge] E-stop alert callback failed", exc_info=True)
        finally:
            session.close()
            self._estop_semaphore.release()
            current_thread = threading.current_thread()
            with self._estop_threads_lock:
                self._estop_threads = [t for t in self._estop_threads if t is not current_thread]

    def _dispatch_command(
        self,
        spec: CommandSpec,
        command_id: str,
        data: dict[str, Any],
        body: dict[str, Any],
        validated_timestamp: str | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        headers, headers_error = validate_headers(data.get("headers"), error_context="Command")
        if headers_error:
            self._publish_command_error(spec.name, command_id, headers_error)
            self._finish_command(command_id)
            return

        url = self._build_http_url(spec.service, spec.http_path)
        if not url:
            self._publish_command_error(spec.name, command_id, f"{spec.service} service is not configured.")
            self._finish_command(command_id)
            return

        if validated_timestamp:
            body["timestamp"] = validated_timestamp
            logger.debug("[bridge] Including timestamp in %s command body: %s", spec.name, validated_timestamp)

        context = CommandContext(
            command_name=spec.name,
            command_id=command_id,
            target_id=extra_context.get("target_id") if extra_context else None,
            task_id=extra_context.get("task_id") if extra_context else None,
        )

        self._submit_http(
            service=spec.service,
            request_id=command_id,
            method="POST",
            url=url,
            headers=headers,
            body=body,
            context=context,
        )
