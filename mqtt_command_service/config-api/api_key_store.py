"""API key CRUD operations."""

import logging
import secrets

from hmac_keys import generate_api_key, hash_api_key
from rbac import Role
from shared.utils import now_iso
from storage import get_store

logger = logging.getLogger(__name__)


def get_api_key_role(api_key_hash: str) -> Role | None:
    store = get_store()
    role_str = store.get(f"api_key:{api_key_hash}")
    if role_str:
        try:
            return Role(role_str)
        except ValueError:
            return None
    return None


def create_api_key(role: Role, description: str | None = None) -> tuple[str, str]:
    api_key = generate_api_key()
    key_hash = hash_api_key(api_key)
    key_id = secrets.token_urlsafe(16)
    now = now_iso()

    def _do_create(data: dict) -> dict:
        data[f"api_key:{key_hash}"] = role.value
        data[f"api_key_meta:{key_id}"] = {
            "role": role.value,
            "description": description,
            "created_at": now,
            "key_id": key_id,
            "key_hash": key_hash,
            "revoked": False,
        }
        data[f"api_key_hash_to_id:{key_hash}"] = key_id
        return data

    get_store().update(_do_create)
    logger.info("API key created: key_id=%s, role=%s", key_id, role.value)
    return api_key, key_id


def is_api_key_revoked(key_hash: str) -> bool:
    return get_store().get(f"api_key_revoked:{key_hash}") == "true"


def revoke_api_key(key_id: str) -> bool:
    revoked = False

    def _do_revoke(data: dict) -> dict:
        nonlocal revoked
        meta = data.get(f"api_key_meta:{key_id}")
        if not meta or not isinstance(meta, dict):
            return data
        key_hash = meta.get("key_hash")
        if not key_hash:
            return data
        now = now_iso()
        data[f"api_key_revoked:{key_hash}"] = "true"
        meta["revoked"] = True
        meta["revoked_at"] = now
        data[f"api_key_meta:{key_id}"] = meta
        revoked = True
        return data

    get_store().update(_do_revoke)
    if revoked:
        logger.info("API key revoked: key_id=%s", key_id)
    return revoked
