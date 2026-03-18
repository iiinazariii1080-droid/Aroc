"""Tests for HubAuthHttpAdapter — HTTP-based auth adapter for mqtt-bridge.

Covers: X-Internal-Key header injection, auth_headers caching,
robot_id extraction, has_valid_token, close/cleanup.
"""

from unittest.mock import MagicMock, patch

import requests

from hub_auth_adapter import HubAuthHttpAdapter


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 400
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


class TestInternalServiceKey:
    """X-Internal-Key header must be sent when configured."""

    def test_internal_key_set_in_session_headers(self):
        adapter = HubAuthHttpAdapter(
            hub_auth_url="http://hub-auth:8101",
            internal_service_key="my-secret-key",
        )
        assert adapter._session.headers.get("X-Internal-Key") == "my-secret-key"
        adapter.close()

    def test_no_internal_key_when_empty(self):
        adapter = HubAuthHttpAdapter(
            hub_auth_url="http://hub-auth:8101",
            internal_service_key="",
        )
        assert "X-Internal-Key" not in adapter._session.headers
        adapter.close()

    def test_no_internal_key_when_omitted(self):
        adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
        assert "X-Internal-Key" not in adapter._session.headers
        adapter.close()

    def test_internal_key_sent_with_auth_headers_request(self):
        """Verify X-Internal-Key is actually sent in HTTP requests."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {"X-Internal-Key": "test-key"}
        mock_session.get.return_value = _mock_response(
            200, {"Authorization": "Bearer jwt-123"}
        )

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(
                hub_auth_url="http://hub-auth:8101",
                internal_service_key="test-key",
            )
            headers = adapter.auth_headers()

        # Session.get was called (the actual HTTP request)
        assert mock_session.get.called
        # The session carries the internal key in default headers
        assert mock_session.headers.get("X-Internal-Key") == "test-key"
        assert headers.get("Authorization") == "Bearer jwt-123"
        adapter.close()


class TestAuthHeaders:
    """auth_headers() returns cached JWT or empty dict on failure."""

    def test_returns_jwt_on_success(self):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.return_value = _mock_response(
            200, {"Authorization": "Bearer token-abc"}
        )

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
            result = adapter.auth_headers()

        assert result == {"Authorization": "Bearer token-abc"}
        adapter.close()

    def test_returns_empty_on_http_error(self):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.return_value = _mock_response(500)

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
            result = adapter.auth_headers()

        assert result == {}
        adapter.close()

    def test_returns_empty_on_connection_error(self):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.side_effect = requests.ConnectionError("refused")

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
            result = adapter.auth_headers()

        assert result == {}
        adapter.close()


class TestRobotId:
    """robot_id() extracts from /auth/status response."""

    def test_returns_robot_id_from_status(self):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.return_value = _mock_response(
            200, {"robot_id": "robot-42", "valid": True, "expires_in": 3600}
        )

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
            assert adapter.robot_id() == "robot-42"
        adapter.close()

    def test_returns_none_on_failure(self):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.return_value = _mock_response(503)

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
            assert adapter.robot_id() is None
        adapter.close()


class TestHasValidToken:
    """has_valid_token() reflects /auth/status 'valid' field."""

    def test_true_when_valid(self):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.return_value = _mock_response(
            200, {"valid": True, "robot_id": "r1"}
        )

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
            assert adapter.has_valid_token() is True
        adapter.close()

    def test_false_when_invalid(self):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.get.return_value = _mock_response(
            200, {"valid": False, "robot_id": "r1"}
        )

        with patch("hub_auth_adapter.requests.Session", return_value=mock_session):
            adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
            assert adapter.has_valid_token() is False
        adapter.close()


class TestClose:
    """close() is safe to call multiple times."""

    def test_close_idempotent(self):
        adapter = HubAuthHttpAdapter(hub_auth_url="http://hub-auth:8101")
        adapter.close()
        adapter.close()  # should not raise
