import hmac
import logging
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from pydantic import AnyHttpUrl, BaseModel, Field

logger = logging.getLogger(__name__)

from app.core.auth_client import get_auth_client
from app.core.config import settings
from app.core.hub_state import hub_state_store
from app.core.hub_service import (
    build_credentials_payload,
    perform_robot_auth,
    require_hub_config,
    run_connection_test,
    run_demo_ping,
    trigger_auth_refresh,
)
from app.core.secret_store import robot_secret_store
from app.core.http_proxy_utils import join_url


router = APIRouter(prefix="/api/v1/hub", tags=["Hub Config"])

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def _require_admin(key: str | None = Depends(_admin_key_header)) -> None:
    """Enforce settings.gateway_admin_key on mutating hub endpoints.

    If settings.gateway_admin_key is not configured the check is skipped (dev/local mode).
    When configured, requests without a matching X-Admin-Key header are rejected
    with 401 to prevent unauthenticated credential/config manipulation over the
    network.
    """
    if not settings.gateway_admin_key:
        return
    if not key or not hmac.compare_digest(key, settings.gateway_admin_key):
        raise HTTPException(status_code=401, detail="Missing or invalid admin key")


class HubConfigPayload(BaseModel):
    base_url: AnyHttpUrl = Field(
        ...,
        description="Base URL of AE.HUB instance the robot should talk to.",
    )
    auth_url: AnyHttpUrl | None = Field(
        None,
        description="Optional override for the authentication endpoint.",
    )
    notes: str | None = Field(
        None,
        max_length=256,
        description="Free-form comment to help operators remember why this config was set.",
    )


class RobotIdentityPayload(BaseModel):
    robot_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Unique robot identifier provided by AE.HUB.",
    )
    display_name: str | None = Field(
        None,
        max_length=128,
        description="Optional human-friendly label shown in dashboards.",
    )
    notes: str | None = Field(
        None,
        max_length=256,
        description="Operators notes for this robot identity.",
    )


class RobotCredentialsPayload(BaseModel):
    robot_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Robot identifier used in auth requests.",
    )
    api_key: str = Field(
        ...,
        min_length=4,
        max_length=256,
        description="API key issued for this robot.",
    )
    notes: str | None = Field(
        None,
        max_length=256,
        description="Operators notes for these credentials.",
    )


class ConnectionTestPayload(BaseModel):
    ping_endpoint: AnyHttpUrl | None = Field(
        None,
        description="Optional explicit endpoint to ping instead of the default health check.",
    )
    dry_run: bool = Field(
        False,
        description="If true, only validate the payload without performing network calls.",
    )


class DemoAuthPayload(BaseModel):
    credentials: RobotCredentialsPayload | None = Field(
        None,
        description="Optional override credentials. Stored credentials are used if omitted.",
    )
    ping_endpoint: AnyHttpUrl | None = Field(
        None,
        description="Override for the robot ping endpoint. Defaults to {base_url}/robot/ping.",
    )


@router.get("/config", summary="Get hub configuration")
async def get_hub_config() -> dict:
    config = await hub_state_store.get_hub_config()
    return {
        "status": "ok" if config else "missing",
        "config": config,
    }


@router.put("/config", summary="Update hub configuration", dependencies=[Depends(_require_admin)])
async def update_hub_config(payload: HubConfigPayload, request: Request) -> dict:
    record = await hub_state_store.set_hub_config(payload.model_dump(exclude_none=True))
    await trigger_auth_refresh(request.app)
    return {
        "status": "saved",
        "config": record,
    }


@router.get("/robot", summary="Get robot identity")
async def get_robot_identity() -> dict:
    robot = await hub_state_store.get_robot_identity()
    return {
        "status": "ok" if robot else "missing",
        "robot": robot,
    }


@router.put("/robot", summary="Set robot identity", dependencies=[Depends(_require_admin)])
async def update_robot_identity(payload: RobotIdentityPayload, request: Request) -> dict:
    record = await hub_state_store.set_robot_identity(payload.model_dump(exclude_none=True))
    await trigger_auth_refresh(request.app)
    return {
        "status": "saved",
        "robot": record,
    }


@router.get("/credentials", summary="Get robot credentials")
async def get_robot_credentials() -> dict:
    credentials = await build_credentials_payload()
    return {
        "status": "ok" if credentials else "missing",
        "credentials": credentials,
    }


@router.put("/credentials", summary="Update robot credentials", dependencies=[Depends(_require_admin)])
async def update_robot_credentials(payload: RobotCredentialsPayload, request: Request) -> dict:
    meta_input = payload.model_dump(exclude={"api_key"})
    old_meta = await hub_state_store.get_robot_credentials_meta()
    old_api_key = await robot_secret_store.get_api_key()
    record = await hub_state_store.set_robot_credentials_meta(meta_input)
    try:
        await robot_secret_store.set_api_key(payload.api_key)
    except Exception:
        # Rollback meta to previous state on api_key update failure
        try:
            if old_meta is not None:
                await hub_state_store.set_robot_credentials_meta(old_meta)
            else:
                await hub_state_store.clear_robot_credentials_meta()
            await robot_secret_store.set_api_key(old_api_key)
        except Exception as rollback_exc:
            logger.error("Credential rollback failed: %s", rollback_exc)
        raise
    await trigger_auth_refresh(request.app)
    credentials = await build_credentials_payload(record)
    return {
        "status": "saved",
        "credentials": credentials,
    }


@router.get("/auth/status", summary="Get AuthClient status")
async def get_auth_status(request: Request) -> dict:
    client = get_auth_client(request.app)
    if not client:
        return {"status": "disabled"}
    return {"status": "ok", "details": await client.describe()}


@router.post("/connection-test", summary="Trigger hub connectivity test", dependencies=[Depends(_require_admin)])
async def trigger_connection_test(
    request: Request,
    payload: ConnectionTestPayload | None = None,
) -> dict:
    config = await require_hub_config()

    payload_obj = payload or ConnectionTestPayload()
    target_url = str(payload_obj.ping_endpoint) if payload_obj.ping_endpoint else config["base_url"]

    if payload_obj.dry_run:
        return {
            "status": "skipped",
            "target": target_url,
            "reason": "dry_run=true",
        }

    return await run_connection_test(request.app, target_url)


@router.post("/auth", summary="Request robot JWT from hub", dependencies=[Depends(_require_admin)])
async def request_robot_token_route(
    request: Request,
    credentials_override: RobotCredentialsPayload | None = None,
) -> dict:
    override_dict = credentials_override.model_dump(exclude_none=True) if credentials_override else None
    auth_result = await perform_robot_auth(request.app, override_dict)
    return {
        "status": "ok",
        **auth_result,
    }


@router.post("/demo-auth", summary="End-to-end auth + robot ping demo", dependencies=[Depends(_require_admin)])
async def demo_robot_auth(
    request: Request,
    payload: DemoAuthPayload | None = None,
) -> dict:
    override = payload.credentials if payload else None
    override_dict = override.model_dump(exclude_none=True) if override else None
    auth_result = await perform_robot_auth(request.app, override_dict)
    token = auth_result["auth_response"]["access_token"]

    ping_endpoint = (
        payload.ping_endpoint
        if payload and payload.ping_endpoint
        else join_url(auth_result["config"]["base_url"], "robot/ping")
    )

    ping = await run_demo_ping(request.app, token, str(ping_endpoint))
    return {
        "status": ping["status"],
        "auth": auth_result,
        "ping": ping["ping"],
    }

