"""Tests for TaskWatcher — standalone task polling lifecycle."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from task_watcher import TaskWatcher


def _make_service_cfg(name="robot", base_url="http://robot:8110/api/v1/robot", watch_tasks=True):
    return SimpleNamespace(name=name, base_url=base_url, watch_tasks=watch_tasks)


def _make_watcher(**overrides) -> tuple[TaskWatcher, dict]:
    callable_defaults = dict(
        get_http_session=MagicMock(),
        auth_headers=MagicMock(return_value={}),
        send_response=MagicMock(),
        store_command_history=MagicMock(),
        publish_navigation_status=MagicMock(),
    )
    callable_defaults.update({k: v for k, v in overrides.items() if k in callable_defaults})
    deps_ns = SimpleNamespace(**callable_defaults)
    config_defaults = dict(
        task_poll_interval=0.1,
        task_poll_timeout=2.0,
        http_timeout=1.0,
        shutdown_event=threading.Event(),
    )
    config_defaults.update({k: v for k, v in overrides.items() if k in config_defaults})
    watcher = TaskWatcher(deps=deps_ns, **config_defaults)
    all_deps = {**config_defaults, **callable_defaults}
    return watcher, all_deps


class TestTaskWatcherStartStop:
    def test_start_watcher_creates_thread(self):
        watcher, _ = _make_watcher()
        cfg = _make_service_cfg()
        # Mock HTTP session so thread doesn't make real requests
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "running"}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        watcher._get_http_session = MagicMock(return_value=mock_session)

        watcher.start_watcher(cfg, "t-1", "r-1")
        assert watcher.active_task_count == 1
        assert "t-1" in watcher.active_task_ids

        # Cleanup
        watcher._shutdown.set()
        watcher.stop_all()

    def test_duplicate_watcher_skipped(self):
        watcher, _ = _make_watcher()
        cfg = _make_service_cfg()
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "running"}
        mock_session.get.return_value = mock_resp
        watcher._get_http_session = MagicMock(return_value=mock_session)

        watcher.start_watcher(cfg, "t-1", "r-1")
        watcher.start_watcher(cfg, "t-1", "r-2")  # duplicate
        assert watcher.active_task_count == 1

        watcher._shutdown.set()
        watcher.stop_all()

    def test_stop_all_clears_tasks(self):
        watcher, deps = _make_watcher()
        cfg = _make_service_cfg()
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "running"}
        mock_session.get.return_value = mock_resp
        watcher._get_http_session = MagicMock(return_value=mock_session)

        watcher.start_watcher(cfg, "t-1", "r-1")
        deps["shutdown_event"].set()
        watcher.stop_all()
        assert watcher.active_task_count == 0


class TestTaskWatcherCompletion:
    def test_completed_task_sends_result(self):
        watcher, deps = _make_watcher(task_poll_interval=0.05, task_poll_timeout=2.0)
        cfg = _make_service_cfg()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "finished", "success": True}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        watcher._get_http_session = MagicMock(return_value=mock_session)

        watcher.start_watcher(cfg, "t-1", "r-1", metadata={"command_id": "cmd-1", "status_type": "navigation"})

        # Wait for watcher to detect completion
        time.sleep(0.5)

        assert deps["send_response"].called
        payload = deps["send_response"].call_args[0][1]
        assert payload["type"] == "result"
        assert payload["success"] is True
        assert payload["task_id"] == "t-1"

        deps["store_command_history"].assert_called()

    def test_failed_task_detected(self):
        watcher, deps = _make_watcher(task_poll_interval=0.05)
        cfg = _make_service_cfg()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "failed", "error": "collision"}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        watcher._get_http_session = MagicMock(return_value=mock_session)

        watcher.start_watcher(cfg, "t-2", "r-2")
        time.sleep(0.5)

        assert deps["send_response"].called
        payload = deps["send_response"].call_args[0][1]
        assert payload["success"] is False


class TestTaskWatcherTimeout:
    def test_timeout_sends_error(self):
        watcher, deps = _make_watcher(
            task_poll_interval=0.05,
            task_poll_timeout=0.2,  # very short timeout
        )
        cfg = _make_service_cfg()

        # Always return "running" — never completes
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "running"}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        watcher._get_http_session = MagicMock(return_value=mock_session)

        watcher.start_watcher(cfg, "t-3", "r-3", metadata={"command_id": "cmd-3"})
        time.sleep(1.0)

        assert deps["send_response"].called
        payload = deps["send_response"].call_args[0][1]
        assert payload["success"] is False
        assert "did not finish" in payload["error"]["message"]


class TestTaskWatcherCapacity:
    def test_max_watchers_rejection(self):
        watcher, deps = _make_watcher()
        cfg = _make_service_cfg()

        # Exhaust all semaphore slots
        for _ in range(20):  # MAX_TASK_WATCHERS = 20
            watcher._task_watcher_sem.acquire(blocking=False)

        watcher.start_watcher(cfg, "t-overflow", "r-overflow")

        assert deps["send_response"].called
        payload = deps["send_response"].call_args[0][1]
        assert payload["success"] is False
        assert "Max concurrent" in payload["error"]["message"]


class TestGetTaskInfo:
    def test_get_existing_task(self):
        watcher, _ = _make_watcher()
        cfg = _make_service_cfg()
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "running"}
        mock_session.get.return_value = mock_resp
        watcher._get_http_session = MagicMock(return_value=mock_session)

        watcher.start_watcher(cfg, "t-1", "r-1", metadata={"command_id": "c-1"})
        info = watcher.get_task_info("t-1")
        assert info is not None
        assert info.task_id == "t-1"
        assert info.command_id == "c-1"

        watcher._shutdown.set()
        watcher.stop_all()

    def test_get_nonexistent_task(self):
        watcher, _ = _make_watcher()
        assert watcher.get_task_info("nonexistent") is None
