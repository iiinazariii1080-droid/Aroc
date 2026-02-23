"""
Comprehensive tests for app.core.security module.

Covers:
- API key lifecycle (create, verify, revoke)
- Role hierarchy enforcement
- Brute force protection (lockout, clearance, persistence)
- Emergency key handling (constant-time comparison)
- Public/optional endpoint path matching
- require_auth / optional_auth dependencies
"""
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

# Ensure in-memory DB for tests
os.environ.setdefault("CONFIG_DB_PATH", ":memory:")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_brute_force():
    """Clear brute force state and HMAC key cache before each test."""
    import app.core.security as sec_module
    from app.core.security import _failed_attempts
    _failed_attempts.clear()
    sec_module._HMAC_KEY = None  # Reset cached HMAC key
    yield
    _failed_attempts.clear()
    sec_module._HMAC_KEY = None


@pytest.fixture()
def storage(tmp_path):
    """Provide a fresh ConfigStorage backed by a temp file, patched into security module."""
    from config_storage import ConfigStorage
    db_file = str(tmp_path / "test_security.db")
    s = ConfigStorage(db_file)
    with patch("app.core.security.get_storage", return_value=s):
        yield s


# ---------------------------------------------------------------------------
# hash_api_key
# ---------------------------------------------------------------------------

class TestHashApiKey:
    def test_deterministic(self):
        from app.core.security import hash_api_key
        assert hash_api_key("test") == hash_api_key("test")

    def test_different_inputs_differ(self):
        from app.core.security import hash_api_key
        assert hash_api_key("a") != hash_api_key("b")

    def test_returns_hex_string(self):
        from app.core.security import hash_api_key
        h = hash_api_key("key")
        assert len(h) == 64  # SHA-256 = 64 hex chars
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------

class TestGenerateApiKey:
    def test_length(self):
        from app.core.security import generate_api_key
        key = generate_api_key()
        assert len(key) == 43  # token_urlsafe(32) => 43 chars

    def test_unique(self):
        from app.core.security import generate_api_key
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100


# ---------------------------------------------------------------------------
# API key lifecycle: create → verify → revoke → verify-fails
# ---------------------------------------------------------------------------

class TestApiKeyLifecycle:
    def test_create_and_verify(self, storage):
        from app.core.security import Role, create_api_key, verify_api_key
        api_key, key_id = create_api_key(Role.WRITE, description="test key")

        assert len(api_key) == 43
        assert key_id  # non-empty

        role = verify_api_key(api_key)
        assert role == Role.WRITE

    def test_create_with_description(self, storage):
        from app.core.security import Role, create_api_key
        _, key_id = create_api_key(Role.READ, description="monitoring")
        meta = storage.get_json(f"api_key_meta:{key_id}")
        assert meta["description"] == "monitoring"
        assert meta["role"] == "read"
        assert meta["revoked"] is False

    def test_revoke_makes_key_invalid(self, storage):
        from app.core.security import Role, create_api_key, revoke_api_key, verify_api_key
        api_key, key_id = create_api_key(Role.ADMIN)

        assert verify_api_key(api_key) == Role.ADMIN

        result = revoke_api_key(key_id)
        assert result is True

        assert verify_api_key(api_key) is None

    def test_revoke_nonexistent_returns_false(self, storage):
        from app.core.security import revoke_api_key
        assert revoke_api_key("nonexistent-id") is False

    def test_verify_does_not_update_last_used_on_every_request(self, storage):
        """Per-request last_used_at writes were removed for performance (P1 #12)."""
        from app.core.security import Role, create_api_key, verify_api_key
        api_key, key_id = create_api_key(Role.READ)

        verify_api_key(api_key)
        meta = storage.get_json(f"api_key_meta:{key_id}")
        # last_used_at should NOT be updated on every verify call
        assert meta.get("last_used_at") is None


# ---------------------------------------------------------------------------
# verify_api_key edge cases
# ---------------------------------------------------------------------------

class TestVerifyApiKey:
    def test_none_returns_none(self):
        from app.core.security import verify_api_key
        assert verify_api_key(None) is None

    def test_empty_string_returns_none(self):
        from app.core.security import verify_api_key
        assert verify_api_key("") is None

    def test_wrong_length_returns_none(self):
        from app.core.security import verify_api_key
        assert verify_api_key("too-short") is None
        assert verify_api_key("x" * 101) is None

    def test_valid_length_but_unknown_key_returns_none(self, storage):
        from app.core.security import verify_api_key
        fake_key = "A" * 43
        assert verify_api_key(fake_key) is None

    def test_emergency_key_returns_admin(self, monkeypatch):
        # EMERGENCY_API_KEY is read at import time, so we patch the module attr
        test_key = "test-emergency-key-for-unit-tests-only-xx"
        import app.core.security as sec
        monkeypatch.setattr(sec, "EMERGENCY_API_KEY", test_key)
        assert sec.verify_api_key(test_key) == sec.Role.ADMIN

    def test_emergency_key_disabled_when_empty(self, monkeypatch):
        import app.core.security as sec
        monkeypatch.setattr(sec, "EMERGENCY_API_KEY", "")
        assert sec.verify_api_key("") is None

    def test_emergency_key_uses_constant_time_comparison(self):
        """Emergency key comparison must use hmac.compare_digest."""
        import inspect

        from app.core import security
        source = inspect.getsource(security.verify_api_key)
        assert "hmac.compare_digest" in source
        assert "api_key == EMERGENCY_API_KEY" not in source


