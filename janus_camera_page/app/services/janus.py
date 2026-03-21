from __future__ import annotations

import concurrent.futures
import logging
import uuid
from functools import wraps
from typing import Any, Callable, Dict

import threading

import httpx

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

_DECORATOR_TIMEOUT_SEC = 30

# Shared executor for Janus REST calls — avoids creating a new
# ThreadPoolExecutor on every watchdog tick / health check.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="janus-rest",
)

# Connection-pooled HTTP client singleton for Janus REST API.
_http: httpx.Client | None = None
_http_lock = threading.Lock()


def _get_client() -> httpx.Client:
    global _http
    if _http is None:
        with _http_lock:
            if _http is None:
                _http = httpx.Client(
                    timeout=get_settings().janus_timeout,
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                )
    return _http


def close_client() -> None:
    """Close the Janus REST HTTP client (called on app shutdown)."""
    global _http
    with _http_lock:
        if _http is not None:
            _http.close()
            _http = None


class JanusError(Exception):
    """Raised when Janus responds with an error payload."""


def _txid() -> str:
    return uuid.uuid4().hex


def janus_create_session() -> int:
    settings = get_settings()
    response = _get_client().post(
        settings.janus_url,
        json={"janus": "create", "transaction": _txid()},
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"create session failed: {payload}")
    return payload["data"]["id"]


def janus_attach_streaming(session_id: int) -> int:
    settings = get_settings()
    response = _get_client().post(
        f"{settings.janus_url}/{session_id}",
        json={
            "janus": "attach",
            "plugin": "janus.plugin.streaming",
            "transaction": _txid(),
        },
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"attach failed: {payload}")
    return payload["data"]["id"]


def janus_message(session_id: int, handle_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    response = _get_client().post(
        f"{settings.janus_url}/{session_id}/{handle_id}",
        json={"janus": "message", "transaction": _txid(), "body": body},
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"message failed: {payload}")
    if "plugindata" in payload:
        return payload["plugindata"]
    if "jsep" in payload:
        return payload["jsep"]
    return {}


def janus_detach(session_id: int, handle_id: int) -> bool:
    """Detach handle. Returns True on success, False on failure."""
    try:
        settings = get_settings()
        _get_client().post(
            f"{settings.janus_url}/{session_id}/{handle_id}",
            json={"janus": "detach", "transaction": _txid()},
        )
        return True
    except Exception:
        logger.warning("Failed to detach Janus handle", exc_info=True)
        return False


def janus_destroy(session_id: int) -> bool:
    """Destroy session. Returns True on success, False on failure."""
    try:
        settings = get_settings()
        _get_client().post(
            f"{settings.janus_url}/{session_id}",
            json={"janus": "destroy", "transaction": _txid()},
        )
        return True
    except Exception:
        logger.warning("Failed to destroy Janus session", exc_info=True)
        return False


def with_streaming_handle(
    func: Callable[..., Dict[str, Any]]
) -> Callable[..., Dict[str, Any]]:
    @wraps(func)
    def _wrapper(*args, **kwargs):
        session_id = janus_create_session()
        handle_id = None
        future: concurrent.futures.Future | None = None
        timed_out = False
        try:
            handle_id = janus_attach_streaming(session_id)
            # Guard against func() hanging forever (e.g. Janus unresponsive).
            future = _executor.submit(func, session_id, handle_id, *args, **kwargs)
            return future.result(timeout=_DECORATOR_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError:
            timed_out = True
            logger.error(
                "with_streaming_handle timed out after %ds (session=%s, handle=%s)",
                _DECORATOR_TIMEOUT_SEC, session_id, handle_id,
            )
            raise JanusError(f"Streaming handle operation timed out after {_DECORATOR_TIMEOUT_SEC}s")
        finally:
            # DEF-01 fix: on timeout, cancel the future and wait for the
            # executor thread to finish before destroying the session.
            # This prevents the race where the thread is still using
            # session_id/handle_id while we destroy them.
            if timed_out and future is not None:
                future.cancel()
                # Wait up to httpx timeout + margin for the thread to finish
                # its in-flight HTTP request.  This blocks the caller briefly
                # but prevents thread-pool exhaustion and use-after-destroy.
                _drain_sec = get_settings().janus_timeout + 1
                try:
                    future.result(timeout=_drain_sec)
                except Exception:
                    pass  # thread finished (success/error/cancel) — safe to clean up
            if handle_id is not None:
                if not janus_detach(session_id, handle_id):
                    logger.error("Orphaned Janus handle: session=%s handle=%s", session_id, handle_id)
                    try:
                        from app.metrics import orphaned_janus_sessions_total
                        orphaned_janus_sessions_total.inc()
                    except Exception:
                        pass
            if not janus_destroy(session_id):
                logger.error("Orphaned Janus session: session=%s", session_id)
                try:
                    from app.metrics import orphaned_janus_sessions_total
                    orphaned_janus_sessions_total.inc()
                except Exception:
                    pass

    return _wrapper


@with_streaming_handle
def streaming_info(session_id: int, handle_id: int, mount_id: int) -> Dict[str, Any]:
    return janus_message(session_id, handle_id, {"request": "info", "id": mount_id})


def janus_summary(mount_id: int | None = None) -> Dict[str, Any]:
    _empty: Dict[str, Any] = {
        "mountpoint_id": None,
        "enabled": None,
        "video_active": False,
        "video_age_ms": None,
        "codec": None,
        "pt": None,
        "fmtp": None,
        "status": "janus_unreachable",
    }
    try:
        target_id = mount_id or get_settings().janus_mount_id
        raw = streaming_info(target_id)
        if not isinstance(raw, dict):
            logger.warning("Janus streaming_info returned unexpected structure: %s", type(raw))
            try:
                from app.metrics import janus_summary_parse_errors_total
                janus_summary_parse_errors_total.inc()
            except Exception:
                pass
            return {**_empty, "status": "parse_error"}
        data = raw.get("data")
        if not isinstance(data, dict):
            logger.warning("Janus streaming_info 'data' missing or invalid: %s", type(data))
            try:
                from app.metrics import janus_summary_parse_errors_total
                janus_summary_parse_errors_total.inc()
            except Exception:
                pass
            return {**_empty, "status": "parse_error"}
        # Janus response: plugindata → data → info (the mount info dict)
        mount = data.get("info", {})
        if not isinstance(mount, dict):
            mount = {}
        media_list = mount.get("media")
        media = media_list[0] if isinstance(media_list, list) and media_list else {}
        return {
            "mountpoint_id": mount.get("id"),
            "enabled": mount.get("enabled"),
            "video_active": media.get("age_ms") is not None,
            "video_age_ms": media.get("age_ms"),
            "codec": media.get("codec"),
            "pt": media.get("pt"),
            "fmtp": media.get("fmtp"),
            "status": "ok",
        }
    except httpx.TimeoutException as exc:
        logger.warning("janus_summary timeout: %s", exc)
        return {**_empty, "status": "janus_timeout"}
    except httpx.ConnectError as exc:
        logger.warning("janus_summary connect error: %s", exc)
        return {**_empty, "status": "janus_unreachable"}
    except JanusError as exc:
        logger.warning("janus_summary Janus error: %s", exc)
        return {**_empty, "status": "janus_error"}
    except Exception as exc:
        logger.warning("janus_summary unexpected error: %s", exc)
        return {**_empty, "status": "internal_error"}

