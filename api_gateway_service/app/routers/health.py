import asyncio
import time
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import SERVICE_MAP, settings
from app.core.auth_client import get_auth_client
from app.core.circuit_breaker import all_breakers
from app.core.http_proxy_utils import join_url
from app.core.openapi_agg import get_openapi_refresh_metrics
from app.core.service_metrics import get_service_error_metrics


router = APIRouter(tags=["Health"])


def _http_client_or_503(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "http_client", None)
    if client is None or client.is_closed:
        raise HTTPException(status_code=503, detail="http client not ready")
    return client


@router.get("/livez", include_in_schema=False)
async def livez() -> Dict[str, Any]:
    """
    Liveness probe: ensures the FastAPI process responds.
    """
    return {"status": "alive"}


@router.get("/healthz")
async def healthz(request: Request) -> Dict[str, Any]:
    """
    General health check for dashboards and simple monitoring tools.
    """
    client = getattr(request.app.state, "http_client", None)
    return {
        "status": "ok",
        "services_configured": len(SERVICE_MAP),
        "http_client_ready": bool(client and not client.is_closed),
        "circuit_breakers": await all_breakers(),
        "openapi_refresh": get_openapi_refresh_metrics(request.app),
        "service_error_rates": get_service_error_metrics(),
    }


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request):
    """
    Readiness probe: validates dependencies when needed.
    """
    client = _http_client_or_503(request)
    payload: Dict[str, Any] = {
        "status": "ready",
        "services_configured": len(SERVICE_MAP),
    }
    failed: List[Dict[str, Any]] = []

    if settings.readiness_check_services and SERVICE_MAP:
        probes = await _probe_services(client)
        payload["probes"] = probes
        failed.extend([p for p in probes if not p["ok"]])

    if settings.readiness_check_auth:
        auth = await _probe_auth(request)
        payload["auth"] = auth
        if not auth["ok"]:
            failed.append(auth)

    if failed:
        payload["status"] = "degraded"
        payload["failed"] = failed
        return JSONResponse(status_code=503, content=payload)

    return payload


async def _probe_services(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    async def _probe(name: str, cfg: Dict[str, str]) -> Dict[str, Any]:
        target = join_url(cfg["url"], cfg.get("prefix", ""))
        started = time.perf_counter()
        try:
            resp = await client.get(
                target,
                timeout=settings.readiness_check_timeout,
                follow_redirects=True,
            )
            return _probe_result(name, target, resp.is_success, resp.status_code, started)
        except httpx.HTTPError as exc:
            return _probe_result(name, target, False, None, started, str(exc))

    return await asyncio.gather(*(_probe(name, cfg) for name, cfg in SERVICE_MAP.items()))


def _probe_result(
    name: str,
    target: str,
    ok: bool,
    status_code: int | None,
    started: float,
    error: str | None = None,
) -> Dict[str, Any]:
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    data: Dict[str, Any] = {
        "service": name,
        "target": target,
        "status_code": status_code,
        "ok": ok,
        "latency_ms": duration_ms,
    }
    if error:
        data["error"] = error
    return data


async def _probe_auth(request: Request) -> Dict[str, Any]:
    auth_client = get_auth_client(request.app)
    if auth_client is None:
        return {
            "service": "auth",
            "ok": False,
            "error": "auth client not initialized",
        }

    details = await auth_client.describe()
    token_present = bool(details.get("token_present"))
    result: Dict[str, Any] = {
        "service": "auth",
        "ok": token_present,
        "token_present": token_present,
        "consecutive_failures": details.get("consecutive_failures"),
    }
    if details.get("last_error"):
        result["error"] = details["last_error"]
    return result

