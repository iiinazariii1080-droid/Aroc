"""Security and authentication module."""
import hashlib
import hmac
import logging
import secrets
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from config_storage import get_storage
from env_settings import get_env_settings

logger = logging.getLogger(__name__)

# Auth disabled flag — read once from environment, immutable after import.
# Set AUTH_DISABLED=true in environment to bypass all auth (testing/development only).
AUTH_DISABLED: bool = get_env_settings().auth_disabled
if AUTH_DISABLED:
    logger.warning(
        "AUTH_DISABLED=true: all authentication is bypassed. "
        "Do NOT use this in production."
    )
# Brute force protection  (thread-safe)
_failed_attempts: dict[str, list[datetime]] = defaultdict(list)
_failed_attempts_lock = threading.Lock()
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)
_PRUNE_INTERVAL = 900  # seconds — prune stale entries every 15 min
_last_prune_time: float = 0.0

# API Key header name
API_KEY_HEADER = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

# Emergency API key — loaded from environment.  No default is shipped in
# source code; the key must be set explicitly via EMERGENCY_API_KEY env var.
# Set EMERGENCY_API_KEY="" (empty) to disable emergency access entirely.
EMERGENCY_API_KEY: str = get_env_settings().emergency_api_key


class Role(StrEnum):
    """User roles for RBAC."""
    READ = "read"      # Read-only access
    WRITE = "write"    # Read + Write access
    ADMIN = "admin"    # Full access including auth management

# Application-level HMAC secret for API-key hashing.
# Prevents rainbow-table attacks without per-key salts.
# Generated once on first run; persists in the config DB.
_HMAC_KEY: bytes | None = None
_hmac_key_lock = threading.Lock()


def _get_hmac_key() -> bytes:
    """Get or create the application-level HMAC key.

    Falls back to a deterministic key derived from HMAC_SECRET env var
    if the config database is unavailable (e.g. during testing).
    """
    global _HMAC_KEY
    if _HMAC_KEY is not None:
        return _HMAC_KEY
    with _hmac_key_lock:
        if _HMAC_KEY is not None:
            return _HMAC_KEY  # type: ignore[unreachable]  # double-checked locking
        try:
            storage = get_storage()
            stored = storage.get("__hmac_secret__")
            if stored:
                _HMAC_KEY = bytes.fromhex(stored)
            else:
                _HMAC_KEY = secrets.token_bytes(32)
                storage.set("__hmac_secret__", _HMAC_KEY.hex(), updated_by="system", reason="Auto-generated HMAC key")
        except Exception:
            # DB unavailable (e.g. tests) — derive from env or use a static fallback
            from env_settings import get_env_settings
            env_secret = get_env_settings().hmac_secret
            if env_secret:
                _HMAC_KEY = hashlib.sha256(env_secret.encode()).digest()
            else:
                # Deterministic fallback so hash_api_key remains pure
                _HMAC_KEY = hashlib.sha256(b"mqtt-command-service-default-hmac-key").digest()
                logger.warning("Using default HMAC key — set HMAC_SECRET env var or initialize DB for production")
        return _HMAC_KEY


def hash_api_key(api_key: str) -> str:
    """Hash API key using HMAC-SHA256 with application secret."""
    return hmac.new(_get_hmac_key(), api_key.encode(), hashlib.sha256).hexdigest()


def generate_api_key() -> str:
    """Generate a new secure API key."""
    return secrets.token_urlsafe(32)  # 32 bytes = 43 characters


def get_api_key_role(api_key_hash: str) -> Role | None:
    """Get role for API key hash from database."""
    try:
        storage = get_storage()
        role_str = storage.get(f"api_key:{api_key_hash}")
    except Exception:
        logger.debug("DB unavailable when checking API key role")
        return None
    if role_str:
        try:
            return Role(role_str)
        except ValueError:
            logger.warning("Invalid role stored for API key: %s", role_str)
            return None
    return None


