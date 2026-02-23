"""Tests for healthcheck module."""

import json
from unittest.mock import MagicMock, patch

import pytest

import healthcheck


class TestCheckApiHealth:
    @patch("healthcheck.urllib.request.urlopen")
    def test_healthy(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps({"status": "healthy"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        healthy, msg = healthcheck.check_api_health()
        assert healthy is True
        assert "passed" in msg

    @patch("healthcheck.urllib.request.urlopen")
    def test_unhealthy_status(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps({"status": "degraded"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        healthy, msg = healthcheck.check_api_health()
        assert healthy is False
        assert "unhealthy" in msg

    @patch("healthcheck.urllib.request.urlopen")
    def test_non_200(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 503
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        healthy, _msg = healthcheck.check_api_health()
        assert healthy is False

    @patch("healthcheck.urllib.request.urlopen", side_effect=Exception("conn refused"))
    def test_connection_error(self, _):
        healthy, msg = healthcheck.check_api_health()
        assert healthy is False
        assert "error" in msg.lower() or "conn refused" in msg


class TestCheckProcessHealth:
    def test_success(self):
        healthy, _msg = healthcheck.check_process_health()
        assert healthy is True
        assert "passed" in _msg.lower()


class TestHealthcheckMain:
    @patch("healthcheck.check_api_health", return_value=(True, "ok"))
    def test_api_enabled_healthy(self, _):
        with patch.object(healthcheck, "API_ENABLED", True):
            with pytest.raises(SystemExit) as exc:
                healthcheck.main()
            assert exc.value.code == 0

    @patch("healthcheck.check_api_health", return_value=(False, "down"))
    def test_api_enabled_unhealthy(self, _):
        with patch.object(healthcheck, "API_ENABLED", True):
            with pytest.raises(SystemExit) as exc:
                healthcheck.main()
            assert exc.value.code == 1

    @patch("healthcheck.check_process_health", return_value=(True, "running"))
    def test_api_disabled(self, _):
        with patch.object(healthcheck, "API_ENABLED", False):
            with pytest.raises(SystemExit) as exc:
                healthcheck.main()
            assert exc.value.code == 0
