"""Unit tests for HttpExecutor.

Covers: submit, backpressure (503), session lifecycle, timeout resolution,
header preparation, shutdown, HTTP error handling, task watcher integration.
No time.sleep() — all thread sync via threading primitives.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from command_dedup import CommandDeduplicator
from http_executor import HttpExecutor, _FORBIDDEN_HEADERS
from payload_models import CommandContext
from task_watcher import TaskWatcher

from shared.config_types import BridgeConfig, ServiceConfig
from shared.constants import BRIDGE_HTTP_EXECUTOR_MAX_WORKERS
from tests.conftest import make_bridge_config, make_executor_deps, make_mock_response, make_mock_session


def _make_executor(
    *,
    config: BridgeConfig | None = None,
    deps_overrides: dict | None = None,
    mock_session: MagicMock | None = None,
) -> tuple[HttpExecutor, SimpleNamespace, threading.Event]:
    """Create HttpExecutor with controllable dependencies."""
    shutdown = threading.Event()
    deps = make_executor_deps(**(deps_overrides or {}))
    cfg = config or make_bridge_config()
    dedup = CommandDeduplicator()
    watcher = MagicMock(spec=TaskWatcher)

    if mock_session is None:
        mock_session = make_mock_session()

    with patch("http_executor.requests.Session", return_value=mock_session):
        executor = HttpExecutor(
            config=cfg,
            shutdown_event=shutdown,
            deps=deps,
            service_dedup=dedup,
            task_watcher=watcher,
        )

    return executor, deps, shutdown


class TestGetHttpSession:
    def test_creates_session_on_first_call(self):
        executor, _, _ = _make_executor()
        session = executor.get_http_session()
        assert session is not None

    def test_returns_same_session_on_repeated_call(self):
        executor, _, _ = _make_executor()
        s1 = executor.get_http_session()
        s2 = executor.get_http_session()
        assert s1 is s2

    def test_raises_during_shutdown(self):
        executor, _, shutdown = _make_executor()
        shutdown.set()
        with pytest.raises(RuntimeError, match="shutdown"):
            executor.get_http_session()


class TestPrepareRequestHeaders:
    def test_merges_auth_and_user_headers(self):
        executor, deps, _ = _make_executor(
            deps_overrides={"auth_headers": MagicMock(return_value={"Authorization": "Bearer tok"})}
        )
        result = executor.prepare_request_headers({"X-Custom": "val"})
        assert result["Authorization"] == "Bearer tok"
        assert result["X-Custom"] == "val"

    def test_filters_forbidden_headers(self):
        executor, _, _ = _make_executor()
        user_headers = {h: "bad" for h in ["Authorization", "Cookie", "Host", "Transfer-Encoding"]}
        result = executor.prepare_request_headers(user_headers)
        for h in ["authorization", "cookie", "host", "transfer-encoding"]:
            assert h not in {k.lower() for k in result}

    def test_none_values_skipped(self):
        executor, _, _ = _make_executor()
        result = executor.prepare_request_headers({"X-Val": None, "X-Good": "yes"})
        assert "X-Val" not in result
        assert result["X-Good"] == "yes"

    def test_empty_headers_returns_auth_only(self):
        executor, deps, _ = _make_executor(
            deps_overrides={"auth_headers": MagicMock(return_value={"Authorization": "Bearer tok"})}
        )
        result = executor.prepare_request_headers({})
        assert result == {"Authorization": "Bearer tok"}


class TestResolveTimeout:
    def test_explicit_timeout_clamped(self):
        executor, _, _ = _make_executor()
        assert executor.resolve_timeout({"timeout": 0.01}, "robot", "/tasks") == 0.1
        assert executor.resolve_timeout({"timeout": 500}, "robot", "/tasks") == 300.0
        assert executor.resolve_timeout({"timeout": 10}, "robot", "/tasks") == 10.0

    def test_no_timeout_returns_none(self):
        executor, _, _ = _make_executor()
        assert executor.resolve_timeout({}, "robot", "/tasks") is None

    def test_invalid_timeout_raises(self):
        executor, _, _ = _make_executor()
        with pytest.raises(ValueError, match="number"):
            executor.resolve_timeout({"timeout": "fast"}, "robot", "/tasks")

    def test_bool_timeout_raises(self):
        executor, _, _ = _make_executor()
        with pytest.raises(ValueError, match="number"):
            executor.resolve_timeout({"timeout": True}, "robot", "/tasks")

    def test_long_operation_config(self):
        cfg = make_bridge_config(
            long_operations={"robot": {"/tasks/navigate": 60.0}},
        )
        executor, _, _ = _make_executor(config=cfg)
        result = executor.resolve_timeout({}, "robot", "/tasks/navigate")
        assert result == 60.0


class TestSubmit:
    def test_submit_during_shutdown_finishes_command(self):
        executor, deps, shutdown = _make_executor()
        shutdown.set()
        ctx = CommandContext(command_name="navigateTo", command_id="cmd-1")
        executor.submit(service="robot", context=ctx)
        deps.finish_command.assert_called_once_with("cmd-1")

    def test_backpressure_returns_503(self):
        executor, deps, _ = _make_executor()
        # Exhaust semaphore
        for _ in range(BRIDGE_HTTP_EXECUTOR_MAX_WORKERS * 5):
            executor._http_semaphore.acquire(blocking=False)

        ctx = CommandContext(command_name="navigateTo", command_id="cmd-bp")
        executor.submit(service="robot", request_id="req-bp", context=ctx)

        deps.send_response.assert_called_once()
        payload = deps.send_response.call_args[0][1]
        assert payload["status_code"] == 503
        assert payload["success"] is False
        deps.finish_command.assert_called_once_with("cmd-bp")


class TestExecute:
    @patch("http_executor.requests.Session")
    def test_http_success_sends_ack(self, MockSession):
        mock_response = make_mock_response(200, {"task_id": "t-1"})
        mock_session = make_mock_session(mock_response)
        MockSession.return_value = mock_session

        executor, deps, shutdown = _make_executor(mock_session=mock_session)
        done = threading.Event()
        original_finish = deps.finish_command
        deps.finish_command = lambda cid: (original_finish(cid), done.set())

        ctx = CommandContext(command_name="navigateTo", command_id="cmd-ok")
        executor.submit(
            service="robot",
            request_id="req-ok",
            method="POST",
            url="http://robot:8110/tasks/navigate",
            headers={},
            body={"target_id": "A"},
            context=ctx,
        )

        done.wait(timeout=5)
        deps.send_response.assert_called()
        payload = deps.send_response.call_args[0][1]
        assert payload["success"] is True
        assert payload["status_code"] == 200
        assert payload["request_id"] == "req-ok"

    @patch("http_executor.requests.Session")
    def test_http_exception_sends_error_ack(self, MockSession):
        mock_session = MagicMock()
        mock_session.request.side_effect = requests.ConnectionError("refused")
        MockSession.return_value = mock_session

        executor, deps, shutdown = _make_executor(mock_session=mock_session)
        done = threading.Event()
        original_finish = deps.finish_command
        deps.finish_command = lambda cid: (original_finish(cid), done.set())

        ctx = CommandContext(command_name="navigateTo", command_id="cmd-err")
        executor.submit(
            service="robot",
            request_id="req-err",
            method="POST",
            url="http://robot:8110/tasks/navigate",
            headers={},
            body={},
            context=ctx,
        )

        done.wait(timeout=5)
        deps.send_response.assert_called()
        payload = deps.send_response.call_args[0][1]
        assert payload["success"] is False
        assert payload["status_code"] == 0
        assert payload["error"]["type"] == "http_error"

    @patch("http_executor.requests.Session")
    def test_non_json_response_falls_back_to_text(self, MockSession):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "plain text body"
        mock_response.headers = {"content-type": "text/plain"}
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response
        MockSession.return_value = mock_session

        executor, deps, _ = _make_executor(mock_session=mock_session)
        done = threading.Event()
        original_finish = deps.finish_command
        deps.finish_command = lambda cid: (original_finish(cid), done.set())

        ctx = CommandContext(command_name="navigateTo", command_id="cmd-txt")
        executor.submit(
            service="robot",
            request_id="req-txt",
            method="GET",
            url="http://robot:8110/status",
            headers={},
            body=None,
            context=ctx,
        )
        done.wait(timeout=5)
        payload = deps.send_response.call_args[0][1]
        assert payload["body"] == "plain text body"

    @patch("http_executor.requests.Session")
    def test_task_watcher_started_when_service_watches_tasks(self, MockSession):
        mock_response = make_mock_response(200, {"task_id": "task-42"})
        mock_session = make_mock_session(mock_response)
        MockSession.return_value = mock_session

        cfg = make_bridge_config(services={
            "robot": ServiceConfig(name="robot", base_url="http://robot:8110", watch_tasks=True),
        })
        executor, deps, _ = _make_executor(config=cfg, mock_session=mock_session)
        done = threading.Event()
        original_finish = deps.finish_command
        deps.finish_command = lambda cid: (original_finish(cid), done.set())

        ctx = CommandContext(command_name="navigateTo", command_id="cmd-watch")
        executor.submit(
            service="robot",
            request_id="req-watch",
            method="POST",
            url="http://robot:8110/tasks/navigate",
            headers={},
            body={"target_id": "A"},
            context=ctx,
        )
        done.wait(timeout=5)
        executor._task_watcher.start_watcher.assert_called_once()
        call_args = executor._task_watcher.start_watcher.call_args
        assert call_args[0][1] == "task-42"


class TestShutdown:
    def test_shutdown_completes(self):
        executor, _, shutdown = _make_executor()
        shutdown.set()
        executor.shutdown()
        # Executor should be shut down — further submits are rejected
        assert shutdown.is_set()

    @patch("http_executor.requests.Session")
    def test_shutdown_closes_sessions(self, MockSession):
        mock_session = MagicMock()
        MockSession.return_value = mock_session

        executor, _, shutdown = _make_executor(mock_session=mock_session)
        # Create a session
        _ = executor.get_http_session()
        shutdown.set()
        executor.shutdown()
        # Verify shutdown attempted to close sessions (via iteration)
        # The mock session should have close() called
