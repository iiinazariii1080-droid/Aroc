"""Authentication endpoints."""
import logging

from fastapi import APIRouter, Form, HTTPException, Security, status
from pydantic import BaseModel

from app.core.security import Role, create_api_key, require_auth, revoke_api_key
from config_storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter()


class APIKeyListItem(BaseModel):
    """API key list item (without the actual key)."""
    key_id: str
    role: str
    description: str | None = None
    created_at: str
    last_used_at: str | None = None
    revoked: bool = False
    revoked_at: str | None = None


@router.post(
    "/api-keys",
    status_code=status.HTTP_201_CREATED,
    summary="Create new API key",
    description="Create a new API key with specified role. Returns the key (show only once!).",
    response_model=dict,
    responses={
        201: {
            "description": "API key created successfully",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        403: {
            "description": "Forbidden - insufficient permissions (requires ADMIN role)",
        }
    }
)
async def create_api_key_endpoint(
    role: Role = Form(..., description="Role for the API key"),
    description: str | None = Form(None, description="Description for the key"),
    admin_role: Role = Security(require_auth(Role.ADMIN)),
) -> dict:
    """Create a new API key. Requires ADMIN role."""
    api_key, key_id = create_api_key(role, description)
    return {
        "key_id": key_id,
        "api_key": api_key,  # Show only once!
        "role": role.value,
        "description": description,
        "warning": "Save this API key now! It will not be shown again.",
    }


@router.get(
    "/api-keys",
    status_code=status.HTTP_200_OK,
    summary="List all API keys",
    description="Get list of all API keys (without the actual keys). Requires ADMIN role.",
    response_model=list[APIKeyListItem],
    responses={
        200: {
            "description": "List of API keys",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        403: {
            "description": "Forbidden - insufficient permissions (requires ADMIN role)",
        }
    }
)
async def list_api_keys(
    admin_role: Role = Security(require_auth(Role.ADMIN)),
) -> list[APIKeyListItem]:
    """
    List all API keys (without the actual keys).

    **Authentication:**
    - Requires API key with ADMIN role

    **Response includes:**
    - Key ID
    - Role
    - Description
    - Creation timestamp
    - Last used timestamp
    - Revocation status

    **Note:** The actual API keys are not returned for security reasons.

    **Example request:**
    ```bash
    curl -X GET "http://localhost:7900/api/v1/auth/api-keys" \
      -H "X-API-Key: your-admin-key"
    ```

    **Example response:**
    ```json
    [
      {
        "key_id": "abc123",
        "role": "read",
        "description": "Monitoring key",
        "created_at": "2024-01-01T12:00:00Z",
        "last_used_at": "2024-01-02T10:30:00Z",
        "revoked": false,
        "revoked_at": null
      }
    ]
    ```
    """
    storage = get_storage()
    all_config = storage.get_all()

    keys = []
    for key, _value in all_config.items():
        if key.startswith("api_key_meta:"):
            key_id = key.replace("api_key_meta:", "")
            metadata = storage.get_json(key)
            if metadata:
                keys.append(APIKeyListItem(
                    key_id=key_id,
                    role=metadata.get("role", "unknown"),
                    description=metadata.get("description"),
                    created_at=metadata.get("created_at", ""),
                    last_used_at=metadata.get("last_used_at"),
                    revoked=metadata.get("revoked", False),
                    revoked_at=metadata.get("revoked_at"),
                ))

    # Sort by created_at descending
    keys.sort(key=lambda x: x.created_at, reverse=True)
    return keys


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_200_OK,
    summary="Revoke API key",
    description="Revoke (delete) an API key by key_id. Requires ADMIN role.",
    responses={
        200: {
            "description": "API key revoked successfully",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        403: {
            "description": "Forbidden - insufficient permissions (requires ADMIN role)",
        },
        404: {
            "description": "API key not found",
        }
    }
)
async def revoke_api_key_endpoint(
    key_id: str,
    admin_role: Role = Security(require_auth(Role.ADMIN)),
) -> dict:
    """Revoke an API key. Requires ADMIN role."""
    success = revoke_api_key(key_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with key_id '{key_id}' not found"
        )
    return {"success": True, "revoked": key_id}

