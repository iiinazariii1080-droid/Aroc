"""Phase 6 — Canary output-schema contract tests.

Validates that ``scripts/browser_canary.py`` produces a result dict
with the expected keys and types, so downstream consumers
(dashboards, RELEASE_GATE checks) can rely on the schema.

These tests do NOT launch a browser — they call the probe functions
with mocked HTTP and verify the output shape.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Import canary module from scripts/ (not on default sys.path) ─────
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import browser_canary

# ===================================================================
# Browser-mode schema
# ===================================================================

# Canonical keys that browser_canary.run_canary() must return
BROWSER_REQUIRED_KEYS = {"url", "pass", "duration_s"}
BROWSER_METRIC_KEYS = {
    "ice_connect_ms",
    "ttff_ms",
    "frames_decoded",
    "packets_lost",
    "jitter",
    "video_width",
    "video_height",
}


class TestBrowserCanarySchema:
    """Verify run_canary() returns well-shaped dict even on failure."""

    def test_error_result_has_required_keys(self):
        """If Playwright is missing, result must still have url + pass."""
        with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
            # Force re-import to hit ImportError path
            result = browser_canary.run_canary("http://fake:8900/", timeout_s=1)
        for key in BROWSER_REQUIRED_KEYS:
            assert key in result, f"Missing key: {key}"
        assert result["pass"] is False

    def test_pass_is_boolean(self):
        """The 'pass' field must always be a bool."""
        result = browser_canary.run_canary("http://fake:8900/", timeout_s=1)
        assert isinstance(result["pass"], bool)


# ===================================================================
# HTTP-mode schema
# ===================================================================

HTTP_REQUIRED_KEYS = {"url", "pass", "duration_s", "checks"}
HTTP_CHECK_NAMES = {"healthz", "health_stream", "client_config", "metrics"}


class TestHttpProbeSchema:
    """Verify run_http_probe() returns well-shaped dict."""

    @pytest.fixture
    def _mock_requests(self):
        """Mock the requests library used by the HTTP probe."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "mode": "nominal",
            "janus_reachable": True,
            "stream_active": True,
            "stream_usable": True,
            "checks": {"rtp_ingest": {}, "client_telemetry": {}},
            "iceServers": [{"urls": "turn:example.com:3478"}],
        }
        mock_resp.text = "camstack_healthz_total 42"

        with patch.dict("sys.modules", {"requests": MagicMock()}):
            import requests as req_mock
            req_mock.get = MagicMock(return_value=mock_resp)
            yield req_mock

    def test_required_keys(self, _mock_requests):
        result = browser_canary.run_http_probe("http://fake:8900", timeout_s=1)
        for key in HTTP_REQUIRED_KEYS:
            assert key in result, f"Missing key: {key}"

    def test_checks_dict_keys(self, _mock_requests):
        result = browser_canary.run_http_probe("http://fake:8900", timeout_s=1)
        for name in HTTP_CHECK_NAMES:
            assert name in result["checks"], f"Missing check: {name}"

    def test_each_check_has_ok_field(self, _mock_requests):
        result = browser_canary.run_http_probe("http://fake:8900", timeout_s=1)
        for name, check in result["checks"].items():
            assert "ok" in check, f"Check {name!r} missing 'ok' field"

    def test_pass_is_boolean(self, _mock_requests):
        result = browser_canary.run_http_probe("http://fake:8900", timeout_s=1)
        assert isinstance(result["pass"], bool)

    def test_duration_is_numeric(self, _mock_requests):
        result = browser_canary.run_http_probe("http://fake:8900", timeout_s=1)
        assert isinstance(result["duration_s"], (int, float))

    def test_all_fail_result(self):
        """When all endpoints are unreachable, pass must be False."""
        mock_req = MagicMock()
        mock_req.get = MagicMock(side_effect=ConnectionError("refused"))
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = browser_canary.run_http_probe("http://dead:8900", timeout_s=1)
        assert result["pass"] is False


# ===================================================================
# Soak runner schema
# ===================================================================

class TestSoakRunnerParseDuration:
    """Validate the soak runner's duration parser."""

    def test_hours(self):
        from tests.soak_runner import _parse_duration
        assert _parse_duration("8h") == 8 * 3600

    def test_minutes(self):
        from tests.soak_runner import _parse_duration
        assert _parse_duration("30m") == 1800

    def test_seconds(self):
        from tests.soak_runner import _parse_duration
        assert _parse_duration("600s") == 600

    def test_bare_number(self):
        from tests.soak_runner import _parse_duration
        assert _parse_duration("120") == 120

    def test_invalid_raises(self):
        from tests.soak_runner import _parse_duration
        with pytest.raises(ValueError):
            _parse_duration("abc")
