"""
Unit tests for ForceArrivalSignal.
"""
import asyncio
from unittest.mock import patch

import pytest

from services.force_arrival import ForceArrivalSignal


@pytest.fixture
def signal() -> ForceArrivalSignal:
    """Fresh ForceArrivalSignal instance."""
    return ForceArrivalSignal()


@pytest.fixture
def command_id() -> str:
    return "cmd-001"


# -- Dwell timer tests --------------------------------------------------------


def test_start_dwell_sets_timestamp(signal: ForceArrivalSignal, command_id: str):
    """start_dwell records a monotonic timestamp."""
    signal.start_dwell(command_id)
    assert command_id in signal._at_goal_since
    assert isinstance(signal._at_goal_since[command_id], float)


def test_start_dwell_idempotent(signal: ForceArrivalSignal, command_id: str):
    """Calling start_dwell again must not overwrite the original timestamp."""
    signal.start_dwell(command_id)
    first_ts = signal._at_goal_since[command_id]

    # Advance monotonic clock and call again
    with patch("services.force_arrival.time.monotonic", return_value=first_ts + 5.0):
        signal.start_dwell(command_id)

    assert signal._at_goal_since[command_id] == first_ts


def test_dwell_elapsed_false_before_threshold(signal: ForceArrivalSignal, command_id: str):
    """dwell_elapsed returns False when threshold has not been reached."""
    base = 1000.0
    with patch("services.force_arrival.time.monotonic", return_value=base):
        signal.start_dwell(command_id)

    with patch("services.force_arrival.time.monotonic", return_value=base + 1.0):
        assert signal.dwell_elapsed(command_id, threshold_s=2.0) is False


def test_dwell_elapsed_true_after_threshold(signal: ForceArrivalSignal, command_id: str):
    """dwell_elapsed returns True once monotonic time exceeds threshold."""
    base = 1000.0
    with patch("services.force_arrival.time.monotonic", return_value=base):
        signal.start_dwell(command_id)

    with patch("services.force_arrival.time.monotonic", return_value=base + 3.0):
        assert signal.dwell_elapsed(command_id, threshold_s=2.0) is True


def test_reset_dwell_clears_timer(signal: ForceArrivalSignal, command_id: str):
    """reset_dwell removes the dwell timestamp."""
    signal.start_dwell(command_id)
    signal.reset_dwell(command_id)
    assert command_id not in signal._at_goal_since


def test_dwell_elapsed_unknown_command_returns_false(signal: ForceArrivalSignal):
    """dwell_elapsed for an unknown command_id returns False."""
    assert signal.dwell_elapsed("nonexistent", threshold_s=0.0) is False


# -- Event lifecycle tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_is_set_consume_lifecycle(signal: ForceArrivalSignal, command_id: str):
    """signal -> is_set True -> consume True -> is_set False."""
    signal.ensure(command_id)

    assert signal.is_set(command_id) is False
    assert signal.consume(command_id) is False

    signal.signal(command_id)

    assert signal.is_set(command_id) is True
    assert signal.consume(command_id) is True
    # After consume, the event is cleared
    assert signal.is_set(command_id) is False
    assert signal.consume(command_id) is False


@pytest.mark.asyncio
async def test_ensure_returns_same_event(signal: ForceArrivalSignal, command_id: str):
    """ensure is idempotent — returns the same Event object."""
    ev1 = signal.ensure(command_id)
    ev2 = signal.ensure(command_id)
    assert ev1 is ev2


@pytest.mark.asyncio
async def test_cleanup_removes_event_and_dwell(signal: ForceArrivalSignal, command_id: str):
    """cleanup removes both the event and dwell timer."""
    signal.ensure(command_id)
    signal.start_dwell(command_id)

    signal.cleanup(command_id)

    assert signal.get(command_id) is None
    assert command_id not in signal._at_goal_since


def test_clear_all(signal: ForceArrivalSignal):
    """clear_all removes all events and dwell timers."""
    for cid in ("a", "b", "c"):
        signal.ensure(cid)
        signal.start_dwell(cid)

    signal.clear_all()

    assert len(signal._events) == 0
    assert len(signal._at_goal_since) == 0


@pytest.mark.asyncio
async def test_signal_clears_dwell(signal: ForceArrivalSignal, command_id: str):
    """Calling signal() should clear the dwell timer for that command."""
    signal.start_dwell(command_id)
    signal.signal(command_id)
    assert command_id not in signal._at_goal_since
    assert signal.is_set(command_id) is True


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown(signal: ForceArrivalSignal):
    """get() returns None for an unknown command_id."""
    assert signal.get("unknown") is None


@pytest.mark.asyncio
async def test_is_set_returns_false_for_unknown(signal: ForceArrivalSignal):
    """is_set returns False when the command_id has never been registered."""
    assert signal.is_set("unknown") is False
