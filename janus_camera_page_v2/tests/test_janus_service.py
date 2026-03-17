"""Tests for app/services/janus.py — Janus REST client (async httpx)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.janus import (
    JanusError,
    janus_attach_streaming,
    janus_create_session,
    _janus_destroy,
    _janus_detach,
    janus_message,
    janus_summary,
    is_stream_fresh,
)


def _mock_httpx_response(data: dict | None = None, *, janus_status: str = "success"):
    """Create a mock httpx response with the given Janus payload."""
    resp = MagicMock()
    payload = {"janus": janus_status}
    if data:
        payload.update(data)
    resp.json.return_value = payload
    return resp


@pytest.fixture(autouse=True)
def _inject_mock_client():
    """Inject a mock httpx.AsyncClient into janus._client for all tests."""
    import app.services.janus as _j
    mock_client = AsyncMock()
    original = _j._client
    _j._client = mock_client
    yield mock_client
    _j._client = original


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_session_id(self, _inject_mock_client):
        _inject_mock_client.post = AsyncMock(
            return_value=_mock_httpx_response({"data": {"id": 42}})
        )
        sid = await janus_create_session()
        assert sid == 42

    @pytest.mark.asyncio
    async def test_error_raises(self, _inject_mock_client):
        _inject_mock_client.post = AsyncMock(
            return_value=_mock_httpx_response(janus_status="error")
        )
        with pytest.raises(JanusError):
            await janus_create_session()


class TestAttachStreaming:
    @pytest.mark.asyncio
    async def test_returns_handle_id(self, _inject_mock_client):
        _inject_mock_client.post = AsyncMock(
            return_value=_mock_httpx_response({"data": {"id": 99}})
        )
        hid = await janus_attach_streaming(42)
        assert hid == 99


class TestJanusMessage:
    @pytest.mark.asyncio
    async def test_returns_plugindata(self, _inject_mock_client):
        resp = MagicMock()
        resp.json.return_value = {
            "janus": "success",
            "plugindata": {"data": {"info": {"id": 1}}},
        }
        _inject_mock_client.post = AsyncMock(return_value=resp)
        result = await janus_message(1, 2, {"request": "info", "id": 1})
        assert "data" in result


class TestJanusDetach:
    @pytest.mark.asyncio
    async def test_no_exception_on_failure(self, _inject_mock_client):
        _inject_mock_client.post = AsyncMock(side_effect=Exception("network error"))
        # Should not raise
        result = await _janus_detach(1, 2)
        assert result is False


class TestJanusDestroy:
    @pytest.mark.asyncio
    async def test_no_exception_on_failure(self, _inject_mock_client):
        _inject_mock_client.post = AsyncMock(side_effect=Exception("network error"))
        result = await _janus_destroy(1)
        assert result is False


class TestJanusSummary:
    @pytest.mark.asyncio
    @patch("app.services.janus._monitor_session")
    async def test_extracts_fields(self, mock_session):
        mock_session.info = AsyncMock(return_value={
            "data": {
                "info": {
                    "id": 1,
                    "enabled": True,
                    "media": [
                        {"age_ms": 100, "codec": "h264", "pt": 96, "fmtp": "profile-level-id=42e01f"}
                    ],
                }
            }
        })
        result = await janus_summary(1)
        assert result["mountpoint_id"] == 1
        assert result["video_active"] is True
        assert result["codec"] == "h264"

    @pytest.mark.asyncio
    @patch("app.services.janus._monitor_session")
    async def test_empty_media(self, mock_session):
        mock_session.info = AsyncMock(return_value={
            "data": {"info": {"id": 1, "enabled": False, "media": [{}]}}
        })
        result = await janus_summary(1)
        assert result["video_active"] is False


class TestIsStreamFresh:
    def test_fresh(self):
        assert is_stream_fresh(100, 5000) is True

    def test_stale(self):
        assert is_stream_fresh(99999, 5000) is False

    def test_none(self):
        assert is_stream_fresh(None, 5000) is False

    def test_non_numeric(self):
        assert is_stream_fresh("bad", 5000) is False


# ===================================================================
# Failure-mode tests (T2)
#
# Risk addressed: R06 (High) — Janus is the most failure-prone
# external dependency. All 8 original tests inject a mock that
# always succeeds. These tests validate error paths.
# ===================================================================


class TestCreateSessionFailureModes:
    """Janus create_session must propagate transport errors, not swallow them."""

    @pytest.mark.asyncio
    async def test_timeout_propagates(self, _inject_mock_client):
        """httpx.TimeoutException → not swallowed, raises to caller."""
        _inject_mock_client.post = AsyncMock(
            side_effect=httpx.TimeoutException("read timeout")
        )
        with pytest.raises(httpx.TimeoutException):
            await janus_create_session()

    @pytest.mark.asyncio
    async def test_connection_refused_propagates(self, _inject_mock_client):
        """httpx.ConnectError → not swallowed, raises to caller."""
        _inject_mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(httpx.ConnectError):
            await janus_create_session()


class TestMessageMalformedJSON:
    """Malformed JSON from Janus must not silently return None."""

    @pytest.mark.asyncio
    async def test_malformed_json_raises(self, _inject_mock_client):
        """response.json() raises JSONDecodeError → propagates."""
        import json
        bad_resp = MagicMock()
        bad_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        _inject_mock_client.post = AsyncMock(return_value=bad_resp)
        with pytest.raises(json.JSONDecodeError):
            await janus_create_session()


class TestSummaryFailureModes:
    """janus_summary must return _empty with reachable=False on transport errors."""

    @pytest.mark.asyncio
    @patch("app.services.janus._monitor_session")
    async def test_summary_returns_unreachable_on_timeout(self, mock_session):
        """Timeout from monitor session → reachable=False."""
        mock_session.info = AsyncMock(
            side_effect=httpx.TimeoutException("read timeout")
        )
        result = await janus_summary(1)
        assert result["reachable"] is False
        assert result["video_active"] is False

    @pytest.mark.asyncio
    @patch("app.services.janus._monitor_session")
    async def test_summary_schema_mismatch_returns_reachable_true(self, mock_session):
        """Unexpected response structure (no data.info.info) → reachable=True, schema_error=True."""
        # Return valid dict but with wrong nested structure
        mock_session.info = AsyncMock(return_value={
            "data": {"unexpected_key": "value"}
        })
        result = await janus_summary(1)
        assert result["reachable"] is True
        # Mount info empty → video_active should be False
        assert result["video_active"] is False


class TestPersistentSessionFailureModes:
    """Persistent session reconnect and close-prevents-reconnect behavior."""

    @pytest.mark.asyncio
    async def test_persistent_session_reconnects_on_expiry(self, _inject_mock_client):
        """Session expiry on first info() call → auto-reconnect → 2 session creates."""
        import app.services.janus as _j

        session = _j._PersistentStreamingSession()
        session._lock = asyncio.Lock()

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            body = kwargs.get("json", {})

            if body.get("janus") == "create":
                return _mock_httpx_response({"data": {"id": call_count * 100}})
            elif body.get("janus") == "attach":
                return _mock_httpx_response({"data": {"id": call_count * 10}})
            elif body.get("janus") == "message":
                # First message call fails (session expired), second succeeds
                if call_count <= 5:
                    raise Exception("Session expired")
                return _mock_httpx_response({
                    "plugindata": {"data": {"info": {"id": 1, "enabled": True, "media": []}}}
                })
            return _mock_httpx_response({})

        _inject_mock_client.post = AsyncMock(side_effect=mock_post)

        result = await session.info(1)
        # Should have reconnected — session created at least twice
        create_calls = [
            c for c in _inject_mock_client.post.call_args_list
            if c.kwargs.get("json", c.args[1] if len(c.args) > 1 else {}).get("janus") == "create"
        ]
        assert len(create_calls) >= 2, (
            f"Expected at least 2 create calls (initial + reconnect), got {len(create_calls)}"
        )

    @pytest.mark.asyncio
    async def test_persistent_session_close_prevents_reconnect(self, _inject_mock_client):
        """After close(), calling info() raises JanusError('session is closing')."""
        import app.services.janus as _j

        session = _j._PersistentStreamingSession()
        session._lock = asyncio.Lock()

        _inject_mock_client.post = AsyncMock(
            return_value=_mock_httpx_response({"data": {"id": 1}})
        )
        await session.close()

        with pytest.raises(JanusError, match="session is closing"):
            await session.info(1)

    @pytest.mark.asyncio
    async def test_attach_failure_destroys_orphan_session(self, _inject_mock_client):
        """Attach fails after create succeeds → destroy called for orphan cleanup."""
        import app.services.janus as _j

        session = _j._PersistentStreamingSession()
        session._lock = asyncio.Lock()

        call_log = []

        async def mock_post(url, **kwargs):
            body = kwargs.get("json", {})
            action = body.get("janus", "unknown")
            call_log.append(action)

            if action == "create":
                return _mock_httpx_response({"data": {"id": 999}})
            elif action == "attach":
                raise JanusError("attach failed: plugin not found")
            elif action == "destroy":
                return _mock_httpx_response({})
            return _mock_httpx_response({})

        _inject_mock_client.post = AsyncMock(side_effect=mock_post)

        with pytest.raises(JanusError, match="attach failed"):
            await session.info(1)

        assert "destroy" in call_log, (
            f"Expected 'destroy' call for orphan session cleanup, got: {call_log}"
        )
