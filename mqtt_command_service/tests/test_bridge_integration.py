"""Integration tests for bridge command flow (reduced mocking).

Uses make_mock_response which derives .ok from status code range,
avoiding the tautological pattern of setting .ok = expected_success.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from command_dispatcher import CommandDispatcher

from tests.conftest import make_mock_response


class TestHttpStatusCodeMapping:
    """Parametric tests for HTTP status code -> success/failure mapping."""

    @pytest.mark.parametrize(
        "status_code,expected_success",
        [
            (200, True),
            (201, True),
            (204, True),
            (400, False),
            (401, False),
            (403, False),
            (404, False),
            (409, False),
            (422, False),
            (500, False),
            (502, False),
            (503, False),
            (504, False),
        ],
    )
    def test_http_status_code_produces_correct_ack(self, status_code, expected_success):
        """Each HTTP status code produces correct success/failure in ack.

        Uses make_mock_response which derives .ok from status code range
        (200-399 = True), matching real requests.Response behavior.
        """
        mock_response = make_mock_response(status_code, body={"detail": "test"})

        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_session.close = MagicMock()

        sent = []
        deps = SimpleNamespace(
            build_http_url=MagicMock(return_value="http://robot:8110/tasks/estop"),
            submit_http=MagicMock(),
            publish_command_error=MagicMock(),
            finish_command=MagicMock(),
            get_http_session=MagicMock(return_value=mock_session),
            auth_headers=MagicMock(return_value={}),
            send_response=lambda svc, p: sent.append(p),
            store_command_history=MagicMock(),
            publish_navigation_status=MagicMock(),
        )
        dispatcher = CommandDispatcher(deps=deps)
        with patch("command_dispatcher.requests.Session", return_value=mock_session):
            dispatcher.handle_estop("cmd-1", {})
            dispatcher.join_estop_threads(timeout=5)

        assert len(sent) == 1
        assert sent[0]["success"] == expected_success
        assert sent[0]["status_code"] == status_code
