"""Tests for command_handlers – CommandHandlerMixin."""

from unittest.mock import MagicMock

import pytest

from command_handlers import CommandHandlerMixin


class FakeBridge(CommandHandlerMixin):
    """Minimal bridge stub satisfying BridgeProtocol for the mixin."""

    def __init__(self):
        self._publish_command_error = MagicMock()
        self._finish_command = MagicMock()
        self._submit_http = MagicMock()
        self._build_http_url = MagicMock(return_value="http://robot:8080")

    def _reset(self):
        for m in (self._publish_command_error, self._finish_command,
                  self._submit_http, self._build_http_url):
            m.reset_mock()


@pytest.fixture
def bridge():
    return FakeBridge()


# ---- navigateTo ----------------------------------------------------------

class TestNavigateCommand:
    def test_missing_target_id(self, bridge):
        bridge._handle_navigate_command("cmd-1", {})
        bridge._publish_command_error.assert_called_once()
        bridge._finish_command.assert_called_once_with("cmd-1")
        bridge._submit_http.assert_not_called()

    def test_invalid_headers(self, bridge):
        bridge._handle_navigate_command("cmd-1", {"target_id": "A", "headers": "bad"})
        bridge._publish_command_error.assert_called_once()
        bridge._finish_command.assert_called_once_with("cmd-1")

    def test_no_url(self, bridge):
        bridge._build_http_url.return_value = None
        bridge._handle_navigate_command("cmd-1", {"target_id": "A"})
        bridge._publish_command_error.assert_called_once()

    def test_success(self, bridge):
        data = {"target_id": "wp-1", "priority": "high", "metadata": {"k": "v"}}
        bridge._handle_navigate_command("cmd-1", data)
        bridge._submit_http.assert_called_once()
        call_kwargs = bridge._submit_http.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        assert body["target_id"] == "wp-1"
        assert body["priority"] == "high"

    def test_forwards_timestamp(self, bridge):
        data = {"target_id": "wp-1", "_validated_timestamp": "2024-01-01T00:00:00Z"}
        bridge._handle_navigate_command("cmd-1", data)
        body = bridge._submit_http.call_args.kwargs.get("body") or bridge._submit_http.call_args[1].get("body")
        assert body["timestamp"] == "2024-01-01T00:00:00Z"


# ---- cancel -------------------------------------------------------------

class TestCancelCommand:
    def test_missing_task_id(self, bridge):
        bridge._handle_cancel_command("cmd-2", {})
        bridge._publish_command_error.assert_called_once()
        bridge._finish_command.assert_called_once_with("cmd-2")

    def test_task_id_fallback(self, bridge):
        """Uses current_task_id when task_id missing."""
        bridge._handle_cancel_command("cmd-2", {"current_task_id": "t-99"})
        bridge._submit_http.assert_called_once()
        body = bridge._submit_http.call_args.kwargs.get("body") or bridge._submit_http.call_args[1].get("body")
        assert body["task_id"] == "t-99"

    def test_invalid_headers(self, bridge):
        bridge._handle_cancel_command("cmd-2", {"task_id": "t-1", "headers": 42})
        bridge._publish_command_error.assert_called_once()

    def test_no_url(self, bridge):
        bridge._build_http_url.return_value = None
        bridge._handle_cancel_command("cmd-2", {"task_id": "t-1"})
        bridge._publish_command_error.assert_called_once()

    def test_success(self, bridge):
        bridge._handle_cancel_command("cmd-2", {"task_id": "t-1", "reason": "abort"})
        bridge._submit_http.assert_called_once()
        body = bridge._submit_http.call_args.kwargs.get("body") or bridge._submit_http.call_args[1].get("body")
        assert body["task_id"] == "t-1"
        assert body["reason"] == "abort"


# ---- estop --------------------------------------------------------------

class TestEstopCommand:
    def test_invalid_headers(self, bridge):
        bridge._handle_estop_command("cmd-3", {"headers": "bad"})
        bridge._publish_command_error.assert_called_once()
        bridge._finish_command.assert_called_once_with("cmd-3")

    def test_no_url(self, bridge):
        bridge._build_http_url.return_value = None
        bridge._handle_estop_command("cmd-3", {})
        bridge._publish_command_error.assert_called_once()

    def test_success(self, bridge):
        bridge._handle_estop_command("cmd-3", {"reason": "emergency"})
        bridge._submit_http.assert_called_once()
        body = bridge._submit_http.call_args.kwargs.get("body") or bridge._submit_http.call_args[1].get("body")
        assert body["reason"] == "emergency"
