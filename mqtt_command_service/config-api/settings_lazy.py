"""Lazy-loaded settings accessors — avoids circular imports with config module.

Each accessor uses functools.lru_cache for thread-safe one-time initialization.
lru_cache is safe on CPython (GIL) and on free-threaded Python 3.13+ (internal lock).
Tests can reset cached values via e.g. get_auth_disabled.cache_clear().
"""

import functools
import logging

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_auth_disabled() -> bool:
    from config import get_settings

    val = get_settings().auth_disabled
    if val:
        logger.warning("AUTH_DISABLED=true: all authentication is bypassed.")
    return val


@functools.lru_cache(maxsize=1)
def get_emergency_api_key() -> str:
    from config import get_settings

    return get_settings().emergency_api_key


@functools.lru_cache(maxsize=1)
def get_internal_service_key() -> str:
    from config import get_settings

    return get_settings().internal_service_key


@functools.lru_cache(maxsize=1)
def get_allow_unauthenticated_read() -> bool:
    from config import get_settings

    return get_settings().allow_unauthenticated_read
