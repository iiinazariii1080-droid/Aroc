"""Tests for app/services/janus.py — Janus REST client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.janus import (
    JanusError,
    janus_attach_streaming,
    janus_create_session,
    janus_destroy,
    janus_detach,
    janus_message,
    janus_summary,
)


def _ok_response(data: dict | None = None):
    resp = MagicMock()
    payload = {"janus": "success"}
    if data:
        payload["data"] = data
    resp.json.return_value = payload
    return resp


class TestCreateSession:
    @patch("app.services.janus.httpx.post", return_value=_ok_response({"id": 42}))
    def test_returns_session_id(self, mock_post):
        sid = janus_create_session()
        assert sid == 42

    @patch("app.services.janus.httpx.post")
    def test_error_raises(self, mock_post):
        mock_post.return_value.json.return_value = {"janus": "error", "error": {"reason": "fail"}}
        with pytest.raises(JanusError):
            janus_create_session()


class TestAttachStreaming:
    @patch("app.services.janus.httpx.post", return_value=_ok_response({"id": 99}))
    def test_returns_handle_id(self, mock_post):
        hid = janus_attach_streaming(42)
        assert hid == 99


class TestJanusMessage:
    @patch("app.services.janus.httpx.post")
    def test_returns_plugindata(self, mock_post):
        mock_post.return_value.json.return_value = {
            "janus": "success",
            "plugindata": {"data": {"info": {"id": 1}}},
        }
        result = janus_message(1, 2, {"request": "info", "id": 1})
        assert "data" in result


class TestJanusDetach:
    @patch("app.services.janus.httpx.post")
    def test_no_exception_on_failure(self, mock_post):
        mock_post.side_effect = Exception("network error")
        # Should not raise
        janus_detach(1, 2)


class TestJanusDestroy:
    @patch("app.services.janus.httpx.post")
    def test_no_exception_on_failure(self, mock_post):
        mock_post.side_effect = Exception("network error")
        janus_destroy(1)


class TestJanusSummary:
    @patch("app.services.janus.streaming_info")
    def test_extracts_fields(self, mock_info):
        # Real Janus response: plugindata → data → info → {mount}
        mock_info.return_value = {
            "data": {
                "info": {
                    "id": 1,
                    "enabled": True,
                    "media": [
                        {"age_ms": 100, "codec": "h264", "pt": 96, "fmtp": "profile-level-id=42e01f"}
                    ],
                }
            }
        }
        result = janus_summary(1)
        assert result["mountpoint_id"] == 1
        assert result["video_active"] is True
        assert result["codec"] == "h264"

    @patch("app.services.janus.streaming_info")
    def test_empty_media(self, mock_info):
        mock_info.return_value = {
            "data": {"info": {"id": 1, "enabled": False, "media": [{}]}}
        }
        result = janus_summary(1)
        assert result["video_active"] is False
