from __future__ import annotations

import logging
import uuid
from functools import wraps
from typing import Any, Callable, Dict

import requests

from app.core.settings import get_settings


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


def janus_detach(session_id: int, handle_id: int) -> None:
    settings = get_settings()
    try:
        requests.post(
            f"{settings.janus_url}/{session_id}/{handle_id}",
            json={"janus": "detach", "transaction": _txid()},
            timeout=settings.janus_timeout,
        )
    except Exception:
        logging.debug("Failed to detach Janus handle", exc_info=True)


def janus_destroy(session_id: int) -> None:
    settings = get_settings()
    try:
        requests.post(
            f"{settings.janus_url}/{session_id}",
            json={"janus": "destroy", "transaction": _txid()},
            timeout=settings.janus_timeout,
        )
    except Exception:
        logging.debug("Failed to destroy Janus session", exc_info=True)


def with_streaming_handle(
    func: Callable[..., Dict[str, Any]]
) -> Callable[..., Dict[str, Any]]:
    @wraps(func)
    def _wrapper(*args, **kwargs):
        session_id = janus_create_session()
        handle_id = None
        try:
            handle_id = janus_attach_streaming(session_id)
            return func(session_id, handle_id, *args, **kwargs)
        finally:
            if handle_id is not None:
                janus_detach(session_id, handle_id)
            janus_destroy(session_id)

    return _wrapper


@with_streaming_handle
def streaming_info(session_id: int, handle_id: int, mount_id: int) -> Dict[str, Any]:
    return janus_message(session_id, handle_id, {"request": "info", "id": mount_id})


def janus_summary(mount_id: int | None = None) -> Dict[str, Any]:
    target_id = mount_id or get_settings().janus_mount_id
    data = streaming_info(target_id).get("data", {})
    mount = data.get("info", {})
    media = (mount.get("media") or [{}])[0]
    return {
        "mountpoint_id": mount.get("id"),
        "enabled": mount.get("enabled"),
        "video_active": media.get("age_ms") is not None,
        "video_age_ms": media.get("age_ms"),
        "codec": media.get("codec"),
        "pt": media.get("pt"),
        "fmtp": media.get("fmtp"),
    }

