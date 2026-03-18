"""Tests for typed payload models."""

from payload_models import AckPayload, ErrorDetail, ResultPayload


class TestErrorDetail:
    def test_http_error_factory(self):
        err = ErrorDetail.http_error("Connection refused")
        assert err.type == "http_error"
        assert err.message == "Connection refused"

    def test_command_error_factory(self):
        err = ErrorDetail.command_error("target_id is required")
        assert err.type == "command_error"

    def test_routing_error_factory(self):
        err = ErrorDetail.routing_error("Unknown service")
        assert err.type == "routing_error"

    def test_invalid_json_factory(self):
        err = ErrorDetail.invalid_json("Payload is not a JSON object")
        assert err.type == "invalid_json"


class TestAckPayload:
    def test_success_to_dict(self):
        ack = AckPayload(
            request_id="req-1",
            service="robot",
            success=True,
            status_code=200,
            body={"task_id": "t-1"},
        )
        d = ack.to_dict()
        assert d["type"] == "ack"
        assert d["request_id"] == "req-1"
        assert d["service"] == "robot"
        assert d["success"] is True
        assert d["status_code"] == 200
        assert d["body"] == {"task_id": "t-1"}
        assert "error" not in d

    def test_error_to_dict(self):
        ack = AckPayload(
            request_id="req-2",
            service="robot",
            success=False,
            status_code=500,
            error=ErrorDetail.http_error("HTTP 500"),
        )
        d = ack.to_dict()
        assert d["success"] is False
        assert d["error"]["type"] == "http_error"
        assert d["error"]["message"] == "HTTP 500"

    def test_command_id_included_when_set(self):
        ack = AckPayload(
            request_id="req-1",
            service="robot",
            success=True,
            status_code=200,
            command_id="cmd-1",
        )
        d = ack.to_dict()
        assert d["command_id"] == "cmd-1"

    def test_command_id_omitted_when_none(self):
        ack = AckPayload(
            request_id="req-1",
            service="robot",
            success=True,
            status_code=200,
        )
        d = ack.to_dict()
        assert "command_id" not in d

    def test_task_id_included_when_set(self):
        ack = AckPayload(
            request_id="req-1",
            service="robot",
            success=True,
            status_code=200,
            task_id="task-1",
        )
        d = ack.to_dict()
        assert d["task_id"] == "task-1"

    def test_headers_included_when_nonempty(self):
        ack = AckPayload(
            request_id="req-1",
            service="robot",
            success=True,
            status_code=200,
            headers={"content-type": "application/json"},
        )
        d = ack.to_dict()
        assert d["headers"] == {"content-type": "application/json"}

    def test_headers_omitted_when_empty(self):
        ack = AckPayload(
            request_id="req-1",
            service="robot",
            success=True,
            status_code=200,
        )
        d = ack.to_dict()
        assert "headers" not in d


class TestResultPayload:
    def test_success_to_dict(self):
        result = ResultPayload(
            request_id="req-1",
            task_id="task-1",
            service="robot",
            success=True,
            status_code=200,
            body={"state": "finished"},
        )
        d = result.to_dict()
        assert d["type"] == "result"
        assert d["request_id"] == "req-1"
        assert d["task_id"] == "task-1"
        assert d["service"] == "robot"
        assert d["success"] is True
        assert d["status_code"] == 200
        assert "error" not in d

    def test_failure_to_dict(self):
        result = ResultPayload(
            request_id="req-1",
            task_id="task-1",
            service="robot",
            success=False,
            status_code=500,
            error=ErrorDetail.http_error("timeout"),
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["error"]["type"] == "http_error"
        assert d["error"]["message"] == "timeout"
