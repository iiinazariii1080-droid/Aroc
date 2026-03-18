"""Shared broker config fetcher with retry/backoff.

Used by mqtt-bridge and mqtt-telemetry to fetch broker config from config-api.
Includes disk-cache fallback so services can restart when config-api is down.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shared.config_types import MQTTConnectionConfig

import requests

from shared.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_CONFIG_CACHE_DIR = Path(os.environ.get("CONFIG_CACHE_DIR", "/tmp"))
_CONFIG_CACHE_FILENAME = "broker_config_cache.json"


def _cache_path() -> Path:
    return _CONFIG_CACHE_DIR / _CONFIG_CACHE_FILENAME


def _save_config_cache(data: dict[str, Any]) -> None:
    """Persist last-known-good config data to disk (owner-only permissions)."""
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except Exception:
        logger.debug("Failed to cache broker config to disk", exc_info=True)


def _load_config_cache() -> dict[str, Any] | None:
    """Load cached config from disk, or None if unavailable/corrupt."""
    try:
        path = _cache_path()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to load cached broker config", exc_info=True)
    return None


def fetch_config[T](
    config_api_url: str,
    build_config: Callable[[dict[str, Any]], T],
    *,
    api_key: str | None = None,
    max_retries: int = 6,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    timeout: float = 5.0,
) -> T:
    """Fetch broker config from config-api with retry/backoff.

    Generic helper — callers provide a ``build_config`` callback that
    converts the API response dict into any config type.

    When *api_key* is provided it is sent as ``X-API-Key`` header so
    config-api returns un-redacted passwords even when
    ``ALLOW_UNAUTHENTICATED_READ`` is the only auth bypass in place.

    On success, the raw API response is cached to disk so that a
    subsequent startup can proceed even if config-api is unreachable.
    """
    url = f"{config_api_url}/api/v1/config/broker"
    password_url = f"{config_api_url}/api/v1/config/broker/password"
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key

    # Respect REQUESTS_CA_BUNDLE for inter-service TLS verification.
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE")
    verify: bool | str = ca_bundle if ca_bundle else True

    def _attempt() -> T:
        resp = requests.get(url, headers=headers, timeout=timeout, verify=verify)
        resp.raise_for_status()
        data = resp.json()

        # Fetch password from the separate protected endpoint.
        # Failure is treated as a retryable error — starting with an
        # empty password would cause an opaque MQTT connection failure.
        pw_resp = requests.get(password_url, headers=headers, timeout=timeout, verify=verify)
        if pw_resp.ok:
            pw = pw_resp.json().get("mqtt_password", "")
            data["mqtt_password"] = pw
        else:
            raise RuntimeError(f"Broker password endpoint returned HTTP {pw_resp.status_code}")

        # Cache successful response for fallback on next startup
        _save_config_cache(data)
        return build_config(data)

    def _on_retry(attempt: int, exc: Exception) -> None:
        logger.warning("Failed to fetch broker config (attempt %d/%d): %s", attempt, max_retries, exc)

    try:
        return retry_with_backoff(
            _attempt,
            max_retries=max_retries,
            base_delay=initial_delay,
            max_delay=max_delay,
            on_retry=_on_retry,
            retryable_exceptions=(ConnectionError, TimeoutError, OSError, RuntimeError, requests.RequestException),
        )
    except Exception as fetch_err:
        # Fallback: try disk-cached config from a previous successful fetch
        cached = _load_config_cache()
        if cached:
            logger.critical(
                "config-api unreachable after %d attempts — using CACHED broker config. "
                "MQTT credentials may be stale! Restart when config-api recovers.",
                max_retries,
            )
            try:
                return build_config(cached)
            except Exception:
                logger.exception("Failed to build config from cache — cache may be corrupt")
        raise RuntimeError(
            f"Cannot fetch broker config from {config_api_url} after {max_retries} attempts "
            f"(no valid cache available)"
        ) from fetch_err


def build_mqtt_connection_config(
    data: dict[str, Any],
    robot_id: str,
    client_id: str,
    publish_qos: int,
) -> MQTTConnectionConfig:
    """Build an MQTTConnectionConfig from config-api response data.

    Single source of truth — used by both mqtt-bridge and mqtt-telemetry
    to avoid duplicating the field-mapping logic.
    """
    from shared.config_types import MQTTConnectionConfig
    from shared.constants import MQTT_DEFAULT_PORT
    from shared.env import resolve_mqtt_port

    use_tls = data.get("mqtt_use_tls", False)
    broker_port = resolve_mqtt_port(data.get("broker_port", MQTT_DEFAULT_PORT), use_tls)

    return MQTTConnectionConfig(
        broker=data.get("broker", ""),
        broker_port=broker_port,
        mqtt_user=data.get("mqtt_user", ""),
        mqtt_password=data.get("mqtt_password", ""),
        robot_id=robot_id,
        client_id=client_id,
        mqtt_publish_qos=max(0, min(2, publish_qos)),
        mqtt_use_tls=use_tls,
        mqtt_tls_insecure=data.get("mqtt_tls_insecure", False),
        auth_mode=data.get("auth_mode", "password"),
    )
