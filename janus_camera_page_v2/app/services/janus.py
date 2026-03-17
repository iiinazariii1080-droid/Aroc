from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, ValidationError

from app.core.settings import get_settings

logger = logging.getLogger(__name__)


class JanusError(Exception):
    """Raised when Janus responds with an error payload."""


def _txid() -> str:
    return uuid.uuid4().hex


# ── Pooled HTTP client lifecycle ─────────────────────────────────────
# Single httpx.AsyncClient used by all Janus REST calls — route handlers
# call the async functions directly; the watchdog thread bridges via
# asyncio.run_coroutine_threadsafe().

_client: Optional[httpx.AsyncClient] = None
# Initialised in start_janus_client() (called from lifespan when the
# event loop is running).  In Python <3.10 a Lock created at import time
# may bind to the wrong (or no) event loop.
_client_lock: Optional[asyncio.Lock] = None


def _get_client_lock() -> asyncio.Lock:
    """Return the client lock.  Must be called after ``start_janus_client()``."""
    if _client_lock is None:
        raise RuntimeError(
            "Janus client lock not initialised — call start_janus_client() first"
        )
    return _client_lock


async def _ensure_client() -> httpx.AsyncClient:
    """Return the pooled client, lazily creating it on first use."""
    global _client
    if _client is not None:
        return _client
    async with _get_client_lock():
        if _client is not None:
            return _client
        settings = get_settings()
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.janus_timeout),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
        return _client


async def start_janus_client() -> None:
    """Pre-create the pooled client during startup (idempotent).

    Initialises the asyncio.Lock here (not at import time) so it binds to
    the running event loop — safe on Python <3.10.  Also initialises the
    persistent monitoring session's lock.
    """
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    if _monitor_session._lock is None:
        _monitor_session._lock = asyncio.Lock()
    await _ensure_client()


async def stop_janus_client() -> None:
    """Close the pooled client on shutdown (idempotent)."""
    global _client
    async with _get_client_lock():
        if _client is not None:
            await _client.aclose()
            _client = None


# ── Core async Janus REST functions ──────────────────────────────────

async def janus_create_session() -> int:
    settings = get_settings()
    client = await _ensure_client()
    response = await client.post(
        settings.janus_url,
        json={"janus": "create", "transaction": _txid()},
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"create session failed: {payload}")
    return payload["data"]["id"]


