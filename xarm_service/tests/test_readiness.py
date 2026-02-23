"""Unit tests for ReadinessGate logic."""
import pytest
from drivers.xarm_driver.state import StateStore, ReadinessGate


def test_readiness_ready_when_all_true():
    store = StateStore()
    gate = ReadinessGate(store)
    store.set_connected(True)
    store.set_robot(motion_enabled=True, is_moving=False, error_code=0)
    store.set_busy(False)
    assert gate.ready is True
    assert gate.connected is True
    assert gate.faulted is False
    assert gate.motion_enabled is True
    assert gate.busy is False


def test_readiness_not_ready_when_disconnected():
    store = StateStore()
    gate = ReadinessGate(store)
    store.set_connected(False)
    store.set_robot(motion_enabled=True)
    store.set_busy(False)
    assert gate.ready is False
    assert gate.connected is False


def test_readiness_not_ready_when_faulted():
    store = StateStore()
    gate = ReadinessGate(store)
    store.set_connected(True)
    store.set_robot(motion_enabled=True, error_code=1)
    store.set_busy(False)
    assert gate.ready is False
    assert gate.faulted is True


def test_readiness_not_ready_when_motion_disabled():
    store = StateStore()
    gate = ReadinessGate(store)
    store.set_connected(True)
    store.set_robot(motion_enabled=False, error_code=0)
    store.set_busy(False)
    assert gate.ready is False
    assert gate.motion_enabled is False


def test_readiness_not_ready_when_busy():
    store = StateStore()
    gate = ReadinessGate(store)
    store.set_connected(True)
    store.set_robot(motion_enabled=True, is_moving=True)
    store.set_busy(True)
    assert gate.ready is False
    assert gate.busy is True