def create_api_key(role: Role, description: str | None = None) -> tuple[str, str]:
    """
    Create a new API key.

    Returns:
        (api_key, key_id): The plain API key (show only once) and key ID
    """
    api_key = generate_api_key()
    key_hash = hash_api_key(api_key)
    key_id = secrets.token_urlsafe(16)  # Shorter ID for reference

    storage = get_storage()
    now = datetime.now(UTC).isoformat()

    # Store key hash -> role mapping
    storage.set(
        f"api_key:{key_hash}",
        role.value,
        updated_by="system",
        reason=f"API key created: {description or 'No description'}"
    )

    # Store key metadata (using key_id as reference)
    metadata = {
        "role": role.value,
        "description": description,
        "created_at": now,
        "key_id": key_id,
        "key_hash": key_hash,  # Store hash for lookup
        "revoked": False,
    }
    storage.set_json(
        f"api_key_meta:{key_id}",
        metadata,
        updated_by="system",
        reason="API key metadata"
    )

    # Add index: key_hash -> key_id for faster lookup
    storage.set(
        f"api_key_hash_to_id:{key_hash}",
        key_id,
        updated_by="system",
        reason="API key hash to ID mapping"
    )

    logger.info("API key created: key_id=%s, role=%s", key_id, role.value)
    return api_key, key_id


def is_api_key_revoked(key_hash: str) -> bool:
    """Check if API key is revoked."""
    try:
        storage = get_storage()
        revoked = storage.get(f"api_key_revoked:{key_hash}")
        return revoked == "true"
    except Exception:
        logger.debug("DB unavailable when checking revocation")
        return False


def revoke_api_key(key_id: str) -> bool:
    """
    Revoke (delete) an API key by key_id.

    Args:
        key_id: The key ID to revoke

    Returns:
        True if key was found and revoked, False otherwise
    """
    storage = get_storage()

    # Get metadata to find hash
    metadata = storage.get_json(f"api_key_meta:{key_id}")
    if not metadata:
        return False

    key_hash = metadata.get("key_hash")
    if not key_hash:
        logger.error("No key_hash in metadata for key_id %s", key_id)
        return False

    # Mark as revoked
    now = datetime.now(UTC).isoformat()
    storage.set(
        f"api_key_revoked:{key_hash}",
        "true",
        updated_by="admin",
        reason=f"API key revoked: {key_id}"
    )

    # Update metadata
    metadata["revoked"] = True
    metadata["revoked_at"] = now
    storage.set_json(
        f"api_key_meta:{key_id}",
        metadata,
        updated_by="admin",
        reason="Key revoked"
    )

    logger.info("API key revoked: key_id=%s", key_id)
    return True


def verify_api_key(api_key: str | None, client_ip: str | None = None) -> Role | None:
    """
    Verify API key and return role with brute force protection.

    Args:
        api_key: The API key to verify
        client_ip: Client IP address for brute force protection (optional)

    Returns:
        Role if valid, None otherwise

    Raises:
        HTTPException: If too many failed attempts (429)
    """
    if client_ip:
        _maybe_prune_stale_entries()
        with _failed_attempts_lock:
            attempts = _failed_attempts.get(client_ip, [])
            now = datetime.now(UTC)
            recent_attempts = [t for t in attempts if now - t < LOCKOUT_DURATION]
            _failed_attempts[client_ip] = recent_attempts

            if len(recent_attempts) >= MAX_FAILED_ATTEMPTS:
                logger.warning("IP %s is locked out due to too many failed attempts", client_ip)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed authentication attempts. Please try again later."
                )

    if not api_key:
        if client_ip:
            with _failed_attempts_lock:
                _failed_attempts[client_ip].append(datetime.now(UTC))
        return None

    # Check emergency key first (always works, ADMIN role)
    # Use constant-time comparison to prevent timing side-channel attacks
    if EMERGENCY_API_KEY and hmac.compare_digest(api_key, EMERGENCY_API_KEY):
        logger.info("Emergency API key used")
        if client_ip:
            with _failed_attempts_lock:
                _failed_attempts.pop(client_ip, None)
        return Role.ADMIN

    # Reject obviously invalid keys (too short or too long)
    if not (20 <= len(api_key) <= 100):
        logger.debug("API key length out of valid range")
        if client_ip:
            with _failed_attempts_lock:
                _failed_attempts[client_ip].append(datetime.now(UTC))
        return None

    key_hash = hash_api_key(api_key)

    # Check if key is revoked
    if is_api_key_revoked(key_hash):
        logger.warning("Attempted use of revoked API key")
        if client_ip:
            with _failed_attempts_lock:
                _failed_attempts[client_ip].append(datetime.now(UTC))
        return None

    role = get_api_key_role(key_hash)

    if role:
        # Clear failed attempts on success
        if client_ip:
            with _failed_attempts_lock:
                _failed_attempts.pop(client_ip, None)

        logger.debug("API key used: role=%s", role.value)
        return role
    else:
        # Record failed attempt
        if client_ip:
            with _failed_attempts_lock:
                _failed_attempts[client_ip].append(datetime.now(UTC))
        return None


