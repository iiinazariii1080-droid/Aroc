"""Tests for hub-auth service — HubAuthManager and FastAPI endpoints.

Covers: token refresh/caching, auth header generation, token expiry detection,
credential resolution (direct + endpoint fallback), thread-safe token access,
close/cleanup, and the /auth/headers, /auth/status, /health API endpoints.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from auth_manager import HubAuthManager, TokenInfo
from shared.config_types import HubAuthSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> HubAuthSettings:
    defaults = dict(
        base_url="https://hub.example.com",
        robot_id="robot-42",
        api_key="secret-key",
        refresh_margin=60.0,
        timeout=5.0,
    )
    defaults.update(overrides)
    return HubAuthSettings(**defaults)


def _make_manager(**overrides) -> HubAuthManager:
    return HubAuthManager(_make_settings(**overrides))


def _mock_auth_response(access_token: str = "jwt-token-abc", expires_in: int = 3600):
    """Return a mock response for POST /auth."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.ok = True
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "access_token": access_token,
        "expires_in": expires_in,
    }
    return resp


def _mock_credentials_response(robot_id: str = "robot-99", api_key: str = "fetched-key"):
    """Return a mock response for GET /credentials."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.ok = True
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "robot_id": robot_id,
        "api_key": api_key,
    }
    return resp


# ===========================================================================
# HubAuthManager — is_enabled / robot_id
# ===========================================================================


class TestIsEnabled:
    def test_enabled_when_base_url_set(self) -> None:
        mgr = _make_manager(base_url="https://hub.example.com")
        assert mgr.is_enabled() is True

    def test_disabled_when_base_url_empty(self) -> None:
        mgr = _make_manager(base_url="")
        assert mgr.is_enabled() is False

    def test_robot_id_from_settings(self) -> None:
        mgr = _make_manager(robot_id="robot-42")
        assert mgr.robot_id() == "robot-42"

    def test_robot_id_none_when_not_set(self) -> None:
        mgr = _make_manager(robot_id=None)
        assert mgr.robot_id() is None


# ===========================================================================
# HubAuthManager — credential resolution
# ===========================================================================


class TestResolveCredentials:
    def test_uses_direct_settings_when_both_present(self) -> None:
        mgr = _make_manager(robot_id="direct-bot", api_key="direct-key")
        creds = mgr._resolve_credentials()
        assert creds == ("direct-bot", "direct-key")
        assert mgr.robot_id() == "direct-bot"

    def test_falls_back_to_endpoint_when_robot_id_missing(self) -> None:
        mgr = _make_manager(robot_id=None, api_key="has-key")
        with patch.object(mgr._session, "get", return_value=_mock_credentials_response("ep-bot", "ep-key")):
            creds = mgr._resolve_credentials()
        assert creds == ("ep-bot", "ep-key")
        assert mgr.robot_id() == "ep-bot"

    def test_falls_back_to_endpoint_when_api_key_missing(self) -> None:
        mgr = _make_manager(robot_id="has-id", api_key=None)
        with patch.object(mgr._session, "get", return_value=_mock_credentials_response("ep-bot", "ep-key")):
            creds = mgr._resolve_credentials()
        assert creds == ("ep-bot", "ep-key")

    def test_endpoint_called_with_correct_url(self) -> None:
        mgr = _make_manager(base_url="https://hub.test", robot_id=None, api_key=None)
        mock_get = MagicMock(return_value=_mock_credentials_response())
        with patch.object(mgr._session, "get", mock_get):
            mgr._resolve_credentials()
        mock_get.assert_called_once_with("https://hub.test/credentials", timeout=5.0)

    def test_returns_none_when_endpoint_fails(self) -> None:
        mgr = _make_manager(robot_id=None, api_key=None)
        with patch.object(mgr._session, "get", side_effect=requests.ConnectionError("refused")):
            creds = mgr._resolve_credentials()
        assert creds is None

    def test_returns_none_when_endpoint_missing_fields(self) -> None:
        mgr = _make_manager(robot_id=None, api_key=None)
        resp = MagicMock(spec=requests.Response)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"robot_id": "bot"}  # missing api_key
        with patch.object(mgr._session, "get", return_value=resp):
            creds = mgr._resolve_credentials()
        assert creds is None

    def test_returns_none_when_endpoint_has_empty_values(self) -> None:
        mgr = _make_manager(robot_id=None, api_key=None)
        resp = MagicMock(spec=requests.Response)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"robot_id": "", "api_key": "key"}
        with patch.object(mgr._session, "get", return_value=resp):
            creds = mgr._resolve_credentials()
        assert creds is None


# ===========================================================================
# HubAuthManager — token refresh
# ===========================================================================


class TestRefreshToken:
    def test_successful_refresh(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr._session, "post", return_value=_mock_auth_response("new-jwt", 7200)):
            token = mgr._refresh_token()
        assert token is not None
        assert token.access_token == "new-jwt"
        assert token.expires_at > time.time()

    def test_refresh_posts_correct_payload(self) -> None:
        mgr = _make_manager(base_url="https://hub.test", robot_id="bot-1", api_key="key-1")
        mock_post = MagicMock(return_value=_mock_auth_response())
        with patch.object(mgr._session, "post", mock_post):
            mgr._refresh_token()
        mock_post.assert_called_once_with(
            "https://hub.test/auth",
            json={"robot_id": "bot-1", "api_key": "key-1"},
            timeout=5.0,
        )

    def test_refresh_returns_none_on_http_error(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr._session, "post", side_effect=requests.HTTPError("500 Server Error")):
            token = mgr._refresh_token()
        assert token is None

    def test_refresh_returns_none_on_connection_error(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr._session, "post", side_effect=requests.ConnectionError("refused")):
            token = mgr._refresh_token()
        assert token is None

    def test_refresh_returns_none_when_access_token_missing(self) -> None:
        mgr = _make_manager()
        resp = MagicMock(spec=requests.Response)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"expires_in": 3600}  # no access_token
        with patch.object(mgr._session, "post", return_value=resp):
            token = mgr._refresh_token()
        assert token is None

    def test_refresh_returns_none_when_no_credentials(self) -> None:
        mgr = _make_manager(robot_id=None, api_key=None)
        with patch.object(mgr._session, "get", side_effect=requests.ConnectionError("no creds")):
            token = mgr._refresh_token()
        assert token is None

    def test_expires_at_uses_minimum_of_one_second(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr._session, "post", return_value=_mock_auth_response("t", 0)):
            token = mgr._refresh_token()
        assert token is not None
        # expires_at should be at least time.time() + 1.0
        assert token.expires_at >= time.time() + 0.5

    def test_defaults_expires_in_to_3600(self) -> None:
        mgr = _make_manager()
        resp = MagicMock(spec=requests.Response)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"access_token": "tok"}  # no expires_in
        before = time.time()
        with patch.object(mgr._session, "post", return_value=resp):
            token = mgr._refresh_token()
        assert token is not None
        assert token.expires_at >= before + 3600


# ===========================================================================
# HubAuthManager — token expiry detection
# ===========================================================================


class TestIsExpiring:
    def test_not_expiring_when_far_future(self) -> None:
        mgr = _make_manager(refresh_margin=60.0)
        token = TokenInfo(access_token="t", expires_at=time.time() + 3600)
        assert mgr._is_expiring(token) is False

    def test_expiring_within_margin(self) -> None:
        mgr = _make_manager(refresh_margin=60.0)
        token = TokenInfo(access_token="t", expires_at=time.time() + 30)
        assert mgr._is_expiring(token) is True

    def test_expired_in_past(self) -> None:
        mgr = _make_manager(refresh_margin=60.0)
        token = TokenInfo(access_token="t", expires_at=time.time() - 100)
        assert mgr._is_expiring(token) is True

    def test_zero_margin_only_expired_tokens(self) -> None:
        mgr = _make_manager(refresh_margin=0.0)
        future_token = TokenInfo(access_token="t", expires_at=time.time() + 5)
        past_token = TokenInfo(access_token="t", expires_at=time.time() - 1)
        assert mgr._is_expiring(future_token) is False
        assert mgr._is_expiring(past_token) is True

    def test_negative_margin_treated_as_zero(self) -> None:
        mgr = _make_manager(refresh_margin=-100.0)
        token = TokenInfo(access_token="t", expires_at=time.time() + 5)
        assert mgr._is_expiring(token) is False


# ===========================================================================
# HubAuthManager — has_valid_token
# ===========================================================================


class TestHasValidToken:
    def test_false_when_no_token(self) -> None:
        mgr = _make_manager()
        assert mgr.has_valid_token() is False

    def test_true_when_valid_token_exists(self) -> None:
        mgr = _make_manager(refresh_margin=60.0)
        mgr._token = TokenInfo(access_token="t", expires_at=time.time() + 3600)
        assert mgr.has_valid_token() is True

    def test_false_when_token_expiring_within_margin(self) -> None:
        mgr = _make_manager(refresh_margin=60.0)
        mgr._token = TokenInfo(access_token="t", expires_at=time.time() + 10)
        assert mgr.has_valid_token() is False


# ===========================================================================
# HubAuthManager — token_status
# ===========================================================================


class TestTokenStatus:
    def test_status_when_no_token(self) -> None:
        mgr = _make_manager(robot_id="bot-1")
        status = mgr.token_status()
        assert status["valid"] is False
        assert status["expires_in"] == 0
        assert status["robot_id"] == "bot-1"

    def test_status_with_valid_token(self) -> None:
        mgr = _make_manager(robot_id="bot-1")
        mgr._token = TokenInfo(access_token="t", expires_at=time.time() + 3600)
        status = mgr.token_status()
        assert status["valid"] is True
        assert status["expires_in"] > 3500
        assert status["robot_id"] == "bot-1"

    def test_status_with_expired_token(self) -> None:
        mgr = _make_manager(robot_id="bot-1")
        mgr._token = TokenInfo(access_token="t", expires_at=time.time() - 100)
        status = mgr.token_status()
        assert status["valid"] is False
        assert status["expires_in"] == 0


# ===========================================================================
# HubAuthManager — auth_headers
# ===========================================================================


class TestAuthHeaders:
    def test_returns_empty_when_disabled(self) -> None:
        mgr = _make_manager(base_url="")
        assert mgr.auth_headers() == {}

    def test_returns_bearer_token(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr._session, "post", return_value=_mock_auth_response("jwt-abc", 3600)):
            headers = mgr.auth_headers()
        assert headers == {"Authorization": "Bearer jwt-abc"}

    def test_returns_empty_when_refresh_fails(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr._session, "post", side_effect=requests.ConnectionError("fail")):
            headers = mgr.auth_headers()
        assert headers == {}

    def test_auth_degraded_true_when_token_fails(self) -> None:
        """When token refresh fails, auth_degraded is True."""
        mgr = _make_manager()
        assert mgr.auth_degraded is False
        with patch.object(mgr._session, "post", side_effect=requests.ConnectionError("fail")):
            mgr.auth_headers()
        assert mgr.auth_degraded is True

    def test_auth_degraded_recovers_on_success(self) -> None:
        """After successful token refresh, auth_degraded returns to False."""
        mgr = _make_manager()
        # First: fail
        with patch.object(mgr._session, "post", side_effect=requests.ConnectionError("fail")):
            mgr.auth_headers()
        assert mgr.auth_degraded is True
        # Then: succeed
        with patch.object(mgr._session, "post", return_value=_mock_auth_response("jwt-ok", 3600)):
            headers = mgr.auth_headers()
        assert headers == {"Authorization": "Bearer jwt-ok"}
        assert mgr.auth_degraded is False

    def test_uses_cached_token(self) -> None:
        mgr = _make_manager(refresh_margin=10.0)
        mgr._token = TokenInfo(access_token="cached-jwt", expires_at=time.time() + 3600)
        headers = mgr.auth_headers()
        assert headers == {"Authorization": "Bearer cached-jwt"}

    def test_refreshes_when_token_expiring(self) -> None:
        mgr = _make_manager(refresh_margin=60.0)
        mgr._token = TokenInfo(access_token="old-jwt", expires_at=time.time() + 10)
        with patch.object(mgr._session, "post", return_value=_mock_auth_response("new-jwt", 3600)):
            headers = mgr.auth_headers()
        assert headers == {"Authorization": "Bearer new-jwt"}


# ===========================================================================
# HubAuthManager — _ensure_token (caching / double-check locking)
# ===========================================================================


class TestEnsureToken:
    def test_returns_cached_token_without_refresh(self) -> None:
        mgr = _make_manager(refresh_margin=10.0)
        mgr._token = TokenInfo(access_token="cached", expires_at=time.time() + 3600)
        mock_post = MagicMock()
        with patch.object(mgr._session, "post", mock_post):
            token = mgr._ensure_token()
        assert token.access_token == "cached"
        mock_post.assert_not_called()

    def test_refreshes_when_no_cached_token(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr._session, "post", return_value=_mock_auth_response("fresh", 3600)):
            token = mgr._ensure_token()
        assert token.access_token == "fresh"
        assert mgr._token.access_token == "fresh"

    def test_refreshes_when_cached_token_expiring(self) -> None:
        mgr = _make_manager(refresh_margin=60.0)
        mgr._token = TokenInfo(access_token="stale", expires_at=time.time() + 5)
        with patch.object(mgr._session, "post", return_value=_mock_auth_response("renewed", 3600)):
            token = mgr._ensure_token()
        assert token.access_token == "renewed"


# ===========================================================================
# HubAuthManager — thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_auth_headers_calls(self) -> None:
        """Multiple threads requesting auth_headers should not corrupt state."""
        mgr = _make_manager(refresh_margin=10.0)
        call_count = 0
        call_lock = threading.Lock()

        def mock_post(*args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
            # Simulate small delay to increase contention window
            time.sleep(0.01)
            return _mock_auth_response("thread-jwt", 3600)

        results = []
        errors = []

        def worker():
            try:
                headers = mgr.auth_headers()
                results.append(headers)
            except Exception as exc:
                errors.append(exc)

        with patch.object(mgr._session, "post", side_effect=mock_post):
            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert not errors, f"Threads raised exceptions: {errors}"
        assert len(results) == 10
        for h in results:
            assert h == {"Authorization": "Bearer thread-jwt"}

    def test_concurrent_robot_id_reads(self) -> None:
        """robot_id() should be thread-safe even during credential resolution."""
        mgr = _make_manager(robot_id="initial-bot")
        results = []

        def reader():
            for _ in range(50):
                rid = mgr.robot_id()
                results.append(rid)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 250
        assert all(r == "initial-bot" for r in results)


# ===========================================================================
# HubAuthManager — close / cleanup
# ===========================================================================


class TestClose:
    def test_close_closes_session(self) -> None:
        mgr = _make_manager()
        mock_close = MagicMock()
        mgr._session.close = mock_close
        mgr.close()
        mock_close.assert_called_once()

    def test_close_suppresses_exceptions(self) -> None:
        mgr = _make_manager()
        mgr._session.close = MagicMock(side_effect=RuntimeError("boom"))
        # Should not raise
        mgr.close()

    def test_double_close_is_safe(self) -> None:
        mgr = _make_manager()
        mgr.close()
        mgr.close()


# ===========================================================================
# FastAPI endpoints — /auth/headers, /auth/status, /health
# ===========================================================================


class TestFastAPIEndpoints:
    """Tests for the hub-auth FastAPI application endpoints.

    The module-level _auth_manager global in main.py is patched per-test
    to avoid real HTTP calls and to isolate endpoint behaviour.
    """

    @pytest.fixture(autouse=True)
    def _setup_client(self, monkeypatch):
        """Create a TestClient with a mocked auth manager for each test."""
        import importlib
        import os
        import sys

        from fastapi.testclient import TestClient

        # Import hub-auth/main.py explicitly to avoid collision with mqtt-bridge/main.py
        hub_auth_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hub-auth")
        spec = importlib.util.spec_from_file_location("hub_auth_main", os.path.join(hub_auth_dir, "main.py"))
        hub_auth_main = importlib.util.module_from_spec(spec)
        # Temporarily ensure hub-auth dir is first in path for its local imports
        old_path = sys.path[:]
        sys.path.insert(0, hub_auth_dir)
        try:
            spec.loader.exec_module(hub_auth_main)
        finally:
            sys.path[:] = old_path

        self._hub_auth_main = hub_auth_main

        self.mock_mgr = MagicMock(spec=HubAuthManager)
        # Default: enabled with a valid token
        self.mock_mgr.is_enabled.return_value = True
        self.mock_mgr.has_valid_token.return_value = True
        self.mock_mgr.auth_headers.return_value = {"Authorization": "Bearer test-jwt"}
        self.mock_mgr.token_status.return_value = {
            "valid": True,
            "expires_in": 3500.0,
            "robot_id": "robot-42",
        }
        self.mock_mgr.robot_id.return_value = "robot-42"

        # Patch the factory so endpoints use our mock
        monkeypatch.setattr(hub_auth_main, "_get_auth_manager", lambda: self.mock_mgr)

        self.client = TestClient(hub_auth_main.app, raise_server_exceptions=True)

    # -- /auth/headers -------------------------------------------------------

    def test_get_auth_headers_returns_bearer(self) -> None:
        resp = self.client.get("/auth/headers")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"Authorization": "Bearer test-jwt"}

    def test_get_auth_headers_empty_when_disabled(self) -> None:
        self.mock_mgr.is_enabled.return_value = False
        resp = self.client.get("/auth/headers")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_get_auth_headers_empty_when_no_token(self) -> None:
        self.mock_mgr.auth_headers.return_value = {}
        resp = self.client.get("/auth/headers")
        assert resp.status_code == 200
        assert resp.json() == {}

    # -- /auth/status --------------------------------------------------------

    def test_get_auth_status_enabled_valid(self) -> None:
        resp = self.client.get("/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["valid"] is True
        assert data["robot_id"] == "robot-42"
        assert data["expires_in"] > 0

    def test_get_auth_status_disabled(self) -> None:
        self.mock_mgr.is_enabled.return_value = False
        self.mock_mgr.token_status.return_value = {
            "valid": False,
            "expires_in": 0,
            "robot_id": None,
        }
        resp = self.client.get("/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["valid"] is False

    def test_get_auth_status_expired_token(self) -> None:
        self.mock_mgr.token_status.return_value = {
            "valid": False,
            "expires_in": 0,
            "robot_id": "robot-42",
        }
        resp = self.client.get("/auth/status")
        data = resp.json()
        assert data["enabled"] is True
        assert data["valid"] is False
        assert data["expires_in"] == 0

    # -- /health -------------------------------------------------------------

    def test_health_check_healthy(self) -> None:
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["enabled"] is True
        assert data["has_valid_token"] is True
        assert "timestamp" in data

    def test_health_check_no_valid_token(self) -> None:
        self.mock_mgr.has_valid_token.return_value = False
        resp = self.client.get("/health")
        data = resp.json()
        # When enabled but no valid token, status is "degraded" with 503
        assert resp.status_code == 503
        assert data["status"] == "degraded"
        assert data["has_valid_token"] is False

    def test_health_check_disabled(self) -> None:
        self.mock_mgr.is_enabled.return_value = False
        self.mock_mgr.has_valid_token.return_value = False
        resp = self.client.get("/health")
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["enabled"] is False

    # -- / (root) ------------------------------------------------------------

    def test_root_endpoint(self) -> None:
        resp = self.client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "hub-auth"
        assert "endpoints" in data
