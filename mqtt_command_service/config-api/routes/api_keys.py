"""API key management endpoints."""

import logging

from fastapi import APIRouter, Form, HTTPException, Security, status
from pydantic import BaseModel

from security import Role, create_api_key, require_auth, revoke_api_key
from storage import get_store

logger = logging.getLogger(__name__)

router = APIRouter()


class APIKeyListItem(BaseModel):
    key_id: str
    role: str
    description: str | None = None
    created_at: str
    revoked: bool = False
    revoked_at: str | None = None


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key_endpoint(
    role: Role = Form(...),
    description: str | None = Form(None),
    admin_role: Role = Security(require_auth(Role.ADMIN)),
) -> dict:
    api_key, key_id = create_api_key(role, description)
    return {
        "key_id": key_id,
        "api_key": api_key,
        "role": role.value,
        "description": description,
        "warning": "Save this API key now! It will not be shown again.",
    }


@router.get("/api-keys", response_model=list[APIKeyListItem])
async def list_api_keys(
    admin_role: Role = Security(require_auth(Role.ADMIN)),
) -> list[APIKeyListItem]:
    store = get_store()
    all_meta = store.get_by_prefix("api_key_meta:")

    keys = []
    for key, metadata in all_meta.items():
        if not isinstance(metadata, dict):
            continue
        key_id = key.removeprefix("api_key_meta:")
        keys.append(
            APIKeyListItem(
                key_id=key_id,
                role=metadata.get("role", "unknown"),
                description=metadata.get("description"),
                created_at=metadata.get("created_at", ""),
                revoked=metadata.get("revoked", False),
                revoked_at=metadata.get("revoked_at"),
            )
        )

    keys.sort(key=lambda x: x.created_at, reverse=True)
    return keys


@router.delete("/api-keys/{key_id}")
async def revoke_api_key_endpoint(
    key_id: str,
    admin_role: Role = Security(require_auth(Role.ADMIN)),
) -> dict:
    if not revoke_api_key(key_id):
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")
    return {"success": True, "revoked": key_id}