def _maybe_prune_stale_entries() -> None:
    """Prune all IPs whose last attempt is older than LOCKOUT_DURATION.

    Runs at most once every _PRUNE_INTERVAL seconds to avoid overhead.
    """
    import time as _time
    global _last_prune_time
    now_mono = _time.monotonic()
    if now_mono - _last_prune_time < _PRUNE_INTERVAL:
        return
    _last_prune_time = now_mono
    cutoff = datetime.now(UTC) - LOCKOUT_DURATION
    with _failed_attempts_lock:
        stale_keys = [
            ip for ip, attempts in _failed_attempts.items()
            if not attempts or attempts[-1] < cutoff
        ]
        for ip in stale_keys:
            del _failed_attempts[ip]
    if stale_keys:
        logger.debug("Pruned %d stale brute-force entries", len(stale_keys))


def require_auth(required_role: Role = Role.READ) -> Any:
    """
    FastAPI dependency that enforces authentication and role-based access.

    Reads the pre-verified role from request.state.auth_role (set by
    the logging middleware). Does NOT re-call verify_api_key().

    Falls back to direct verification only if middleware hasn't run
    (e.g., in tests or non-HTTP contexts).

    Usage:
        @app.get("/endpoint")
        async def endpoint(role: Role = Security(require_auth(Role.WRITE))):
            ...
    """
    async def auth_dependency(
        request: Request,
        api_key: str | None = Security(api_key_header),
    ) -> Role:
        if AUTH_DISABLED:
            return Role.ADMIN

        # Read role from middleware (single verification point)
        role = getattr(request.state, "auth_role", None)

        # Fallback: middleware hasn't run (tests, internal calls)
        if role is None and api_key:
            role = verify_api_key(api_key, client_ip=None)

        if not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        # Check role permissions
        role_hierarchy = {
            Role.READ: 1,
            Role.WRITE: 2,
            Role.ADMIN: 3,
        }

        if role_hierarchy[role] < role_hierarchy[required_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {required_role.value}, your role: {role.value}",
            )

        return role  # type: ignore[no-any-return]  # role narrowed by guard above

    return auth_dependency


# Public endpoints that don't require authentication
_PUBLIC_PATHS = {
    "/",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# Prefixed variants are also public
_PUBLIC_PREFIXES = ("/docs", "/openapi", "/redoc")

_OPTIONAL_AUTH_PATHS = {
    "/config/broker",
}


def _normalize_path(path: str) -> str:
    """Normalize path: strip /api/v1 prefix and trailing slash."""
    stripped = path.rstrip("/") or "/"
    # Strip versioned API prefix so /api/v1/health matches /health
    if stripped.startswith("/api/v1"):
        stripped = stripped[len("/api/v1"):] or "/"
    return stripped


def is_public_endpoint(path: str) -> bool:
    """Check if endpoint is public (no auth required)."""
    normalized = _normalize_path(path)
    if normalized in _PUBLIC_PATHS:
        return True
    return any(normalized.startswith(p) for p in _PUBLIC_PREFIXES)


def is_optional_auth_endpoint(path: str) -> bool:
    """Endpoints that allow anonymous access but validate provided API keys."""
    if not get_env_settings().disable_auth_for_config:
        return False
    normalized = _normalize_path(path)
    return normalized in _OPTIONAL_AUTH_PATHS


def optional_auth() -> Any:
    """
    Dependency that allows anonymous access when API key is missing,
    but enforces validation when key is provided.

    Reads pre-verified role from request.state.auth_role when available.
    """
    async def auth_dependency(
        request: Request,
        api_key: str | None = Security(api_key_header),
    ) -> Role:
        if get_env_settings().disable_auth_for_config and not api_key:
            return Role.ADMIN

        # Read role from middleware (single verification point)
        role = getattr(request.state, "auth_role", None)

        # Fallback: middleware hasn't run
        if role is None and api_key:
            role = verify_api_key(api_key, client_ip=None)

        if not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return role  # type: ignore[no-any-return]  # role narrowed by guard above
    return auth_dependency

