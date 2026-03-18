"""HMAC key management for API key hashing."""

import functools
import hashlib
import hmac
import logging
import os
import secrets

from storage import get_store

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _get_hmac_key() -> bytes:
    """Return HMAC key, cached for process lifetime. Config changes require container restart."""
    store = get_store()
    stored = store.get("__hmac_secret__")
    if stored:
        return bytes.fromhex(stored)

    from config import get_settings

    settings = get_settings()
    env_secret = settings.hmac_secret
    if env_secret:
        return hashlib.sha256(env_secret.encode()).digest()

    # In production, HMAC_SECRET must be set explicitly.
    if os.environ.get("REQUIRE_HMAC_SECRET", "").lower() in ("true", "1", "yes"):
        raise RuntimeError(
            "REQUIRE_HMAC_SECRET is set but HMAC_SECRET is not configured. "
            "Set HMAC_SECRET env var for production deployments."
        )
    key = secrets.token_bytes(32)
    store.set("__hmac_secret__", key.hex())
    logger.warning(
        "HMAC_SECRET not configured — auto-generated and persisted to store. "
        "All API keys will be invalidated if the store is lost. "
        "Set HMAC_SECRET env var for production deployments."
    )
    return key


def hash_api_key(api_key: str) -> str:
    return hmac.new(_get_hmac_key(), api_key.encode(), hashlib.sha256).hexdigest()


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)
