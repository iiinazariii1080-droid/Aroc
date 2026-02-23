"""
Кеширование для API запросов.
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
    """Простой in-memory кеш с TTL."""
    
    def __init__(self, default_ttl: int = 300):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Получить значение из кеша."""
        async with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() < entry['expires_at']:
                    return entry['value']
                else:
                    del self._cache[key]
            return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Установить значение в кеш."""
        async with self._lock:
            ttl = ttl or self._default_ttl
            self._cache[key] = {
                'value': value,
                'expires_at': time.time() + ttl
            }
    
    async def clear(self) -> None:
        """Очистить весь кеш."""
        async with self._lock:
            self._cache.clear()
    
    async def invalidate(self, key: str) -> None:
        """Удалить конкретный ключ из кеша."""
        async with self._lock:
            self._cache.pop(key, None)


# Глобальный экземпляр кеша
cache = SimpleCache(default_ttl=settings.cache_ttl_seconds)


def cached(ttl: Optional[int] = None, key_prefix: str = ""):
    """
    Декоратор для кеширования результатов функций.
    
    Args:
        ttl: Время жизни кеша в секундах
        key_prefix: Префикс для ключа кеша
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Создаем устойчивый ключ кеша:
            # - не используем встроенный hash() (salted per-process)
            # - не включаем repr(self) (адрес в памяти) как часть ключа
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
            
            # Пытаемся получить из кеша
            cached_result = await cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Выполняем функцию
            result = await func(*args, **kwargs)
            
            # Сохраняем в кеш
            await cache.set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    return decorator


def cache_invalidate(pattern: str):
    """
    Декоратор для инвалидации кеша при изменении данных.

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
            
            # Инвалидируем кеш по паттерну
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
