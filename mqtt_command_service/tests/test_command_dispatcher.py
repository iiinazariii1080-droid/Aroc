"""Tests for CommandDispatcher — standalone command processing."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from command_dispatcher import _ESTOP_MAX_RETRIES, COMMAND_SPECS, CommandDispatcher


def _make_dispatcher(**overrides):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    mock_response.text = ""
    mock_session.post.return_value = mock_response
    defaults = dict(
        build_http_url=MagicMock(return_value="http://robot:8110/api/v1/robot/tasks/navigate"),
        submit_http=MagicMock(),
        publish_command_error=MagicMock(),
        finish_command=MagicMock(),
        get_http_session=MagicMock(return_value=mock_session),
        auth_headers=MagicMock(return_value={}),
        send_response=MagicMock(),
        store_command_history=MagicMock(),
        publish_navigation_status=MagicMock(),
    )
    defaults.update(overrides)
    deps_ns = SimpleNamespace(**defaults)
    all_deps = dict(defaults)
    all_deps["_mock_session"] = mock_session
    all_deps["_mock_response"] = mock_response
    return CommandDispatcher(deps=deps_ns), all_deps


class TestCommandSpec:
    def test_extract_required_success(self):
        spec = COMMAND_SPECS["navigateto"]
        values, error = spec.extract_required({"target_id": "A"})
        assert error is None
        assert values == {"target_id": "A"}

    def test_extract_required_missing(self):
        spec = COMMAND_SPECS["navigateto"]
        _values, error = spec.extract_required({})
        assert error is not None
        assert "target_id" in error

    def test_extract_with_alias(self):
        spec = COMMAND_SPECS["cancel"]
        values, error = spec.extract_required({"current_task_id": "t-1"})
        assert error is None
        assert values == {"task_id": "t-1"}

    def test_estop_no_required_fields(self):
        spec = COMMAND_SPECS["estop"]
        values, error = spec.extract_required({})
        assert error is None
        assert values == {}

    def test_position_spec_extract_required(self):
        spec = COMMAND_SPECS["position"]
        values, error = spec.extract_required({"x": 5.2, "y": 3.1, "theta": 0.785})
        assert error is None
        assert values == {"x": 5.2, "y": 3.1, "theta": 0.785}

    def test_position_spec_missing_field(self):
        spec = COMMAND_SPECS["position"]
        _values, error = spec.extract_required({"x": 1.0, "y": 2.0})
        assert error is not None
        assert "theta" in error


class TestNavigateDispatch:
    def test_navigate_calls_submit_http(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("navigateto", "cmd-1", {"target_id": "station-A"})
        deps["submit_http"].assert_called_once()
        call_kwargs = deps["submit_http"].call_args.kwargs
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["service"] == "robot"
        assert call_kwargs["context"].command_name == "navigateTo"
        assert call_kwargs["context"].target_id == "station-A"

    def test_navigate_missing_target_id(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("navigateto", "cmd-1", {})
        deps["publish_command_error"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-1")
        deps["submit_http"].assert_not_called()

    def test_navigate_includes_timestamp(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("navigateto", "cmd-1", {"target_id": "A"}, validated_timestamp="2024-01-01T00:00:00Z")
        body = deps["submit_http"].call_args.kwargs["body"]
        assert body["timestamp"] == "2024-01-01T00:00:00Z"

    def test_navigate_body_includes_priority_and_metadata(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch(
            "navigateto",
            "cmd-1",
            {
                "target_id": "A",
                "priority": "high",
                "metadata": {"floor": 3},
            },
        )
        body = deps["submit_http"].call_args.kwargs["body"]
        assert body["priority"] == "high"
        assert body["metadata"] == {"floor": 3}


class TestPositionDispatch:
    def test_position_calls_submit_http_to_symovo(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("position", "cmd-p1", {"x": 5.2, "y": 3.1, "theta": 0.785})
        deps["submit_http"].assert_called_once()
        call_kwargs = deps["submit_http"].call_args.kwargs
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["service"] == "symovo"
        assert call_kwargs["context"].command_name == "position"

    def test_position_missing_coordinates(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("position", "cmd-p2", {"x": 1.0})
        deps["publish_command_error"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-p2")
        deps["submit_http"].assert_not_called()

    def test_position_body_contains_coordinates(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("position", "cmd-p3", {"x": 5.2, "y": 3.1, "theta": 0.785})
        body = deps["submit_http"].call_args.kwargs["body"]
        assert body["x"] == 5.2
        assert body["y"] == 3.1
        assert body["theta"] == 0.785
        assert body["command_id"] == "cmd-p3"

    def test_position_service_not_configured(self):
        dispatcher, deps = _make_dispatcher(
            build_http_url=MagicMock(return_value=None),
        )
        dispatcher.dispatch("position", "cmd-p4", {"x": 1.0, "y": 2.0, "theta": 0.0})
        deps["publish_command_error"].assert_called_once()
        assert "symovo" in deps["publish_command_error"].call_args[0][2]
        deps["finish_command"].assert_called_once_with("cmd-p4")


class TestCancelDispatch:
    def test_cancel_calls_submit_http(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("cancel", "cmd-2", {"task_id": "t-1"})
        deps["submit_http"].assert_called_once()

    def test_cancel_with_alias(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("cancel", "cmd-2", {"current_task_id": "t-1"})
        deps["submit_http"].assert_called_once()
        body = deps["submit_http"].call_args.kwargs["body"]
        assert body["task_id"] == "t-1"

    def test_cancel_missing_task_id(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch("cancel", "cmd-2", {})
        deps["publish_command_error"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-2")


class TestEstopDispatch:
    @patch("command_dispatcher.requests.Session")
    def test_estop_sends_ack_without_second_http_call(self, MockSession):
        """E-stop must NOT call _submit_http (which would make a second HTTP request).
        Instead it should build the ack directly and call send_response."""
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_session.post.return_value = mock_response
        dispatcher, deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-3", {})
        dispatcher.join_estop_threads(timeout=5)
        # submit_http should NOT be called — that was the double-dispatch bug
        deps["submit_http"].assert_not_called()
        # Instead, send_response and store_command_history should be called
        deps["send_response"].assert_called_once()
        deps["store_command_history"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-3")
        # Verify the session.post was called exactly once (the synchronous retry path)
        mock_session.post.assert_called_once()

    @patch("command_dispatcher.requests.Session")
    def test_estop_ack_contains_correct_fields(self, MockSession):
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_session.post.return_value = mock_response
        dispatcher, deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-3", {})
        dispatcher.join_estop_threads(timeout=5)
        ack_payload = deps["send_response"].call_args[0][1]
        assert ack_payload["success"] is True
        assert ack_payload["status_code"] == 200
        assert ack_payload["command_id"] == "cmd-3"
        assert ack_payload["service"] == "robot"

    @patch("command_dispatcher.requests.Session")
    def test_estop_includes_reason_in_body(self, MockSession):
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_session.post.return_value = mock_response
        dispatcher, _deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-3", {"reason": "emergency"})
        dispatcher.join_estop_threads(timeout=5)
        # Verify the HTTP request body contains the reason
        call_kwargs = mock_session.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["reason"] == "emergency"

    @patch("command_dispatcher.requests.Session")
    def test_estop_publishes_navigation_status(self, MockSession):
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_session.post.return_value = mock_response
        dispatcher, deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-3", {})
        dispatcher.join_estop_threads(timeout=5)
        deps["publish_navigation_status"].assert_called_once()
        call_kwargs = deps["publish_navigation_status"].call_args.kwargs
        assert call_kwargs["state"] == "acknowledged"
        assert call_kwargs["success"] is True

    @patch("command_dispatcher.requests.Session")
    def test_estop_retries_on_failure(self, MockSession):
        mock_session = MockSession.return_value
        mock_session.post.side_effect = [
            requests.ConnectionError("refused"),
            MagicMock(ok=True, status_code=200, json=MagicMock(return_value={}), text=""),
        ]
        dispatcher, deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-3", {})
        dispatcher.join_estop_threads(timeout=5)
        assert mock_session.post.call_count == 2
        deps["send_response"].assert_called_once()

    @patch("command_dispatcher.requests.Session")
    def test_estop_all_retries_exhausted(self, MockSession):
        mock_session = MockSession.return_value
        mock_session.post.side_effect = requests.ConnectionError("refused")
        dispatcher, deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-3", {})
        dispatcher.join_estop_threads(timeout=30)
        assert mock_session.post.call_count == _ESTOP_MAX_RETRIES
        deps["publish_command_error"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-3")


class TestDispatchErrors:
    def test_no_url_configured(self):
        dispatcher, deps = _make_dispatcher(
            build_http_url=MagicMock(return_value=None),
        )
        dispatcher.dispatch("navigateto", "cmd-1", {"target_id": "A"})
        deps["publish_command_error"].assert_called_once()
        assert "not configured" in deps["publish_command_error"].call_args[0][2]
        deps["finish_command"].assert_called_once_with("cmd-1")

    def test_invalid_headers_rejected(self):
        dispatcher, deps = _make_dispatcher()
        dispatcher.dispatch(
            "navigateto",
            "cmd-1",
            {
                "target_id": "A",
                "headers": "not-a-dict",
            },
        )
        deps["publish_command_error"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-1")


# ===========================================================================
# E-stop negative path and edge case tests
# ===========================================================================


class TestEstopNegativePaths:
    """E-stop negative path and edge case tests."""

    def test_estop_url_not_configured(self):
        """build_http_url returns None -> error published, no HTTP call."""
        dispatcher, deps = _make_dispatcher(
            build_http_url=MagicMock(return_value=None),
        )
        dispatcher.handle_estop("cmd-1", {})
        deps["publish_command_error"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-1")
        deps["_mock_session"].post.assert_not_called()

    def test_estop_invalid_headers_rejected(self):
        """Non-dict headers -> error published before HTTP call."""
        dispatcher, deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-2", {"headers": "not-a-dict"})
        deps["publish_command_error"].assert_called_once()
        deps["finish_command"].assert_called_once_with("cmd-2")
        deps["_mock_session"].post.assert_not_called()

    @patch("command_dispatcher.requests.Session")
    def test_estop_alert_callback_invoked_on_exhaustion(self, MockSession):
        """After all retries exhausted, alert callback is called with (command_id, error_msg)."""
        mock_session = MockSession.return_value
        mock_session.post.side_effect = requests.ConnectionError("refused")
        mock_session.close = MagicMock()
        alert_cb = MagicMock()
        deps_ns = SimpleNamespace(**{
            k: v for k, v in _make_dispatcher()[1].items() if not k.startswith("_")
        })
        dispatcher = CommandDispatcher(deps=deps_ns, estop_alert_callback=alert_cb)
        shutdown = threading.Event()
        dispatcher.set_shutdown_event(shutdown)
        dispatcher.handle_estop("cmd-3", {})
        dispatcher.join_estop_threads(timeout=30)
        alert_cb.assert_called_once()
        assert alert_cb.call_args[0][0] == "cmd-3"
        assert "refused" in alert_cb.call_args[0][1]

    @patch("command_dispatcher.requests.Session")
    def test_estop_exhaustion_publishes_error_and_finishes(self, MockSession):
        """After all retries exhausted, publish_command_error and finish_command are called."""
        mock_session = MockSession.return_value
        mock_session.post.side_effect = requests.ConnectionError("connection refused")
        mock_session.close = MagicMock()
        dispatcher, deps = _make_dispatcher()
        shutdown = threading.Event()
        dispatcher.set_shutdown_event(shutdown)
        dispatcher.handle_estop("cmd-exhaust", {})
        dispatcher.join_estop_threads(timeout=30)
        assert mock_session.post.call_count == _ESTOP_MAX_RETRIES
        deps["publish_command_error"].assert_called_once()
        error_msg = deps["publish_command_error"].call_args[0][2]
        assert "failed after" in error_msg.lower() or str(_ESTOP_MAX_RETRIES) in error_msg
        deps["finish_command"].assert_called_once_with("cmd-exhaust")

    @patch("command_dispatcher.requests.Session")
    def test_estop_thread_cleanup_after_exhaustion(self, MockSession):
        """After exhaustion, e-stop thread removes itself from _estop_threads."""
        mock_session = MockSession.return_value
        mock_session.post.side_effect = requests.ConnectionError("refused")
        mock_session.close = MagicMock()
        dispatcher, _deps = _make_dispatcher()
        shutdown = threading.Event()
        dispatcher.set_shutdown_event(shutdown)
        dispatcher.handle_estop("cmd-cleanup", {})
        dispatcher.join_estop_threads(timeout=30)
        assert len(dispatcher._estop_threads) == 0

    @patch("command_dispatcher.requests.Session")
    def test_estop_thread_cleanup_after_success(self, MockSession):
        """After successful e-stop, thread removes itself from _estop_threads."""
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_session.post.return_value = mock_response
        mock_session.close = MagicMock()
        dispatcher, _deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-ok", {})
        dispatcher.join_estop_threads(timeout=5)
        assert len(dispatcher._estop_threads) == 0

    @patch("command_dispatcher.requests.Session")
    def test_estop_session_closed_after_exhaustion(self, MockSession):
        """Dedicated e-stop session is closed even after all retries fail."""
        mock_session = MockSession.return_value
        mock_session.post.side_effect = requests.ConnectionError("refused")
        mock_session.close = MagicMock()
        dispatcher, _deps = _make_dispatcher()
        shutdown = threading.Event()
        dispatcher.set_shutdown_event(shutdown)
        dispatcher.handle_estop("cmd-close", {})
        dispatcher.join_estop_threads(timeout=30)
        mock_session.close.assert_called_once()

    @patch("command_dispatcher.requests.Session")
    def test_estop_non_json_response_fallback(self, MockSession):
        """response.json() raises -> falls back to response.text."""
        mock_session = MockSession.return_value
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "plain text"
        mock_session.post.return_value = mock_response
        mock_session.close = MagicMock()
        dispatcher, deps = _make_dispatcher()
        dispatcher.handle_estop("cmd-4", {})
        dispatcher.join_estop_threads(timeout=5)
        deps["send_response"].assert_called_once()
        payload = deps["send_response"].call_args[0][1]
        assert payload["body"] == "plain text"
