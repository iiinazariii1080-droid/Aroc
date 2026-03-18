"""Tests for shared.utils — singleton_factory, safe_json_loads, is_task_done, now_iso."""

import threading

from shared.utils import is_task_done, now_iso, safe_json_loads, singleton_factory


class TestSafeJsonLoads:
    def test_valid_json(self):
        data, error = safe_json_loads('{"key": "value"}')
        assert data == {"key": "value"}
        assert error is None

    def test_invalid_json(self):
        data, error = safe_json_loads("{bad json")
        assert data is None
        assert error is not None
        assert "JSON decode error" in error

    def test_empty_string(self):
        data, error = safe_json_loads("")
        assert data is None
        assert error is not None


class TestIsTaskDone:
    def test_finished_state(self):
        assert is_task_done({"state": "finished"}) is True

    def test_failed_state(self):
        assert is_task_done({"state": "failed"}) is True

    def test_running_state(self):
        assert is_task_done({"state": "running"}) is False

    def test_success_false(self):
        assert is_task_done({"success": False}) is True

    def test_non_dict(self):
        assert is_task_done("not a dict") is False
        assert is_task_done(None) is False
        assert is_task_done(42) is False

    def test_status_field(self):
        assert is_task_done({"status": "done"}) is True
        assert is_task_done({"status": "canceled"}) is True

    def test_case_insensitive(self):
        assert is_task_done({"state": "FINISHED"}) is True
        assert is_task_done({"state": "Failed"}) is True

    def test_empty_dict(self):
        assert is_task_done({}) is False


class TestSingletonFactory:
    def test_returns_callable(self):
        getter = singleton_factory(lambda: 42)
        assert callable(getter)

    def test_returns_same_instance(self):
        getter = singleton_factory(list)
        a = getter()
        b = getter()
        assert a is b

    def test_factory_called_once(self):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return {"created": True}

        getter = singleton_factory(factory)
        getter()
        getter()
        getter()
        assert call_count == 1

    def test_thread_safety(self):
        """Ensure only one instance is created under concurrent access."""
        call_count = 0
        lock = threading.Lock()

        def slow_factory():
            nonlocal call_count
            with lock:
                call_count += 1
            import time

            time.sleep(0.01)
            return object()

        getter = singleton_factory(slow_factory)
        results = []

        def worker():
            results.append(getter())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 1
        assert all(r is results[0] for r in results)


class TestNowIso:
    def test_returns_iso_string(self):
        result = now_iso()
        assert isinstance(result, str)
        assert "T" in result
        # Should be parseable
        from datetime import datetime

        datetime.fromisoformat(result)
