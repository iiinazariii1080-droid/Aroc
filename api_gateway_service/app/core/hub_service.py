from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException

from app.core.auth_client import get_auth_client
from app.core.auth_service import AuthSetupError, load_auth_context, request_robot_token as auth_request_robot_token
from app.core.hub_state import hub_state_store
from app.core.http_client import get_http_client
from app.core.secret_store import robot_secret_store
from app.core.http_proxy_utils import join_url


async def require_hub_config() -> Dict[str, Any]:
    config = await hub_state_store.get_hub_config()
    if not config:
        raise HTTPException(status_code=400, detail="Hub configuration is not set")
    return config


def safe_json(response: httpx.Response) -> Dict[str, Any] | None:
    try:
        return response.json()
    except ValueError:
        return None


async def build_credentials_payload(meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if meta is None:
        meta = await hub_state_store.get_robot_credentials_meta()
    api_key_preview = await robot_secret_store.masked_api_key()
    if not meta and not api_key_preview:
        return None
    payload: Dict[str, Any] = dict(meta) if meta else {}
    if api_key_preview:
        payload["api_key_preview"] = api_key_preview
    return payload


async def trigger_auth_refresh(app: FastAPI) -> None:
    client = get_auth_client(app)
    if client:
        await client.force_refresh()


async def perform_robot_auth(
    app: FastAPI,
    credentials_override: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    client = get_http_client(app)
    try:
        context = await load_auth_context(credentials_override)
    except AuthSetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        payload = await auth_request_robot_token(client, context)
    except httpx.HTTPStatusError as exc:
        detail = safe_json(exc.response) or {"error": exc.response.text}
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Auth request failed: {exc!r}") from exc

    return {
        "config": {
            "base_url": context.base_url,
            "auth_url": context.auth_url,
        },
        "credentials": {
            "robot_id": context.robot_id,
            "notes": context.notes,
        },
        "auth_response": payload,
    }


async def run_connection_test(app: FastAPI, target_url: str) -> Dict[str, Any]:
    client = get_http_client(app)
    started = time.perf_counter()
    try:
        response = await client.get(target_url, timeout=5.0)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "ok" if response.is_success else "failed",
            "target": target_url,
            "status_code": response.status_code,
            "latency_ms": duration_ms,
        }
    except httpx.HTTPError as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "error",
            "target": target_url,
            "latency_ms": duration_ms,
            "error": str(exc),
        }


async def run_demo_ping(app: FastAPI, token: str, ping_endpoint: str) -> Dict[str, Any]:
    client = get_http_client(app)
    started = time.perf_counter()
    try:
        ping_resp = await client.get(
            ping_endpoint,
            timeout=5.0,
            headers={"Authorization": f"Bearer {token}"},
        )
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        body = ping_resp.json() if ping_resp.headers.get("content-type", "").startswith("application/json") else ping_resp.text
        return {
            "status": "ok" if ping_resp.is_success else "failed",
            "ping": {
                "url": ping_endpoint,
                "status_code": ping_resp.status_code,
                "latency_ms": duration_ms,
                "body": body,
            },
        }
    except httpx.HTTPError as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "error",
            "ping": {
                "url": ping_endpoint,
                "latency_ms": duration_ms,
                "error": str(exc),
            },
        }