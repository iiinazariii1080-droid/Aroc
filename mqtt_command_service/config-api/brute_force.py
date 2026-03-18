"""Brute-force protection: in-memory tracking with persistent lockout.

Lockouts are persisted to ConfigStore so they survive restarts.
"""

import hashlib
import hmac
import logging
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from hmac_keys import _get_hmac_key
from storage import get_store

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)
_PRUNE_INTERVAL = 900
_MAX_TRACKED_IPS = 10_000
_MAX_PERSISTED_LOCKOUTS = 10_000
_LOCKOUT_STORE_PREFIX = "lockout:"

_failed_attempts: dict[str, list[datetime]] = defaultdict(list)
_failed_attempts_lock = threading.Lock()
_last_prune_time: float = 0.0


def _hash_ip(ip: str) -> str:
    """Hash IP address for storage (avoid persisting raw IPs).

    Uses keyed HMAC so that an attacker with store access cannot
    brute-force the original IP (IPv4 space is only 32 bits).
    """
    return hmac.new(_get_hmac_key(), ip.encode(), hashlib.sha256).hexdigest()[:16]


def _maybe_prune_stale_entries() -> None:
    import time as _time

    global _last_prune_time
    now_mono = _time.monotonic()
    cutoff = datetime.now(UTC) - LOCKOUT_DURATION
    with _failed_attempts_lock:
        if now_mono - _last_prune_time < _PRUNE_INTERVAL:
            # Even outside the prune interval, enforce size limit
            if len(_failed_attempts) > _MAX_TRACKED_IPS:
                _evict_oldest_entries(cutoff)
            return
        _last_prune_time = now_mono
        stale = [ip for ip, attempts in _failed_attempts.items() if not attempts or attempts[-1] < cutoff]
        for ip in stale:
            del _failed_attempts[ip]
        # After pruning stale, if still over limit, evict oldest
        if len(_failed_attempts) > _MAX_TRACKED_IPS:
            _evict_oldest_entries(cutoff)


def _evict_oldest_entries(cutoff: datetime) -> None:
    """Evict oldest entries to bring _failed_attempts under _MAX_TRACKED_IPS.

    Must be called while holding _failed_attempts_lock.
    """
    by_recency = sorted(
        _failed_attempts.items(),
        key=lambda item: item[1][-1] if item[1] else datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    to_keep = {ip for ip, _ in by_recency[:_MAX_TRACKED_IPS]}
    to_remove = [ip for ip in _failed_attempts if ip not in to_keep]
    for ip in to_remove:
        del _failed_attempts[ip]


def _persist_lockout(ip: str) -> None:
    """Persist a lockout entry to ConfigStore so it survives restarts.

    Preserves existing attempt history written by _persist_failed_attempt.
    """
    try:
        ip_hash = _hash_ip(ip)
        expiry = (datetime.now(UTC) + LOCKOUT_DURATION).isoformat()
        store = get_store()
        key = f"{_LOCKOUT_STORE_PREFIX}{ip_hash}"
        # Read-then-update to preserve attempt history
        entry = store.get(key)
        if isinstance(entry, dict):
            entry["expiry"] = expiry
            entry["locked"] = True
        else:
            entry = {"expiry": expiry, "locked": True}
        store.set(key, entry)
        # Prune oldest lockout entries if over limit
        store.prune_prefix(_LOCKOUT_STORE_PREFIX, _MAX_PERSISTED_LOCKOUTS)
    except Exception:
        logger.debug("Failed to persist lockout for IP", exc_info=True)


def _persist_failed_attempt(ip: str) -> int:
    """Record a failed attempt in the persistent store. Returns current count."""
    try:
        ip_hash = _hash_ip(ip)
        key = f"{_LOCKOUT_STORE_PREFIX}{ip_hash}"
        store = get_store()
        entry = store.get(key)
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        cutoff = (now - LOCKOUT_DURATION).isoformat()

        if isinstance(entry, dict):
            # Prune expired attempts
            attempts = [t for t in entry.get("attempts", []) if t > cutoff]
        else:
            attempts = []

        attempts.append(now_iso)
        locked = len(attempts) >= MAX_FAILED_ATTEMPTS
        data: dict = {"attempts": attempts, "locked": locked}
        if locked:
            data["expiry"] = (now + LOCKOUT_DURATION).isoformat()
        store.set(key, data)
        return len(attempts)
    except Exception:
        logger.warning("Failed to persist failed attempt — falling back to in-memory count", exc_info=True)
        with _failed_attempts_lock:
            recent = [t for t in _failed_attempts.get(ip, []) if datetime.now(UTC) - t < LOCKOUT_DURATION]
            return len(recent)


def _check_persisted_lockout(ip: str) -> bool:
    """Check if a persisted lockout exists and is still active."""
    try:
        ip_hash = _hash_ip(ip)
        entry = get_store().get(f"{_LOCKOUT_STORE_PREFIX}{ip_hash}")
        if not entry:
            return False
        # Legacy format: plain ISO timestamp strings from earlier versions.
        # Expire naturally within LOCKOUT_DURATION (15 min). No migration needed.
        if isinstance(entry, str):
            expiry = datetime.fromisoformat(entry)
        elif isinstance(entry, dict):
            if not entry.get("locked"):
                return False
            expiry_str = entry.get("expiry")
            if not expiry_str:
                return False
            expiry = datetime.fromisoformat(expiry_str)
        else:
            return False
        if datetime.now(UTC) < expiry:
            return True
        # Expired — clean up
        get_store().delete(f"{_LOCKOUT_STORE_PREFIX}{ip_hash}")
    except Exception:
        logger.debug("Failed to check persisted lockout", exc_info=True)
    return False


def _clear_persisted_lockout(ip: str) -> None:
    """Remove a persisted lockout entry on successful auth."""
    try:
        ip_hash = _hash_ip(ip)
        get_store().delete(f"{_LOCKOUT_STORE_PREFIX}{ip_hash}")
    except Exception:
        logger.debug("Failed to clear persisted lockout for IP", exc_info=True)


def record_failed_attempt(ip: str | None) -> None:
    """Record a failed authentication attempt."""
    if not ip:
        return
    now = datetime.now(UTC)
    # Persistent store is the single source of truth for lockout decisions.
    # In-memory dict is a performance cache only.
    persisted_count = _persist_failed_attempt(ip)
    with _failed_attempts_lock:
        _failed_attempts[ip].append(now)
    # Use persisted count as authoritative; fall back to in-memory on failure.
    if persisted_count == 0:
        with _failed_attempts_lock:
            recent = [t for t in _failed_attempts[ip] if now - t < LOCKOUT_DURATION]
            count = len(recent)
    else:
        count = persisted_count
    if count >= MAX_FAILED_ATTEMPTS:
        _persist_lockout(ip)


def clear_failed_attempts(ip: str | None) -> None:
    """Clear failed attempts on successful auth."""
    if not ip:
        return
    with _failed_attempts_lock:
        _failed_attempts.pop(ip, None)
    _clear_persisted_lockout(ip)


def check_lockout(ip: str) -> bool:
    """Return True if the IP is locked out (persistent or in-memory)."""
    if _check_persisted_lockout(ip):
        return True
    _maybe_prune_stale_entries()
    now = datetime.now(UTC)
    with _failed_attempts_lock:
        attempts = _failed_attempts.get(ip, [])
        recent = [t for t in attempts if now - t < LOCKOUT_DURATION]
        _failed_attempts[ip] = recent
        if len(recent) >= MAX_FAILED_ATTEMPTS:
            _persist_lockout(ip)
            return True
    return False
