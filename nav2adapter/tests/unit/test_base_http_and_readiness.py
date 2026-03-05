"""Tests for BaseHttpClient — session, _make_request retry, get_raw, error extraction.
   Tests for ReadinessChecker — all state flag conditions."""
import pytest
import asyncio
import aiohttp
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from contextlib import asynccontextmanager

from services.base_http_client import BaseHttpClient
from services.readiness_checker import ReadinessChecker
from exceptions import DeviceError, DeviceConnectionError


# ── Concrete subclass for testing abstract BaseHttpClient ────────────

class _TestClient(BaseHttpClient):
    """Concrete subclass so we can instantiate the ABC."""
    pass


# ── _extract_error_message ───────────────────────────────────────────

class TestExtractErrorMessage:
    def setup_method(self):
        self.client = _TestClient(base_url="http://test", timeout_seconds=5)

    def test_detail_string(self):
        assert self.client._extract_error_message({"detail": "boom"}) == "boom"

    def test_detail_dict_with_error(self):
        assert self.client._extract_error_message({"detail": {"error": "inner"}}) == "inner"

    def test_error_key(self):
        assert self.client._extract_error_message({"error": "oops"}) == "oops"

    def test_text_key(self):
        assert self.client._extract_error_message({"text": "raw text"}) == "raw text"

    def test_message_key(self):
        assert self.client._extract_error_message({"message": "msg"}) == "msg"

    def test_none_returns_empty(self):
        assert self.client._extract_error_message(None) == "Empty response"

    def test_plain_string(self):
        assert self.client._extract_error_message("hello") == "hello"

    def test_empty_dict(self):
        assert self.client._extract_error_message({}) == "Unknown error"

    def test_detail_none(self):
        # detail exists but is None — _extract_error_message falls through to str(data)
        result = self.client._extract_error_message({"detail": None})
        assert isinstance(result, str)


# ── _create_connector ────────────────────────────────────────────────

class TestCreateConnector:
    def test_invalid_certs(self):
        client = _TestClient(base_url="http://test", allow_invalid_certs=True)
        conn = client._create_connector()
        assert isinstance(conn, aiohttp.TCPConnector)

    def test_valid_certs(self):
        client = _TestClient(base_url="http://test", allow_invalid_certs=False)
        conn = client._create_connector()
        assert isinstance(conn, aiohttp.TCPConnector)


# ── _ensure_session ──────────────────────────────────────────────────

class TestEnsureSession:
    @pytest.mark.asyncio
    async def test_creates_session(self):
        client = _TestClient(base_url="http://test")
        session = await client._ensure_session()
        assert isinstance(session, aiohttp.ClientSession)
        await client.close()

    @pytest.mark.asyncio
    async def test_reuses_session(self):
        client = _TestClient(base_url="http://test")
        s1 = await client._ensure_session()
        s2 = await client._ensure_session()
        assert s1 is s2
        await client.close()

    @pytest.mark.asyncio
    async def test_recreates_closed_session(self):
        client = _TestClient(base_url="http://test")
        s1 = await client._ensure_session()
        await client.close()
        s2 = await client._ensure_session()
        assert s1 is not s2
        await client.close()


# ── close ────────────────────────────────────────────────────────────

class TestClose:
    @pytest.mark.asyncio
    async def test_close_no_session(self):
        client = _TestClient(base_url="http://test")
        await client.close()  # should not raise

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with _TestClient(base_url="http://test") as client:
            session = await client._ensure_session()
            assert not session.closed
        # session closed after exit


# ── _make_request retry logic (mocked transport) ─────────────────────

class TestMakeRequest:
    @pytest.mark.asyncio
    async def test_success_200(self):
        client = _TestClient(base_url="http://test", max_retries=0)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True})

        @asynccontextmanager
        async def mock_request(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client._make_request("GET", "/test")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_non_dict_wrapped_in_result(self):
        client = _TestClient(base_url="http://test", max_retries=0)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=[1, 2, 3])

        @asynccontextmanager
        async def mock_request(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client._make_request("GET", "/test")
        assert result == {"result": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_4xx_non_retriable_raises_device_error(self):
        client = _TestClient(base_url="http://test", max_retries=2)
        mock_resp = MagicMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value={"error": "bad"})

        @asynccontextmanager
        async def mock_request(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(DeviceError):
                await client._make_request("GET", "/test")

    @pytest.mark.asyncio
    async def test_503_retriable_retries_then_raises(self):
        client = _TestClient(base_url="http://test", max_retries=1, retry_delay=0.01)
        call_count = 0

        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.json = AsyncMock(return_value={"error": "unavailable"})

        @asynccontextmanager
        async def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            yield mock_resp

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(DeviceConnectionError):
                await client._make_request("GET", "/test")
        assert call_count == 2  # initial + 1 retry

    @pytest.mark.asyncio
    async def test_timeout_retries_then_raises(self):
        client = _TestClient(base_url="http://test", max_retries=1, retry_delay=0.01)
        call_count = 0

        @asynccontextmanager
        async def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()
            yield  # never reached

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(DeviceConnectionError):
                await client._make_request("GET", "/test")
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_op_timeout_overrides_default(self):
        client = _TestClient(base_url="http://test", max_retries=0)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True})

        used_timeout = None

        @asynccontextmanager
        async def mock_request(*args, **kwargs):
            nonlocal used_timeout
            used_timeout = kwargs.get("timeout")
            yield mock_resp

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_session):
            await client._make_request("GET", "/test", op_timeout=42.0)
        assert used_timeout.total == 42.0

    @pytest.mark.asyncio
    async def test_max_retries_override(self):
        client = _TestClient(base_url="http://test", max_retries=5, retry_delay=0.01)
        call_count = 0

        @asynccontextmanager
        async def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()
            yield

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(DeviceConnectionError):
                await client._make_request("GET", "/test", max_retries=0)
        assert call_count == 1  # no retries with max_retries=0


