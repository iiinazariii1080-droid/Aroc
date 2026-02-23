"""Tests for HubAuthManager (auth.py)."""
import time
from unittest.mock import Mock, patch

import pytest

from auth import HubAuthManager, TokenInfo
from config import HubAuthSettings


@pytest.fixture
def settings():
    return HubAuthSettings(base_url="http://hub", robot_id="r1", api_key="k1")


@pytest.fixture
def mgr(settings):
    return HubAuthManager(settings)


class TestEnsureToken:
    def test_returns_cached_when_valid(self, mgr):
        """Cached non-expiring token is returned without HTTP call."""
        mgr._token = TokenInfo(access_token="cached", expires_at=time.time() + 3600)
        with patch.object(mgr, "_refresh_token") as mock_refresh:
            token = mgr._ensure_token()
            assert token.access_token == "cached"
            mock_refresh.assert_not_called()

    def test_refreshes_when_expiring(self, mgr):
        """Expired token triggers refresh and updates cache."""
        mgr._token = TokenInfo(access_token="old", expires_at=time.time() - 10)
        new_token = TokenInfo(access_token="new", expires_at=time.time() + 3600)
        with patch.object(mgr, "_refresh_token", return_value=new_token):
            result = mgr._ensure_token()
            assert result.access_token == "new"
            assert mgr._token.access_token == "new"

    def test_refreshes_when_no_token(self, mgr):
        """No cached token triggers refresh."""
        new_token = TokenInfo(access_token="fresh", expires_at=time.time() + 3600)
        with patch.object(mgr, "_refresh_token", return_value=new_token):
            result = mgr._ensure_token()
            assert result.access_token == "fresh"

    def test_returns_none_on_refresh_failure(self, mgr):
        """Returns None when refresh fails."""
        mgr._token = TokenInfo(access_token="old", expires_at=time.time() - 10)
        with patch.object(mgr, "_refresh_token", return_value=None):
            result = mgr._ensure_token()
            assert result is None


class TestRefreshToken:
    def test_successful_refresh(self, mgr):
        """Successful HTTP response returns TokenInfo."""
        mock_resp = Mock()
        mock_resp.json.return_value = {"access_token": "tok123", "expires_in": 3600}
        mock_resp.raise_for_status = Mock()
        with patch.object(mgr._session, "post", return_value=mock_resp):
            token = mgr._refresh_token()
            assert token is not None
            assert token.access_token == "tok123"
            assert token.expires_at > time.time()

    def test_refresh_does_not_hold_lock(self, mgr):
        """_refresh_token must not hold _lock during HTTP call."""
        mock_resp = Mock()
        mock_resp.json.return_value = {"access_token": "tok", "expires_in": 3600}
        mock_resp.raise_for_status = Mock()
        with patch.object(mgr._session, "post", return_value=mock_resp):
            mgr._refresh_token()
            assert not mgr._lock.locked()

    def test_refresh_returns_none_on_http_error(self, mgr):
        """HTTP error returns None, doesn't crash."""
        with patch.object(mgr._session, "post", side_effect=Exception("conn err")):
            token = mgr._refresh_token()
            assert token is None

    def test_refresh_returns_none_when_no_access_token(self, mgr):
        """Missing access_token in response returns None."""
        mock_resp = Mock()
        mock_resp.json.return_value = {"expires_in": 3600}
        mock_resp.raise_for_status = Mock()
        with patch.object(mgr._session, "post", return_value=mock_resp):
            token = mgr._refresh_token()
            assert token is None


class TestResolveCredentials:
    def test_returns_settings_credentials(self, mgr):
        """When settings have robot_id and api_key, return them directly."""
        result = mgr._resolve_credentials()
        assert result == ("r1", "k1")

    def test_fetches_from_hub_when_no_settings(self):
        """When settings lack credentials, fetch from hub API."""
        settings = HubAuthSettings(base_url="http://hub", robot_id=None, api_key=None)
        mgr = HubAuthManager(settings)
        mock_resp = Mock()
        mock_resp.json.return_value = {"robot_id": "r2", "api_key": "k2"}
        mock_resp.raise_for_status = Mock()
        with patch.object(mgr._session, "get", return_value=mock_resp):
            result = mgr._resolve_credentials()
            assert result == ("r2", "k2")
            assert mgr._cached_robot_id == "r2"

    def test_returns_none_on_hub_error(self):
        """Hub API error returns None."""
        settings = HubAuthSettings(base_url="http://hub", robot_id=None, api_key=None)
        mgr = HubAuthManager(settings)
        with patch.object(mgr._session, "get", side_effect=Exception("timeout")):
            result = mgr._resolve_credentials()
            assert result is None


class TestAuthHeaders:
    def test_returns_bearer_when_valid(self, mgr):
        mgr._token = TokenInfo(access_token="tok", expires_at=time.time() + 3600)
        headers = mgr.auth_headers()
        assert headers == {"Authorization": "Bearer tok"}

    def test_returns_empty_when_disabled(self):
        settings = HubAuthSettings(base_url="", robot_id=None, api_key=None)
        mgr = HubAuthManager(settings)
        assert mgr.auth_headers() == {}

    def test_returns_empty_when_refresh_fails(self, mgr):
        with patch.object(mgr, "_refresh_token", return_value=None):
            assert mgr.auth_headers() == {}


class TestClose:
    def test_close_session(self, mgr):
        with patch.object(mgr._session, "close") as mock_close:
            mgr.close()
            mock_close.assert_called_once()
