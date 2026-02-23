"""Tests for _normalize_upstream_path and JSONFormatter."""

import json
import logging
import pytest

from app.routers.proxy_http import _normalize_upstream_path
from app.core.logging_cfg import JSONFormatter


# ── _normalize_upstream_path ─────────────────────────────

class TestNormalizeUpstreamPath:
    def test_empty_path(self):
        assert _normalize_upstream_path("xarm", "") == ""

    def test_simple_path(self):
        assert _normalize_upstream_path("xarm", "status") == "status"

    def test_leading_slash(self):
        assert _normalize_upstream_path("xarm", "/status") == "status"

    def test_strips_duplicate_prefix(self):
        """When upstream echoes the gateway prefix, it should be removed."""
        assert _normalize_upstream_path("xarm", "api/v1/xarm/status") == "status"

    def test_strips_duplicate_prefix_with_slash(self):
        assert _normalize_upstream_path("xarm", "/api/v1/xarm/status") == "status"

    def test_no_strip_for_different_service(self):
        """Prefix for a different service should NOT be stripped."""
        result = _normalize_upstream_path("xarm", "api/v1/igus/data")
        assert result == "api/v1/igus/data"

    def test_nested_path(self):
        assert _normalize_upstream_path("robot", "tasks/123/steps") == "tasks/123/steps"


# ── JSONFormatter ────────────────────────────────────────

class TestJSONFormatter:
    def _format(self, msg: str, **kwargs) -> dict:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in kwargs.items():
            setattr(record, k, v)
        line = formatter.format(record)
        return json.loads(line)

    def test_basic_fields(self):
        obj = self._format("hello world")
        assert obj["msg"] == "hello world"
        assert obj["level"] == "INFO"
        assert obj["logger"] == "test.logger"
        assert "ts" in obj
        assert obj["ts"].endswith("Z")

    def test_extra_fields_included(self):
        obj = self._format("req", request_id="abc-123", method="GET", status=200)
        assert obj["request_id"] == "abc-123"
        assert obj["method"] == "GET"
        assert obj["status"] == 200

    def test_extra_fields_absent_when_not_set(self):
        obj = self._format("no extras")
        assert "request_id" not in obj
        assert "method" not in obj

    def test_output_is_single_line(self):
        formatter = JSONFormatter()
        record = logging.LogRecord("x", logging.ERROR, "f", 1, "multi\nline\nmsg", (), None)
        line = formatter.format(record)
        assert "\n" not in line

    def test_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord("x", logging.WARNING, "f", 1, "тест unicode 🚀", (), None)
        line = formatter.format(record)
        parsed = json.loads(line)
        assert "🚀" in parsed["msg"]
