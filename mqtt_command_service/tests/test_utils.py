"""Tests for utils – safe_json_loads and is_task_done."""


from utils import is_task_done, safe_json_loads


class TestSafeJsonLoads:
    def test_valid_json(self):
        data, err = safe_json_loads('{"key": "val"}')
        assert data == {"key": "val"}
        assert err is None

    def test_valid_list(self):
        data, err = safe_json_loads('[1, 2, 3]')
        assert data == [1, 2, 3]
        assert err is None

    def test_invalid_json(self):
        data, err = safe_json_loads("{bad}")
        assert data is None
        assert err is not None
        assert "JSON decode error" in err

    def test_empty_string(self):
        data, err = safe_json_loads("")
        assert data is None
        assert err is not None

    def test_non_string_input(self):
        """Passing non-string causes unexpected error path."""
        data, err = safe_json_loads(123)
        assert data is None
        assert "Unexpected error" in err


class TestIsTaskDone:
    def test_non_dict_returns_true(self):
        assert is_task_done("text") is True
        assert is_task_done(None) is True
        assert is_task_done(42) is True

    def test_empty_dict_not_done(self):
        assert is_task_done({}) is False

    def test_detail_present(self):
        """Non-trivial detail means task is done."""
        assert is_task_done({"detail": "Navigation completed"}) is True

    def test_detail_none_not_done(self):
        assert is_task_done({"detail": None}) is False

    def test_detail_working_not_done(self):
        assert is_task_done({"detail": "working"}) is False

    def test_terminal_state(self):
        assert is_task_done({"state": "finished"}) is True
        assert is_task_done({"state": "failed"}) is True
        assert is_task_done({"state": "cancelled"}) is True

    def test_running_not_done(self):
        assert is_task_done({"state": "running"}) is False

    def test_success_false(self):
        assert is_task_done({"success": False}) is True

    def test_status_field(self):
        """Uses 'status' when 'state' is absent."""
        assert is_task_done({"status": "done"}) is True
