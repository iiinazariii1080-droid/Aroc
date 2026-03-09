from __future__ import annotations

import concurrent.futures
import logging
import uuid
from functools import wraps
from typing import Any, Callable, Dict

import requests

from app.core.settings import get_settings

_DECORATOR_TIMEOUT_SEC = 30


class JanusError(Exception):
    """Raised when Janus responds with an error payload."""


def _txid() -> str:
    return uuid.uuid4().hex


def janus_create_session() -> int:
    settings = get_settings()
    response = requests.post(
        settings.janus_url,
        json={"janus": "create", "transaction": _txid()},
        timeout=settings.janus_timeout,
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"create session failed: {payload}")
    return payload["data"]["id"]


def janus_attach_streaming(session_id: int) -> int:
    settings = get_settings()
    response = requests.post(
        f"{settings.janus_url}/{session_id}",
        json={
            "janus": "attach",
            "plugin": "janus.plugin.streaming",
            "transaction": _txid(),
        },
        timeout=settings.janus_timeout,
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"attach failed: {payload}")
    return payload["data"]["id"]


def janus_message(session_id: int, handle_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    response = requests.post(
        f"{settings.janus_url}/{session_id}/{handle_id}",
        json={"janus": "message", "transaction": _txid(), "body": body},
        timeout=settings.janus_timeout,
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"message failed: {payload}")
    return payload.get("plugindata", {}) or payload.get("jsep", {})


def janus_detach(session_id: int, handle_id: int) -> bool:
    """Detach handle. Returns True on success, False on failure."""
    settings = get_settings()
    try:
        requests.post(
            f"{settings.janus_url}/{session_id}/{handle_id}",
            json={"janus": "detach", "transaction": _txid()},
            timeout=settings.janus_timeout,
        )
        return True
    except Exception:
        logging.warning("Failed to detach Janus handle", exc_info=True)
        return False


def janus_destroy(session_id: int) -> bool:
    """Destroy session. Returns True on success, False on failure."""
    settings = get_settings()
    try:
        requests.post(
            f"{settings.janus_url}/{session_id}",
            json={"janus": "destroy", "transaction": _txid()},
            timeout=settings.janus_timeout,
        )
        return True
    except Exception:
        logging.warning("Failed to destroy Janus session", exc_info=True)
        return False


def with_streaming_handle(
    func: Callable[..., Dict[str, Any]]
) -> Callable[..., Dict[str, Any]]:
    @wraps(func)
    def _wrapper(*args, **kwargs):
        session_id = janus_create_session()
        handle_id = None
        try:
            handle_id = janus_attach_streaming(session_id)
            # Guard against func() hanging forever (e.g. Janus unresponsive).
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(func, session_id, handle_id, *args, **kwargs)
                return future.result(timeout=_DECORATOR_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError:
            logging.error(
                "with_streaming_handle timed out after %ds (session=%s, handle=%s)",
                _DECORATOR_TIMEOUT_SEC, session_id, handle_id,
            )
            raise JanusError(f"Streaming handle operation timed out after {_DECORATOR_TIMEOUT_SEC}s")
        finally:
            if handle_id is not None:
                if not janus_detach(session_id, handle_id):
                    logging.error("Orphaned Janus handle: session=%s handle=%s", session_id, handle_id)
            if not janus_destroy(session_id):
                logging.error("Orphaned Janus session: session=%s", session_id)

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
    }
    try:
        target_id = mount_id or get_settings().janus_mount_id
        raw = streaming_info(target_id)
        if not isinstance(raw, dict):
            logging.warning("Janus streaming_info returned unexpected structure: %s", type(raw))
            return _empty
        data = raw.get("data")
        if not isinstance(data, dict):
            logging.warning("Janus streaming_info 'data' missing or invalid: %s", type(data))
            return _empty
        # Janus nests plugindata → data → info → info
        info_outer = data.get("info", {})
        mount = info_outer.get("info", {}) if isinstance(info_outer, dict) else {}
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
        }
    except Exception as exc:
        logging.warning("janus_summary failed: %s", exc)
        return _empty