async def janus_attach_streaming(session_id: int) -> int:
    settings = get_settings()
    client = await _ensure_client()
    response = await client.post(
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


async def janus_message(session_id: int, handle_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    client = await _ensure_client()
    response = await client.post(
        f"{settings.janus_url}/{session_id}/{handle_id}",
        json={"janus": "message", "transaction": _txid(), "body": body},
    )
    payload = response.json()
    if payload.get("janus") != "success":
        raise JanusError(f"message failed: {payload}")
    # Return plugindata when present (streaming plugin responses).
    # Fall back to jsep only when plugindata is absent — not when it is merely
    # an empty dict, which is a valid Janus success response.  The previous
    # `plugindata or jsep` caused silent mis-routing: an empty plugindata {}
    # (falsy) would swap in the jsep, causing janus_summary() to parse the
    # wrong structure and treat a healthy Janus as unreachable.
    if "plugindata" in payload:
        return payload["plugindata"]
    return payload.get("jsep", {})


async def _janus_detach(session_id: int, handle_id: int) -> bool:
    """Detach handle. Returns True on success, False on failure."""
    try:
        client = await _ensure_client()
        await client.post(
            f"{get_settings().janus_url}/{session_id}/{handle_id}",
            json={"janus": "detach", "transaction": _txid()},
        )
        return True
    except Exception:
        logger.warning("Failed to detach Janus handle", exc_info=True)
        return False


async def _janus_destroy(session_id: int) -> bool:
    """Destroy session. Returns True on success, False on failure."""
    try:
        client = await _ensure_client()
        await client.post(
            f"{get_settings().janus_url}/{session_id}",
            json={"janus": "destroy", "transaction": _txid()},
        )
        return True
    except Exception:
        logger.warning("Failed to destroy Janus session", exc_info=True)
        return False


# ── Pydantic models for Janus streaming info response ────────────────
# Validates the nested plugindata → data → info → info structure returned
# by the Janus streaming plugin.  Schema mismatches are logged as warnings
# so the watchdog can distinguish "unreachable" from "unexpected format".

class _JanusMedia(BaseModel):
    age_ms: Optional[int] = None
    codec: Optional[str] = None
    pt: Optional[int] = None
    fmtp: Optional[str] = None


class _JanusMount(BaseModel):
    id: Optional[int] = None
    enabled: Optional[bool] = None
    media: Optional[List[_JanusMedia]] = None


class _JanusStreamingData(BaseModel):
    info: Optional[_JanusMount] = None


class _JanusStreamingResponse(BaseModel):
    data: Optional[_JanusStreamingData] = None


# ── Persistent streaming session (async) ─────────────────────────────

class _PersistentStreamingSession:
    """Long-lived Janus session + streaming handle for high-frequency monitoring.

    Reduces watchdog cost from 5 HTTP calls/cycle (create+attach+info+detach+destroy)
    down to 1 (info). On session expiry or error, reconnects transparently and retries
    once per call so the watchdog never sees a spurious failure.

    Janus sessions stay alive as long as they receive messages; the watchdog runs every
    8 s by default so no explicit keepalive pings are needed.
    """

    def __init__(self) -> None:
        self._lock: Optional[asyncio.Lock] = None
        self._session_id: Optional[int] = None
        self._handle_id: Optional[int] = None
        self._closing: bool = False

    async def _connect(self) -> None:
        """Create a new session + handle (must be called with _lock held).

        If session creation succeeds but attach fails, the orphaned session
        is destroyed to avoid leaking Janus sessions on the server.
        """
        session_id = await janus_create_session()
        try:
            handle_id = await janus_attach_streaming(session_id)
        except Exception:
            # Destroy the orphaned session — best-effort, don't mask the
            # original error if destroy also fails.
            logger.warning("Attach failed after session %s created; destroying orphan", session_id)
            await _janus_destroy(session_id)
            raise
        self._session_id = session_id
        self._handle_id = handle_id

    def _invalidate(self) -> None:
        """Drop cached IDs (must be called with _lock held)."""
        self._session_id = None
        self._handle_id = None

    async def info(self, mount_id: int) -> Dict[str, Any]:
        """Return streaming plugin info for *mount_id*.

        1 HTTP call on the happy path; up to 6 calls
        (create+attach+message ×2) on session expiry.

        Will not reconnect if close() has been called — raises JanusError
        instead to prevent orphaned session creation during shutdown.
        """
        assert self._lock is not None, (
            "PersistentStreamingSession not initialised — "
            "call start_janus_client() first"
        )
        async with self._lock:
            if self._closing:
                raise JanusError("session is closing")
            for attempt in range(2):
                if self._session_id is None:
                    await self._connect()
                try:
                    return await janus_message(self._session_id, self._handle_id, {"request": "info", "id": mount_id})
                except Exception:
                    # Session may have expired — drop and retry once with a fresh session.
                    self._invalidate()
                    if attempt == 1:
                        raise
        raise JanusError("unreachable")  # pragma: no cover

    async def close(self) -> None:
        """Destroy the session and release the handle.

        Sets _closing flag under the lock to prevent info() from creating
        a new session while we're destroying the old one.  HTTP cleanup
        calls (detach/destroy) are intentionally made *outside* the lock
        to avoid holding the mutex during I/O.  Best-effort: errors from
        Janus are logged inside _janus_detach/_janus_destroy.
        """
        assert self._lock is not None, (
            "PersistentStreamingSession not initialised — "
            "call start_janus_client() first"
        )
        async with self._lock:
            self._closing = True
            session_id = self._session_id
            handle_id = self._handle_id
            self._invalidate()

        if session_id is not None:
            if handle_id is not None:
                await _janus_detach(session_id, handle_id)
            await _janus_destroy(session_id)


_monitor_session = _PersistentStreamingSession()


async def close_monitor_session() -> None:
    """Explicitly destroy the persistent monitoring session on shutdown.

    Janus sessions auto-expire after 60 s of inactivity, but explicitly
    destroying avoids leaving an orphaned session visible in Janus admin UI
    and releases the handle immediately on graceful shutdown.
    Best-effort: errors are logged and swallowed.
    """
    await _monitor_session.close()


# ── Stream freshness helper ──────────────────────────────────────────

def is_stream_fresh(age_ms: Any, threshold_ms: int) -> bool:
    """Check if a video stream age indicates a fresh/healthy stream.

    Centralises the age check used by healthz, health_stream, and the
    watchdog loop to prevent drift between the three call sites.
    """
    return age_ms is not None and isinstance(age_ms, (int, float)) and age_ms <= threshold_ms


async def janus_summary(mount_id: int | None = None) -> Dict[str, Any]:
    _empty: Dict[str, Any] = {
        "reachable": False,
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
        raw = await _monitor_session.info(target_id)
        if not isinstance(raw, dict):
            logger.warning("Janus streaming_info returned unexpected structure: %s", type(raw))
            return _empty

        # Validate response against Pydantic schema — distinguishes
        # "Janus unreachable" from "unexpected response format".
        try:
            parsed = _JanusStreamingResponse.model_validate(raw)
        except ValidationError as ve:
            logger.warning(
                "Janus response schema mismatch (possible API version change): %s",
                ve,
            )
            return {**_empty, "reachable": True, "schema_error": True}

        mount = None
        if parsed.data and parsed.data.info:
            mount = parsed.data.info

        if mount is None:
            logger.warning("Janus response parsed but mount info is empty")
            return {**_empty, "reachable": True}

        media = mount.media[0] if mount.media else _JanusMedia()
        return {
            "reachable": True,
            "mountpoint_id": mount.id,
            "enabled": mount.enabled,
            "video_active": media.age_ms is not None,
            "video_age_ms": media.age_ms,
            "codec": media.codec,
            "pt": media.pt,
            "fmtp": media.fmtp,
        }
    except Exception as exc:
        logger.warning("janus_summary failed: %s", exc)
        return _empty


def _reset_for_tests() -> None:
    """Reset module-level singletons for test isolation.

    Called by ``ServiceRegistry.reset()`` — keeps internal details private.
    """
    global _client, _client_lock, _monitor_session
    _client = None
    _client_lock = None  # re-create lazily in the new event loop
    _monitor_session = _PersistentStreamingSession()
