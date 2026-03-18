"""
Unit tests for domain event models.
"""
import pytest
from pydantic import ValidationError

from domain.events import (
    AckEvent,
    AckType,
    ResultCanceledEvent,
    ResultErrorEvent,
    ResultSuccessEvent,
    ResultType,
    StateExecutingEvent,
    StateProgressEvent,
    StateType,
    now_iso,
)


# -- Enum values ---------------------------------------------------------------


class TestEnums:
    def test_ack_type_values(self):
        assert AckType.RECEIVED.value == "ack.received"
        assert AckType.ACCEPTED.value == "ack.accepted"

    def test_state_type_values(self):
        assert StateType.EXECUTING.value == "state.executing"
        assert StateType.PROGRESS.value == "state.progress"

    def test_result_type_values(self):
        assert ResultType.SUCCESS.value == "result.success"
        assert ResultType.ERROR.value == "result.error"
        assert ResultType.CANCELED.value == "result.canceled"


# -- AckEvent ------------------------------------------------------------------


class TestAckEvent:
    def test_received(self):
        ev = AckEvent(type=AckType.RECEIVED.value, command_id="cmd-1")
        assert ev.type == "ack.received"
        assert ev.command_id == "cmd-1"
        assert ev.timestamp  # auto-populated by default_factory

    def test_accepted(self):
        ev = AckEvent(type=AckType.ACCEPTED.value, command_id="cmd-2")
        assert ev.type == "ack.accepted"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            AckEvent(type="result.success", command_id="cmd-1")


# -- StateExecutingEvent -------------------------------------------------------


class TestStateExecutingEvent:
    def test_creation(self):
        ev = StateExecutingEvent(type="state.executing", command_id="cmd-1")
        assert ev.type == "state.executing"
        assert ev.command_id == "cmd-1"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            StateExecutingEvent(type="ack.received", command_id="cmd-1")


# -- StateProgressEvent --------------------------------------------------------


class TestStateProgressEvent:
    def test_creation(self):
        ev = StateProgressEvent(
            type="state.progress",
            command_id="cmd-1",
            progress_percent=42,
            eta_seconds=5.5,
        )
        assert ev.progress_percent == 42
        assert ev.eta_seconds == 5.5

    def test_progress_bounds(self):
        with pytest.raises(ValidationError):
            StateProgressEvent(
                type="state.progress",
                command_id="cmd-1",
                progress_percent=101,
            )


# -- ResultSuccessEvent --------------------------------------------------------


class TestResultSuccessEvent:
    def test_creation(self):
        ev = ResultSuccessEvent(type="result.success", command_id="cmd-1")
        assert ev.type == "result.success"


# -- ResultErrorEvent ----------------------------------------------------------


class TestResultErrorEvent:
    def test_creation(self):
        ev = ResultErrorEvent(
            type="result.error",
            command_id="cmd-1",
            reason="transport_timeout",
        )
        assert ev.type == "result.error"
        assert ev.reason == "transport_timeout"

    def test_reason_required(self):
        with pytest.raises(ValidationError):
            ResultErrorEvent(type="result.error", command_id="cmd-1")


# -- ResultCanceledEvent -------------------------------------------------------


class TestResultCanceledEvent:
    def test_creation(self):
        ev = ResultCanceledEvent(type="result.canceled", command_id="cmd-1")
        assert ev.type == "result.canceled"


# -- now_iso helper ------------------------------------------------------------


class TestNowIso:
    def test_returns_iso_string(self):
        ts = now_iso()
        assert isinstance(ts, str)
        assert "T" in ts
        # Should contain timezone info
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")
