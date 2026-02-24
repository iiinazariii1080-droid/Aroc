"""Tests for app/services/system.py — subprocess wrappers."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.services.system import run, service_restart, service_status, systemd_brief


class TestRun:
    @patch("app.services.system.subprocess.run")
    def test_success(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout="output\n", stderr="")
        result = run(["echo", "hi"])
        assert result == "output\n"

    @patch("app.services.system.subprocess.run")
    def test_failure_raises(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(RuntimeError, match="cmd failed"):
            run(["false"])

    @patch("app.services.system.subprocess.run", side_effect=subprocess.TimeoutExpired(["cmd"], 5))
    def test_timeout(self, mock_sub):
        with pytest.raises(subprocess.TimeoutExpired):
            run(["sleep", "999"])


class TestServiceRestart:
    @patch("app.services.system.run")
    def test_calls_systemctl(self, mock_run):
        service_restart()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "systemctl" in args
        assert "restart" in args


class TestServiceStatus:
    @patch("app.services.system.run", return_value="active\n")
    def test_active(self, mock_run):
        result = service_status()
        assert result["active"] is True

    @patch("app.services.system.run", return_value="inactive\n")
    def test_inactive(self, mock_run):
        result = service_status()
        assert result["active"] is False


class TestSystemdBrief:
    @patch("app.services.system.run")
    def test_parses_output(self, mock_run):
        mock_run.return_value = (
            "ActiveState=active\nActiveEnterTimestamp=Mon 2025-01-01 12:00:00 UTC\nNRestarts=3\n"
        )
        result = systemd_brief()
        assert result["active"] is True
        assert result["restarts"] == 3
        assert "2025" in result["since"]
