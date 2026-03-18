"""
Caching for API requests.
"""
import asyncio
import time
import hashlib
import json
from typing import Any, Callable, Dict, Optional, TypeVar
from functools import wraps

from app.config import settings

T = TypeVar('T')


class SimpleCache:
    """Simple in-memory cache with TTL."""
    
    def __init__(self, default_ttl: int = 300, max_size: int = 10_000):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        async with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() < entry['expires_at']:
                    return entry['value']
                else:
                    del self._cache[key]
            return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache."""
        async with self._lock:
            # Evict oldest entry if at capacity and this is a new key
            if key not in self._cache and len(self._cache) >= self._max_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k]['expires_at'])
                del self._cache[oldest_key]
            ttl = ttl or self._default_ttl
            self._cache[key] = {
                'value': value,
                'expires_at': time.time() + ttl
            }
    
    async def clear(self) -> None:
        """Clear the entire cache."""
        async with self._lock:
            self._cache.clear()
    
    async def invalidate(self, key: str) -> None:
        """Delete a specific key from cache."""
        async with self._lock:
            self._cache.pop(key, None)


# Global cache instance
cache = SimpleCache(default_ttl=settings.cache_ttl_seconds)

# Background eviction task (started lazily on first use)
_eviction_task: Any = None


async def _eviction_loop() -> None:
    """Periodically remove expired cache entries to prevent unbounded memory growth."""
    while True:
        try:
            await asyncio.sleep(60)
            now = time.time()
            async with cache._lock:
                expired = [k for k, v in cache._cache.items() if now >= v['expires_at']]
                for k in expired:
                    cache._cache.pop(k, None)
        except asyncio.CancelledError:
            break
        except Exception:
            pass  # best-effort


def ensure_eviction_task() -> None:
    """Start the background eviction task if not already running."""
    global _eviction_task
    if _eviction_task is None or (hasattr(_eviction_task, 'done') and _eviction_task.done()):
        try:
            _eviction_task = asyncio.create_task(_eviction_loop())
        except RuntimeError:
            pass  # No running loop


def cancel_eviction_task() -> None:
    """Cancel the background eviction task (call during shutdown)."""
    global _eviction_task
    if _eviction_task is not None and not _eviction_task.done():
        try:
            _eviction_task.cancel()
        except RuntimeError:
            pass  # Event loop already closed
    _eviction_task = None


def cached(ttl: Optional[int] = None, key_prefix: str = ""):
    """
    Decorator for caching function results.

    Args:
        ttl: Cache time-to-live in seconds
        key_prefix: Prefix for the cache key
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            ensure_eviction_task()
            # Build a stable cache key:
            # - do not use built-in hash() (salted per-process)
            # - do not include repr(self) (memory address) as part of the key
            call_args = args
            client_identity = None
            if call_args and hasattr(call_args[0], "__class__"):
                # For bound methods, never rely on dropping `self` without compensating identity.
                # Build a stable identity string from common client attributes.
                self_obj = call_args[0]
                ident_parts = [type(self_obj).__name__]
                for attr in ("base_url", "robot_number", "amr_id", "client_id", "host", "port"):
                    if hasattr(self_obj, attr):
                        val = getattr(self_obj, attr)
                        if val is not None:
                            ident_parts.append(f"{attr}={val}")
                client_identity = ";".join(ident_parts)
                # Drop `self` for bound methods (identity already captured above)
                call_args = call_args[1:]
            payload = {
                "fn": f"{func.__module__}.{getattr(func, '__qualname__', func.__name__)}",
                "client": client_identity,  # Include client identity to prevent collisions
                "args": call_args,
                "kwargs": dict(sorted(kwargs.items())),
            }
            payload_str = json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            cache_key = f"{key_prefix}:{digest}"
            
            # Try to get from cache
            cached_result = await cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute the function
            result = await func(*args, **kwargs)
            
            # Save to cache
            await cache.set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    return decorator


def cache_invalidate(pattern: str):
    """
    Decorator for invalidating cache on data changes.

    Uses **substring match**: any cache key containing *pattern* will be evicted.
    Example: pattern="symovo_station" invalidates both "symovo_stations:…" and
    "symovo_charging_stations:…" keys.  This is intentional — see symovo_service.py.

    Args:
        pattern: Substring to match against cache keys.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            result = await func(*args, **kwargs)
            
            # Invalidate cache by pattern
            async with cache._lock:
                keys_to_remove = [
                    key for key in cache._cache.keys() 
                    if pattern in key
                ]
                for key in keys_to_remove:
                    cache._cache.pop(key, None)
            
            return result
        
        return wrapper
    return decorator
