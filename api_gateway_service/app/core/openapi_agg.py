import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import logging
from typing import Any, Dict

import httpx
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .config import SERVICE_MAP
from .http_proxy_utils import join_url
from .openapi_utils import strip_prefix, rename_component_refs, merge_component_sections

logger = logging.getLogger(__name__)

# How often the background task refreshes the cached OpenAPI schema (seconds).
_OPENAPI_REFRESH_INTERVAL_S = 120.0


class OpenAPIRefreshError(RuntimeError):
    def __init__(self, reason: str, exc: Exception) -> None:
        super().__init__(f"{reason}: {exc}")
        self.reason = reason
        self.original = exc


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _classify_openapi_error(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "upstream_connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "upstream_read_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "upstream_connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "upstream_http_status"
    if isinstance(exc, httpx.HTTPError):
        return "upstream_http_error"
    if isinstance(exc, ValueError):
        return "schema_value_error"
    if isinstance(exc, (TypeError, KeyError)):
        return "schema_structure_error"
    if isinstance(exc, (RuntimeError, AttributeError)):
        return "runtime_state_error"
    return "unexpected_error"


def _ensure_refresh_metrics(app: FastAPI) -> Dict[str, Any]:
    metrics = getattr(app.state, "_openapi_refresh_metrics", None)
    if metrics is None:
        metrics = {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "failure_reasons": defaultdict(int),
            "last_success_at": None,
            "last_failure_at": None,
            "last_failure_reason": None,
            "last_failure_error": None,
        }
        app.state._openapi_refresh_metrics = metrics
    return metrics


def _mark_refresh_attempt(app: FastAPI) -> None:
    metrics = _ensure_refresh_metrics(app)
    metrics["attempts"] += 1


def _mark_refresh_success(app: FastAPI) -> None:
    metrics = _ensure_refresh_metrics(app)
    metrics["successes"] += 1
    metrics["last_success_at"] = _iso_now()


def _mark_refresh_failure(app: FastAPI, reason: str, exc: Exception) -> None:
    metrics = _ensure_refresh_metrics(app)
    metrics["failures"] += 1
    metrics["failure_reasons"][reason] += 1
    metrics["last_failure_at"] = _iso_now()
    metrics["last_failure_reason"] = reason
    metrics["last_failure_error"] = str(exc)


def get_openapi_refresh_metrics(app: FastAPI) -> Dict[str, Any]:
    metrics = _ensure_refresh_metrics(app)
    return {
        "attempts": metrics["attempts"],
        "successes": metrics["successes"],
        "failures": metrics["failures"],
        "failure_reasons": dict(metrics["failure_reasons"]),
        "last_success_at": metrics["last_success_at"],
        "last_failure_at": metrics["last_failure_at"],
        "last_failure_reason": metrics["last_failure_reason"],
        "last_failure_error": metrics["last_failure_error"],
    }


async def _build_aggregated_schema(app: FastAPI) -> Dict[str, Any]:
    try:
        return await aggregate_services_openapi(app)
    except (
        httpx.HTTPError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        AttributeError,
    ) as exc:
        raise OpenAPIRefreshError(_classify_openapi_error(exc), exc) from exc


async def _fetch_one_spec(
    client: httpx.AsyncClient,
    service_key: str,
    cfg: Dict[str, str],
) -> tuple[str, Dict[str, Any] | None]:
    """Fetch openapi.json from a single upstream service."""
    try:
        resp = await client.get(
            join_url(cfg["url"], "openapi.json"),
            timeout=5.0,
        )
        resp.raise_for_status()
        return service_key, resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("Failed to fetch OpenAPI spec from %s: %s", service_key, exc)
        return service_key, None


async def aggregate_services_openapi(app: FastAPI) -> Dict[str, Any]:
    """
    Build a merged OpenAPI schema from the gateway's own routes
    plus every upstream service that exposes /openapi.json.

    Uses the shared async HTTP client — no event-loop blocking.
    Upstream specs are fetched in parallel via asyncio.gather.
    """
    base_schema = get_openapi(
        title=app.title,
        version="1.0.0",
        routes=[r for r in app.routes if getattr(r, "include_in_schema", True)],
        description="API Gateway with aggregated service routes",
    )
    base_paths = base_schema.setdefault("paths", {})
    base_components = base_schema.setdefault("components", {})

    client: httpx.AsyncClient | None = getattr(app.state, "http_client", None)
    if client is None or client.is_closed:
        logger.warning("HTTP client not available — returning gateway-only OpenAPI schema")
        return base_schema

    # Fetch all upstream specs concurrently
    results = await asyncio.gather(
        *[_fetch_one_spec(client, k, c) for k, c in SERVICE_MAP.items()]
    )

    for service_key, spec in results:
        if spec is None:
            continue

        upstream_prefix = SERVICE_MAP[service_key].get("prefix", "")
        pref = f"{service_key}_"
        spec = rename_component_refs(spec, pref)

        for path_url, path_item in (spec.get("paths") or {}).items():
            stripped = strip_prefix(path_url, upstream_prefix)
            if not stripped.startswith("/"):
                stripped = "/" + stripped
            # Prefix operationId to avoid collisions across services in Swagger UI
            for method_item in path_item.values():
                if isinstance(method_item, dict) and "operationId" in method_item:
                    method_item["operationId"] = f"{service_key}_{method_item['operationId']}"
            base_paths[f"/api/v1/{service_key}{stripped}"] = path_item

        comps = spec.get("components") or {}
        prefixed = {}
        for sec, payload in comps.items():
            sec_out = {f"{pref}{name}": val for name, val in (payload or {}).items()}
            prefixed[sec] = sec_out
        merge_component_sections(base_components, prefixed)

    return base_schema


async def populate_openapi_cache(app: FastAPI) -> None:
    """Build and store the aggregated OpenAPI schema. Called once during startup."""
    _mark_refresh_attempt(app)
    try:
        schema = await _build_aggregated_schema(app)
        app.state._openapi_cache = schema
        _mark_refresh_success(app)
        logger.info(
            "OpenAPI cache populated (%d paths)",
            len(schema.get("paths", {})),
        )
    except OpenAPIRefreshError as exc:
        _mark_refresh_failure(app, exc.reason, exc.original)
        logger.warning(
            "Failed to populate OpenAPI cache on startup (reason=%s): %s",
            exc.reason,
            exc.original,
        )
        app.state._openapi_cache = _gateway_only_schema(app)


async def openapi_refresh_loop(app: FastAPI) -> None:
    """Background task: re-fetches upstream specs every _OPENAPI_REFRESH_INTERVAL_S."""
    while True:
        await asyncio.sleep(_OPENAPI_REFRESH_INTERVAL_S)
        _mark_refresh_attempt(app)
        try:
            schema = await _build_aggregated_schema(app)
            app.state._openapi_cache = schema
            _mark_refresh_success(app)
            logger.debug("OpenAPI cache refreshed")
        except asyncio.CancelledError:
            raise
        except OpenAPIRefreshError as exc:
            _mark_refresh_failure(app, exc.reason, exc.original)
            logger.warning(
                "OpenAPI cache refresh failed (reason=%s): %s",
                exc.reason,
                exc.original,
            )


def _gateway_only_schema(app: FastAPI) -> Dict[str, Any]:
    """Fallback schema containing only the gateway's own routes."""
    return get_openapi(
        title=app.title,
        version="1.0.0",
        routes=[r for r in app.routes if getattr(r, "include_in_schema", True)],
        description="API Gateway with aggregated service routes",
    )


def setup_custom_openapi(app: FastAPI) -> None:
    """
    Replace FastAPI's openapi() with a pure-sync cache reader.
    The cache is populated during startup and refreshed by a background task.
    """
    if not hasattr(app.state, "_openapi_cache"):
        app.state._openapi_cache = _gateway_only_schema(app)
    _ensure_refresh_metrics(app)

    def custom_openapi() -> Dict[str, Any]:
        return app.state._openapi_cache

    app.openapi = custom_openapi

