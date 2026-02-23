from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.core.hub_state import hub_state_store
from app.core.secret_store import robot_secret_store
from app.core.http_proxy_utils import join_url


class AuthSetupError(RuntimeError):
    pass


@dataclass
class AuthContext:
    base_url: str
    auth_url: str
    robot_id: str
    api_key: str
    notes: Optional[str]


async def load_auth_context(
    credentials_override: Optional[Dict[str, Any]] = None,
) -> AuthContext:
    config = await hub_state_store.get_hub_config()
    if not config or not config.get("base_url"):
        raise AuthSetupError("Hub configuration is not set")

    meta = await hub_state_store.get_robot_credentials_meta()
    robot_id = None
    notes = None
    if credentials_override and credentials_override.get("robot_id"):
        robot_id = credentials_override["robot_id"]
        notes = credentials_override.get("notes")
    elif meta:
        robot_id = meta.get("robot_id")
        notes = meta.get("notes")

    if not robot_id:
        raise AuthSetupError("Robot ID is not configured")

    if credentials_override and credentials_override.get("api_key"):
        api_key = credentials_override["api_key"]
    else:
        api_key = await robot_secret_store.get_api_key()

    if not api_key:
        raise AuthSetupError("Robot API key is not configured")

    auth_url = config.get("auth_url") or join_url(config["base_url"], "auth/robot")
    auth_url = auth_url.strip()
    if not auth_url.startswith(("http://", "https://")):
        raise AuthSetupError("Auth URL must be absolute http(s) URL")

    return AuthContext(
        base_url=config["base_url"],
        auth_url=auth_url,
        robot_id=robot_id,
        api_key=api_key,
        notes=notes,
    )


async def request_robot_token(
    client: httpx.AsyncClient,
    context: AuthContext,
) -> Dict[str, Any]:
    response = await client.post(
        context.auth_url,
        json={"robot_id": context.robot_id, "api_key": context.api_key},
        timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0),
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Auth endpoint returned {response.status_code}",
            request=response.request,
            response=response,
        )
    data = response.json()
    if "access_token" not in data:
        raise RuntimeError("Auth response missing access_token")
    return data

