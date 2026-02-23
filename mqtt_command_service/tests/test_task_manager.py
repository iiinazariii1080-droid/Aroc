"""Tests for task_manager – TaskManagerMixin and TaskInfo."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from constants import MAX_TASK_WATCHERS
from task_manager import TaskInfo, TaskManagerMixin

# ---- Fake bridge stub ---------------------------------------------------

class _FakeServiceCfg:
    def __init__(self, name="robot", base_url="http://robot:8080"):
        self.name = name
        self.base_url = base_url


class _FakeConfig:
    task_poll_interval = 0.05
    task_poll_timeout = 1.0
    http_timeout = 2.0


class FakeBridge(TaskManagerMixin):
    """Minimal bridge satisfying BridgeProtocol for the mixin."""

    def __init__(self):
        self._shutdown = threading.Event()
        self._tasks_lock = threading.Lock()
        self._active_tasks = {}
        self._task_watcher_sem = threading.Semaphore(MAX_TASK_WATCHERS)
        self.config = _FakeConfig()

        self._send_response = MagicMock()
        self._publish_navigation_status = MagicMock()
        self._store_command_history = MagicMock()
        self._auth_headers = MagicMock(return_value={})
        self._http_session = requests.Session()

    def _get_http_session(self):
        return self._http_session


@pytest.fixture
def bridge():
    b = FakeBridge()
    yield b
    b._shutdown.set()
    # Let any background threads finish
    time.sleep(0.15)


# ---- TaskInfo -----------------------------------------------------------

class TestTaskInfo:
    def test_dataclass_fields(self):
        t = TaskInfo(
            service="robot",
            request_id="r-1",
            task_id="t-1",
            thread=threading.Thread(),
            started_at=time.time(),
        )
        assert t.status == "running"
        assert t.command_id is None
        assert t.metadata == {}

    def test_custom_status(self):
        t = TaskInfo(
            service="robot",
            request_id="r-1",
            task_id="t-1",
            thread=threading.Thread(),
            started_at=time.time(),
            status="completed",
            command_id="cmd-1",
        )
        assert t.status == "completed"
        assert t.command_id == "cmd-1"


# ---- get_task_info / get_active_tasks -----------------------------------

class TestTaskQueries:
    def test_get_task_info_none(self, bridge):
        assert bridge.get_task_info("nonexistent") is None

    def test_get_task_info_copy(self, bridge):
        info = TaskInfo("robot", "r1", "t1", threading.Thread(), time.time())
        bridge._active_tasks["t1"] = info
        result = bridge.get_task_info("t1")
        assert result is not None
        assert result is not info  # copy, not reference
        assert result.task_id == "t1"

    def test_get_active_tasks(self, bridge):
        info = TaskInfo("robot", "r1", "t1", threading.Thread(), time.time())
        bridge._active_tasks["t1"] = info
        tasks = bridge.get_active_tasks()
        assert "t1" in tasks
        assert tasks is not bridge._active_tasks  # copy

    def test_remove_task(self, bridge):
        bridge._active_tasks["t1"] = TaskInfo("robot", "r1", "t1", threading.Thread(), time.time())
        bridge._remove_task("t1")
        assert "t1" not in bridge._active_tasks

    def test_remove_nonexistent(self, bridge):
        bridge._remove_task("noexist")  # should not raise


# ---- _start_task_watcher -------------------------------------------------

class TestStartTaskWatcher:
    def test_starts_thread(self, bridge):
        svc = _FakeServiceCfg()
        with patch.object(bridge, "_task_watcher_loop"):
            bridge._start_task_watcher(svc, "t-1", "r-1")
            assert "t-1" in bridge._active_tasks
            info = bridge._active_tasks["t-1"]
            assert info.service == "robot"
            assert info.status == "running"

    def test_skip_duplicate(self, bridge):
        svc = _FakeServiceCfg()
        bridge._active_tasks["t-1"] = TaskInfo("robot", "r-1", "t-1", threading.Thread(), time.time())
        original_sem_value = bridge._task_watcher_sem._value
        bridge._start_task_watcher(svc, "t-1", "r-1")
        # Semaphore released back since duplicate skipped
        assert bridge._task_watcher_sem._value == original_sem_value


# ---- _publish_task_timeout -----------------------------------------------

class TestPublishTaskTimeout:
    def test_timeout_payload(self, bridge):
        bridge._active_tasks["t-1"] = TaskInfo("robot", "r-1", "t-1", threading.Thread(), time.time())
        bridge._publish_task_timeout("robot", "t-1", "r-1", 200, {"state": "running"}, None)
        bridge._send_response.assert_called_once()
        payload = bridge._send_response.call_args[0][1]
        assert payload["success"] is False
        assert "did not finish" in payload["error"]["message"]

    def test_updates_status(self, bridge):
        bridge._active_tasks["t-1"] = TaskInfo("robot", "r-1", "t-1", threading.Thread(), time.time())
        bridge._publish_task_timeout("robot", "t-1", "r-1", None, None, "timeout")
        assert bridge._active_tasks["t-1"].status == "timeout"

    def test_stores_command_history(self, bridge):
        bridge._active_tasks["t-1"] = TaskInfo("robot", "r-1", "t-1", threading.Thread(), time.time())
        bridge._publish_task_timeout("robot", "t-1", "r-1", None, None, None, {"command_id": "cmd-99"})
        bridge._store_command_history.assert_called_once()
        assert bridge._store_command_history.call_args[0][0] == "cmd-99"


# ---- _stop_all_watchers --------------------------------------------------

class TestStopAllWatchers:
    def test_empty(self, bridge):
        bridge._stop_all_watchers()  # no error

    def test_clears_tasks(self, bridge):
        t = threading.Thread(target=lambda: None)
        t.start()
        bridge._active_tasks["t-1"] = TaskInfo("robot", "r-1", "t-1", t, time.time())
        bridge._stop_all_watchers()
        assert len(bridge._active_tasks) == 0