# ── _read_payload ────────────────────────────────────────────────────

class TestReadPayload:
    @pytest.mark.asyncio
    async def test_json_success(self):
        client = _TestClient(base_url="http://test")
        resp = MagicMock()
        resp.json = AsyncMock(return_value={"key": "val"})
        result = await client._read_payload(resp)
        assert result == {"key": "val"}

    @pytest.mark.asyncio
    async def test_json_fails_text_fallback(self):
        client = _TestClient(base_url="http://test")
        resp = MagicMock()
        resp.json = AsyncMock(side_effect=ValueError("bad json"))
        resp.text = AsyncMock(return_value='{"fallback": true}')
        result = await client._read_payload(resp)
        assert result == {"fallback": True}

    @pytest.mark.asyncio
    async def test_all_fail_returns_no_content(self):
        client = _TestClient(base_url="http://test")
        resp = MagicMock()
        resp.json = AsyncMock(side_effect=ValueError("bad"))
        resp.text = AsyncMock(side_effect=RuntimeError("no text"))
        result = await client._read_payload(resp)
        assert result == {"error": "No content"}


# ═══════════════════════════════════════════════════════════════════════
# ReadinessChecker
# ═══════════════════════════════════════════════════════════════════════

class TestReadinessChecker:
    def test_all_ready(self):
        status = {"state_flags": {"drive_ready": True, "safety_cleared": True}}
        r = ReadinessChecker.check_readiness(status)
        assert r.ready is True
        assert r.error_detail is None

    def test_missing_state_flags(self):
        r = ReadinessChecker.check_readiness({})
        assert r.ready is False
        assert "missing_state_flags" in r.error_detail

    def test_emergency_stop(self):
        flags = {"drive_ready": True, "safety_cleared": True, "emergency_stop": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "emergency_stop" in r.error_detail

    def test_emergency_stop_reset(self):
        flags = {"drive_ready": True, "safety_cleared": True, "emergency_stop_reset_request": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "emergency_stop" in r.error_detail

    def test_fuse_blown(self):
        flags = {"drive_ready": True, "safety_cleared": True, "sfuse_blown": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "fuse_blown" in r.error_detail

    def test_safety_not_cleared(self):
        flags = {"drive_ready": True, "safety_cleared": False}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "safety_not_cleared" in r.error_detail

    def test_waiting_for_scanner(self):
        flags = {"drive_ready": True, "safety_cleared": True, "waiting_for_scanner": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "waiting_for_scanner" in r.error_detail

    def test_laser_timeout(self):
        flags = {"drive_ready": True, "safety_cleared": True, "laser_timeout": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "laser_timeout" in r.error_detail

    def test_odom_timeout(self):
        flags = {"drive_ready": True, "safety_cleared": True, "odom_timeout": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "odom_timeout" in r.error_detail

    def test_drive_manual(self):
        flags = {"drive_ready": True, "safety_cleared": True, "drive_manual": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "drive_manual" in r.error_detail

    def test_robot_paused(self):
        flags = {"drive_ready": True, "safety_cleared": True, "robot_paused": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "robot_paused" in r.error_detail

    def test_charging(self):
        flags = {"drive_ready": True, "safety_cleared": True, "charging": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "charging" in r.error_detail

    def test_charging_connector(self):
        flags = {"drive_ready": True, "safety_cleared": True, "charging_connector": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "charging" in r.error_detail

    def test_drive_not_ready(self):
        flags = {"drive_ready": False, "safety_cleared": True}
        r = ReadinessChecker.check_readiness({"state_flags": flags})
        assert r.ready is False
        assert "drive_not_ready" in r.error_detail

    def test_is_ready_shortcut(self):
        assert ReadinessChecker.is_ready({"state_flags": {"drive_ready": True, "safety_cleared": True}}) is True
        assert ReadinessChecker.is_ready({}) is False

    def test_state_flags_not_dict(self):
        r = ReadinessChecker.check_readiness({"state_flags": "invalid"})
        assert r.ready is False
        assert "missing_state_flags" in r.error_detail
