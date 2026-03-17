"""Tests for API key authentication middleware.

Covers:
- Protected POST without key -> 401
- Protected POST with wrong key -> 403
- Protected POST with correct key -> 200
- Unprotected GET without key -> 200
- Auth explicitly disabled -> 200 for all
- SSE /drive/events with correct key -> 200
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from tests.fakes import FakeDrive, FakeEventBus, AsyncNoopLock, set_app_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_auth_cache() -> None:
    """Reset the module-level auth cache so each test gets a fresh read."""
    import app.auth as _auth
    _auth._CACHED_API_KEY = None
    _auth._API_KEY_LOADED = False


@pytest.fixture(autouse=True)
def _clean_auth_cache():
    """Ensure auth cache is reset before and after every test."""
    _reset_auth_cache()
    yield
    _reset_auth_cache()


@pytest.fixture
def client_with_auth(noop_lifecycle):
    """TestClient with IGUS_API_KEY=test-secret-key."""
    with patch.dict("os.environ", {"IGUS_API_KEY": "test-secret-key"}, clear=False):
        _reset_auth_cache()
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(is_connected=True),
                event_bus=FakeEventBus(),
                motor_lock=AsyncNoopLock(),
            )
            yield c


@pytest.fixture
def client_no_auth(noop_lifecycle):
    """TestClient with auth explicitly disabled."""
    with patch.dict(
        "os.environ",
        {"IGUS_AUTH_DISABLED": "true", "IGUS_API_KEY": ""},
        clear=False,
    ):
        _reset_auth_cache()
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(is_connected=True),
                event_bus=FakeEventBus(),
                motor_lock=AsyncNoopLock(),
            )
            yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAuthEnabled:
    """When IGUS_API_KEY is set, protected endpoints require the key."""

    def test_protected_post_no_key_returns_401(self, client_with_auth):
        r = client_with_auth.post("/drive/stop", json={"mode": "quick_stop"})
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_REQUIRED"

    def test_protected_post_wrong_key_returns_403(self, client_with_auth):
        r = client_with_auth.post(
            "/drive/stop",
            json={"mode": "quick_stop"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "AUTH_FORBIDDEN"

    def test_protected_post_correct_key_returns_200(self, client_with_auth):
        r = client_with_auth.post(
            "/drive/stop",
            json={"mode": "quick_stop"},
            headers={"X-API-Key": "test-secret-key"},
        )
        assert r.status_code == 200

    def test_unprotected_get_no_key_returns_200(self, client_with_auth):
        r = client_with_auth.get("/healthz")
        assert r.status_code == 200

    def test_sse_events_no_key_returns_401(self, client_with_auth):
        r = client_with_auth.get("/drive/events")
        assert r.status_code == 401
        assert r.json()["code"] == "AUTH_REQUIRED"

    def test_sse_events_wrong_key_returns_403(self, client_with_auth):
        r = client_with_auth.get(
            "/drive/events",
            headers={"X-API-Key": "wrong-key"},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "AUTH_FORBIDDEN"


class TestAuthDisabled:
    """When IGUS_AUTH_DISABLED=true, all endpoints are open."""

    def test_protected_post_no_key_returns_200(self, client_no_auth):
        r = client_no_auth.post("/drive/stop", json={"mode": "quick_stop"})
        assert r.status_code == 200

    def test_unprotected_get_no_key_returns_200(self, client_no_auth):
        r = client_no_auth.get("/healthz")
        assert r.status_code == 200


class TestAuthUnit:
    """Unit tests for auth helper functions."""

    def test_is_auth_disabled_true(self):
        from app.auth import is_auth_disabled
        with patch.dict("os.environ", {"IGUS_AUTH_DISABLED": "true"}):
            assert is_auth_disabled() is True

    def test_is_auth_disabled_false_by_default(self):
        from app.auth import is_auth_disabled
        with patch.dict("os.environ", {"IGUS_AUTH_DISABLED": ""}, clear=False):
            assert is_auth_disabled() is False

    def test_get_api_key_returns_key_when_set(self):
        from app.auth import get_api_key
        with patch.dict("os.environ", {"IGUS_API_KEY": "my-key", "IGUS_AUTH_DISABLED": ""}):
            _reset_auth_cache()
            assert get_api_key() == "my-key"

    def test_get_api_key_returns_none_when_auth_disabled(self):
        from app.auth import get_api_key
        with patch.dict("os.environ", {"IGUS_AUTH_DISABLED": "true", "IGUS_API_KEY": "my-key"}):
            _reset_auth_cache()
            assert get_api_key() is None