# ---------------------------------------------------------------------------
# Brute force protection
# ---------------------------------------------------------------------------

class TestBruteForceProtection:
    def test_lockout_after_max_attempts(self):
        from app.core.security import MAX_FAILED_ATTEMPTS, verify_api_key
        ip = "10.0.0.1"

        for _ in range(MAX_FAILED_ATTEMPTS):
            verify_api_key(None, client_ip=ip)

        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None, client_ip=ip)
        assert exc_info.value.status_code == 429

    def test_lockout_not_bypassed_by_providing_any_key(self):
        """
        Regression test: previously the middleware cleared _failed_attempts
        when any X-API-Key header was present, bypassing brute force.
        Now verify_api_key manages its own state — invalid keys must still
        count as failed attempts.
        """
        from app.core.security import MAX_FAILED_ATTEMPTS, verify_api_key
        ip = "10.0.0.2"

        # Use keys with wrong length to avoid DB lookups
        for _ in range(MAX_FAILED_ATTEMPTS):
            verify_api_key("invalid-key-too-short", client_ip=ip)

        # Even with a different invalid key, should be locked out
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("another-bad-key", client_ip=ip)
        assert exc_info.value.status_code == 429

    def test_successful_auth_clears_lockout(self, storage):
        from app.core.security import (
            MAX_FAILED_ATTEMPTS,
            Role,
            _failed_attempts,
            create_api_key,
            verify_api_key,
        )
        ip = "10.0.0.3"
        api_key, _ = create_api_key(Role.READ)

        # Accumulate some failures (but less than max)
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            verify_api_key("x" * 43, client_ip=ip)

        assert len(_failed_attempts[ip]) == MAX_FAILED_ATTEMPTS - 1

        # Successful auth should clear the slate
        role = verify_api_key(api_key, client_ip=ip)
        assert role == Role.READ
        assert ip not in _failed_attempts

    def test_lockout_expires(self):
        from app.core.security import (
            LOCKOUT_DURATION,
            MAX_FAILED_ATTEMPTS,
            _failed_attempts,
            verify_api_key,
        )
        ip = "10.0.0.4"
        old_time = datetime.now(UTC) - LOCKOUT_DURATION - timedelta(seconds=1)
        _failed_attempts[ip] = [old_time] * MAX_FAILED_ATTEMPTS

        # Old attempts should be pruned — no lockout
        result = verify_api_key(None, client_ip=ip)
        assert result is None  # Still fails auth, but no 429

    def test_no_client_ip_skips_brute_force(self):
        from app.core.security import _failed_attempts, verify_api_key
        # Many failures without IP should never trigger lockout
        for _ in range(20):
            verify_api_key(None, client_ip=None)
        # No exception raised, no state stored
        assert len(_failed_attempts) == 0


# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------

class TestRoleHierarchy:
    """Verify READ < WRITE < ADMIN ordering."""

    def test_role_values(self):
        from app.core.security import Role
        hierarchy = {Role.READ: 1, Role.WRITE: 2, Role.ADMIN: 3}
        assert hierarchy[Role.READ] < hierarchy[Role.WRITE] < hierarchy[Role.ADMIN]


# ---------------------------------------------------------------------------
# Public / optional-auth endpoint matching
# ---------------------------------------------------------------------------

class TestPublicEndpointMatching:
    @pytest.mark.parametrize("path,expected", [
        ("/", True),
        ("/health", True),
        ("/health/", True),
        ("/docs", True),
        ("/docs/oauth2-redirect", True),
        ("/openapi.json", True),
        ("/redoc", True),
        # With /api/v1 prefix
        ("/api/v1/health", True),
        ("/api/v1/docs", True),
        ("/api/v1/openapi.json", True),
        ("/api/v1/redoc", True),
        # Non-public
        ("/auth/api-keys", False),
        ("/config/broker", False),
        ("/api/v1/config/broker", False),
        ("/certificates", False),
    ])
    def test_is_public(self, path, expected):
        from app.core.security import is_public_endpoint
        assert is_public_endpoint(path) is expected, f"Failed for path={path}"


