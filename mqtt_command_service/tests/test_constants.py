"""Tests for shared constants consistency."""

from shared.constants import TASK_FAILURE_STATES, TASK_TERMINAL_STATES


def test_failure_states_subset_of_terminal():
    """TASK_FAILURE_STATES must be a subset of TASK_TERMINAL_STATES."""
    assert TASK_FAILURE_STATES <= TASK_TERMINAL_STATES, (
        f"TASK_FAILURE_STATES contains values not in TASK_TERMINAL_STATES: "
        f"{TASK_FAILURE_STATES - TASK_TERMINAL_STATES}"
    )
