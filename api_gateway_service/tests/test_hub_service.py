"""Unit tests for app.core.hub_service — auth orchestration, connectivity, SSRF."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.core.hub_service import (
    _validate_target_url,
    build_credentials_payload,
    perform_robot_auth,
    require_hub_config,
    run_connection_test,
    run_demo_ping,
    safe_json,
)


class TestRequireHubConfig:
    async def test_raises_when_no_config(self):
        with patch("app.core.hub_service.hub_state_store") as store:
            store.get_hub_config = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc_info:
                await require_hub_config()
            assert exc_info.value.status_code == 400

    async def test_returns_config_when_present(self):
        cfg = {"base_url": "http://hub.test"}
        with patch("app.core.hub_service.hub_state_store") as store:
            store.get_hub_config = AsyncMock(return_value=cfg)
            result = await require_hub_config()
            assert result["base_url"] == "http://hub.test"


class TestSafeJson:
    def test_parses_valid_json(self):
        resp = MagicMock()
        resp.json.return_value = {"key": "value"}
        assert safe_json(resp) == {"key": "value"}

    def test_returns_none_on_invalid_json(self):
        resp = MagicMock()
        resp.json.side_effect = ValueError("bad json")
        assert safe_json(resp) is None


class TestBuildCredentialsPayload:
    async def test_returns_none_when_empty(self):
        with patch("app.core.hub_service.hub_state_store") as store, \
             patch("app.core.hub_service.robot_secret_store") as secret:
            store.get_robot_credentials_meta = AsyncMock(return_value=None)
            secret.masked_api_key = AsyncMock(return_value=None)
            result = await build_credentials_payload()
            assert result is None

    async def test_includes_meta_and_masked_key(self):
        meta = {"robot_id": "bot-1", "notes": "test"}
        with patch("app.core.hub_service.hub_state_store") as store, \
             patch("app.core.hub_service.robot_secret_store") as secret:
            store.get_robot_credentials_meta = AsyncMock(return_value=meta)
            secret.masked_api_key = AsyncMock(return_value="ab***cd")
            result = await build_credentials_payload()
            assert result["robot_id"] == "bot-1"
            assert result["api_key_preview"] == "ab***cd"

    async def test_uses_provided_meta(self):
        meta = {"robot_id": "override"}
        with patch("app.core.hub_service.robot_secret_store") as secret:
            secret.masked_api_key = AsyncMock(return_value="xx***yy")
            result = await build_credentials_payload(meta)
            assert result["robot_id"] == "override"


class TestRunConnectionTest:
    async def test_successful_connection(self):
        app = MagicMock()
        resp = MagicMock()
        resp.is_success = True
        resp.status_code = 200
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=resp)
        with patch("app.core.hub_service.get_http_client", return_value=mock_client):
            result = await run_connection_test(app, "http://hub.test/ping")
        assert result["status"] == "ok"
        assert result["status_code"] == 200
        assert "latency_ms" in result

    async def test_connection_failure(self):
        app = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("app.core.hub_service.get_http_client", return_value=mock_client):
            result = await run_connection_test(app, "http://dead.host")
        assert result["status"] == "error"
        assert "error" in result


class TestRunDemoPing:
    async def test_successful_ping(self):
        app = MagicMock()
        resp = MagicMock()
        resp.is_success = True
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"pong": True}
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=resp)
        with patch("app.core.hub_service.get_http_client", return_value=mock_client):
            result = await run_demo_ping(app, "token123", "http://hub/ping")
        assert result["status"] == "ok"
        assert result["ping"]["status_code"] == 200

    async def test_ping_failure(self):
        app = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        with patch("app.core.hub_service.get_http_client", return_value=mock_client):
            result = await run_demo_ping(app, "token", "http://hub/ping")
        assert result["status"] == "error"
        assert "error" in result["ping"]


class TestPerformRobotAuth:
    async def test_auth_setup_error_returns_400(self):
        from app.core.auth_service import AuthSetupError
        app = MagicMock()
        with patch("app.core.hub_service.load_auth_context", side_effect=AuthSetupError("missing config")):
            with pytest.raises(HTTPException) as exc_info:
                await perform_robot_auth(app, None)
            assert exc_info.value.status_code == 400

    async def test_http_status_error_propagated(self):
        from app.core.auth_service import AuthContext
        app = MagicMock()
        ctx = AuthContext(base_url="http://hub", auth_url="http://hub/auth", robot_id="r1", api_key="k1", notes=None)
        mock_request = MagicMock(spec=httpx.Request)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.text = "unauthorized"
        mock_response.headers = {}
        mock_response.json.side_effect = ValueError("not json")
        mock_client = MagicMock()
        with patch("app.core.hub_service.load_auth_context", return_value=ctx), \
             patch("app.core.hub_service.get_http_client", return_value=mock_client), \
             patch("app.core.hub_service.auth_request_robot_token",
                   side_effect=httpx.HTTPStatusError("401", request=mock_request, response=mock_response)):
            with pytest.raises(HTTPException) as exc_info:
                await perform_robot_auth(app, None)
            assert exc_info.value.status_code == 401


class TestValidateTargetUrl:
    """SSRF protection: block loopback and link-local addresses."""

    def test_allows_normal_url(self):
        _validate_target_url("http://hub.example.com/ping")

    def test_allows_private_ip(self):
        # RFC1918 addresses are valid — the robot talks to LAN services
        _validate_target_url("http://192.168.1.100:8000/health")
        _validate_target_url("http://10.0.0.5:8201/api")

    def test_blocks_localhost(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_target_url("http://localhost:8080/secret")
        assert exc_info.value.status_code == 400

    def test_blocks_127_0_0_1(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_target_url("http://127.0.0.1:8080/admin")
        assert exc_info.value.status_code == 400

    def test_blocks_ipv6_loopback(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_target_url("http://[::1]:8080/")
        assert exc_info.value.status_code == 400

    def test_blocks_link_local(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_target_url("http://169.254.169.254/latest/meta-data/")
        assert exc_info.value.status_code == 400

    def test_blocks_0_0_0_0(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_target_url("http://0.0.0.0:8080/")
        assert exc_info.value.status_code == 400

    def test_blocks_empty_host(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_target_url("http:///path")
        assert exc_info.value.status_code == 400


class TestConnectionTestSsrf:
    """run_connection_test blocks SSRF before making any HTTP call."""

    async def test_rejects_loopback(self):
        app = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            await run_connection_test(app, "http://127.0.0.1:8080/admin")
        assert exc_info.value.status_code == 400