class TestOptionalAuthEndpointMatching:
    def test_disabled_by_default(self):
        from app.core.security import is_optional_auth_endpoint
        from env_settings import reset_env_settings
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISABLE_AUTH_FOR_CONFIG", None)
            reset_env_settings()
            assert is_optional_auth_endpoint("/config/broker") is False

    def test_enabled_via_env(self):
        from app.core.security import is_optional_auth_endpoint
        from env_settings import reset_env_settings
        with patch.dict(os.environ, {"DISABLE_AUTH_FOR_CONFIG": "true"}):
            reset_env_settings()
            assert is_optional_auth_endpoint("/config/broker") is True
            assert is_optional_auth_endpoint("/api/v1/config/broker") is True

    def test_other_paths_not_optional(self):
        from app.core.security import is_optional_auth_endpoint
        from env_settings import reset_env_settings
        with patch.dict(os.environ, {"DISABLE_AUTH_FOR_CONFIG": "true"}):
            reset_env_settings()
            assert is_optional_auth_endpoint("/auth/api-keys") is False


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    @pytest.mark.parametrize("path,expected", [
        ("/health", "/health"),
        ("/health/", "/health"),
        ("/api/v1/health", "/health"),
        ("/api/v1/health/", "/health"),
        ("/api/v1", "/"),
        ("/api/v1/", "/"),
        ("/", "/"),
        ("/some/deep/path", "/some/deep/path"),
    ])
    def test_normalization(self, path, expected):
        from app.core.security import _normalize_path
        assert _normalize_path(path) == expected, f"Failed for path={path}"


# ---------------------------------------------------------------------------
# require_auth dependency
# ---------------------------------------------------------------------------

class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_returns_role_from_request_state(self):
        from app.core.security import Role, require_auth
        dep = require_auth(Role.READ)

        request = MagicMock()
        request.state.auth_role = Role.WRITE

        role = await dep(request=request, api_key=None)
        assert role == Role.WRITE

    @pytest.mark.asyncio
    async def test_raises_401_when_no_role(self):
        from app.core.security import Role, require_auth
        dep = require_auth(Role.READ)

        request = MagicMock()
        request.state = MagicMock(spec=[])  # no auth_role attribute

        with pytest.raises(HTTPException) as exc_info:
            await dep(request=request, api_key=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_raises_403_insufficient_role(self):
        from app.core.security import Role, require_auth
        dep = require_auth(Role.ADMIN)

        request = MagicMock()
        request.state.auth_role = Role.READ

        with pytest.raises(HTTPException) as exc_info:
            await dep(request=request, api_key=None)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_passes_all_checks(self):
        from app.core.security import Role, require_auth
        for required in [Role.READ, Role.WRITE, Role.ADMIN]:
            dep = require_auth(required)
            request = MagicMock()
            request.state.auth_role = Role.ADMIN
            role = await dep(request=request, api_key=None)
            assert role == Role.ADMIN

    @pytest.mark.asyncio
    async def test_fallback_verification_when_no_state(self, storage):
        """When middleware hasn't run, dependency falls back to verify_api_key."""
        from app.core.security import Role, create_api_key, require_auth
        api_key, _ = create_api_key(Role.WRITE)

        dep = require_auth(Role.READ)
        request = MagicMock()
        request.state = MagicMock(spec=[])  # no auth_role

        role = await dep(request=request, api_key=api_key)
        assert role == Role.WRITE


# ---------------------------------------------------------------------------
# optional_auth dependency
# ---------------------------------------------------------------------------

class TestOptionalAuth:
    @pytest.mark.asyncio
    async def test_anonymous_when_disabled_for_config(self):
        from app.core.security import Role, optional_auth
        from env_settings import reset_env_settings
        with patch.dict(os.environ, {"DISABLE_AUTH_FOR_CONFIG": "true"}):
            reset_env_settings()
            dep = optional_auth()
            request = MagicMock()
            request.state = MagicMock(spec=[])
            role = await dep(request=request, api_key=None)
            assert role == Role.ADMIN

    @pytest.mark.asyncio
    async def test_raises_401_when_no_key_and_not_disabled(self):
        from app.core.security import optional_auth
        from env_settings import reset_env_settings
        with patch.dict(os.environ, {"DISABLE_AUTH_FOR_CONFIG": "false"}):
            reset_env_settings()
            dep = optional_auth()
            request = MagicMock()
            request.state = MagicMock(spec=[])
            with pytest.raises(HTTPException) as exc_info:
                await dep(request=request, api_key=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_reads_from_request_state(self):
        from app.core.security import Role, optional_auth
        from env_settings import reset_env_settings
        with patch.dict(os.environ, {"DISABLE_AUTH_FOR_CONFIG": "false"}):
            reset_env_settings()
            dep = optional_auth()
            request = MagicMock()
            request.state.auth_role = Role.READ
            role = await dep(request=request, api_key="some-key")
            assert role == Role.READ
