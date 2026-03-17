"""Integration boundary tests: cross-component contract verification.

Each test crosses at least 2 real component boundaries and verifies a contract
that existing unit/component tests do not cover.

Boundary map:
  HTTP Request → FastAPI route → run_command → DriveUseCases → FakeDrive (state mutation)
  FakeDrive state → DriveUseCases.get_drive_status → HTTP response (status read)
  Driver exceptions → translate_driver_exception → ServiceError → HTTPException → JSON
  IdleShutdownMixin → _idle_shutdown_action → disable_voltage / skip when jog active

What stays real: FastAPI pipeline, command_executor, use_cases, status mappers, error handlers.
What is faked: FakeDrive (no Modbus I/O), FakeEventBus (no SSE delivery).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

import main
from drivers.dryve_d1.protocol.exceptions import ModbusGatewayException
from tests.fakes import ControllableLock, FakeDrive, FakeEventBus, set_app_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _v1_ok(r) -> dict:
    """Assert v1 API envelope success and return data."""
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, f"Expected ok=True: {body}"
    return body["data"]


def _v1_err(r, status: int) -> dict:
    """Assert v1 API error response."""
    assert r.status_code == status, f"Expected {status}, got {r.status_code}: {r.text}"
    return r.json()


def _legacy_ok(r) -> dict:
    """Assert legacy endpoint (no envelope) success and return body."""
    assert r.status_code == 200, r.text
    return r.json()


_JOG_POS = {"direction": "positive", "speed": 10, "ttl_ms": 500}
_JOG_NEG = {"direction": "negative", "speed": 7, "ttl_ms": 500}
_MOVE = {
    "target_position": 50000,
    "profile": {
        "velocity": 5000,
        "acceleration": 10000,
        "deceleration": 10000,
    },
    "timeout_ms": 5000,
}


def _setup_state(app, drive=None, motor_locked=False):
    d = drive or FakeDrive()
    set_app_state(
        app,
        drive=d,
        event_bus=FakeEventBus(),
        motor_lock=ControllableLock(locked_state=motor_locked),
    )
    return d


# ===========================================================================
# GAP 1: HTTP command → state mutation → status read roundtrip
# ===========================================================================


class TestCommandStateRoundtrip:
    """Verify that commands mutate FakeDrive state observable through status endpoints."""

    def test_jog_start_makes_status_report_is_moving(self, noop_lifecycle):
        """IT-01: POST jog_start → GET /status reflects is_moving=true."""
        with TestClient(main.app) as c:
            _setup_state(main.app)
            data = _v1_ok(c.post("/drive/jog_start", json=_JOG_POS))
            assert data["velocity"] > 0

            # Legacy /status returns flat StatusResponse with is_moving field
            status = _legacy_ok(c.get("/status"))
            assert status["is_moving"] is True

    def test_stop_after_jog_clears_is_moving(self, noop_lifecycle):
        """IT-02: jog_start → stop → status shows is_moving=false."""
        with TestClient(main.app) as c:
            _setup_state(main.app)
            _v1_ok(c.post("/drive/jog_start", json=_JOG_POS))
            assert _legacy_ok(c.get("/status"))["is_moving"] is True

            _v1_ok(c.post("/drive/stop", json={"mode": "quick_stop"}))
            assert _legacy_ok(c.get("/status"))["is_moving"] is False

    def test_move_updates_position_in_v1_status(self, noop_lifecycle):
        """IT-03: POST move(target=50000) → GET /drive/status shows position=50000."""
        with TestClient(main.app) as c:
            _setup_state(main.app)
            move_data = _v1_ok(c.post("/drive/move_to_position", json=_MOVE))
            assert move_data["target_position"] == 50000

            v1_status = _v1_ok(c.get("/drive/status"))
            assert v1_status["position"] == 50000


# ===========================================================================
# GAP 2: Command trace lifecycle through HTTP
# ===========================================================================


class TestCommandTraceLifecycle:
    """Verify that command traces propagate through the full pipeline."""

    def test_trace_empty_then_populated_after_command(self, noop_lifecycle):
        """IT-04: Trace starts empty, populated after command with operation name."""
        with TestClient(main.app) as c:
            _setup_state(main.app)
            # Clear stale trace from previous tests
            main.app.state.latest_command_trace = None

            trace1 = _v1_ok(c.get("/drive/trace/latest"))
            assert trace1["has_trace"] is False

            _v1_ok(c.post("/drive/jog_start", json=_JOG_POS))

            trace2 = _v1_ok(c.get("/drive/trace/latest"))
            assert trace2["has_trace"] is True
            assert trace2["trace"]["operation"] == "jog_start"
            assert trace2["trace"]["command_id"] is not None

    def test_failed_command_updates_trace(self, noop_lifecycle):
        """IT-05: Failed command still writes trace (operation is recorded)."""
        with TestClient(main.app) as c:
            _setup_state(main.app, drive=FakeDrive(fault_mode=True))
            main.app.state.latest_command_trace = None

            r = c.post("/drive/move_to_position", json=_MOVE)
            assert r.status_code == 409

            trace = _v1_ok(c.get("/drive/trace/latest"))
            assert trace["has_trace"] is True
            assert trace["trace"]["operation"] == "move_to_position"


# ===========================================================================
# GAP 3: Degraded startup → health endpoint contract
# ===========================================================================


class TestDegradedModeConsistency:
    """Verify degraded mode is consistently observable through health and data endpoints."""

    def test_degraded_mode_health_and_status_consistency(self, noop_lifecycle):
        """IT-06: drive=None → /ready=503 and /drive/status=503."""
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            main.app.state.drive_last_error = "connection refused"

            r_ready = c.get("/ready")
            assert r_ready.status_code == 503
            body = r_ready.json()
            assert body["driver_connected"] is False

            r_status = c.get("/drive/status")
            err = _v1_err(r_status, 503)
            assert err["code"] == "DRIVE_NOT_INITIALIZED"


# ===========================================================================
# GAP 4: Error propagation fidelity (driver exception → HTTP)
# ===========================================================================


class TestErrorPropagationFidelity:
    """Verify driver exception types map to correct HTTP status + error codes.

    ModbusGatewayException has ZERO existing test coverage at the HTTP layer.
    """

    def test_modbus_gateway_device_failure_maps_to_503(self, noop_lifecycle):
        """IT-07: ModbusGatewayException(DEVICE_FAILURE=0x04) → 503 + MODBUS_GATEWAY_ERROR."""
        exc = ModbusGatewayException(function_code=0xAB, exception_code=0x04)
        with TestClient(main.app) as c:
            _setup_state(main.app, drive=FakeDrive(raise_on_move=exc))
            r = c.post("/drive/move_to_position", json=_MOVE)
            err = _v1_err(r, 503)
            assert err["code"] == "MODBUS_GATEWAY_ERROR"
            assert "DEVICE_FAILURE" in err["message"]

    def test_modbus_illegal_function_gets_distinct_code(self, noop_lifecycle):
        """IT-08: ModbusGatewayException(ILLEGAL_FUNCTION=0x01) → 503 + MODBUS_ILLEGAL_FUNCTION."""
        exc = ModbusGatewayException(function_code=0xAB, exception_code=0x01)
        with TestClient(main.app) as c:
            _setup_state(main.app, drive=FakeDrive(raise_on_move=exc))
            r = c.post("/drive/move_to_position", json=_MOVE)
            err = _v1_err(r, 503)
            assert err["code"] == "MODBUS_ILLEGAL_FUNCTION"

    def test_timeout_maps_to_504(self, noop_lifecycle):
        """IT-09: TimeoutError → 504 + TIMEOUT."""
        with TestClient(main.app) as c:
            _setup_state(main.app, drive=FakeDrive(raise_on_move=TimeoutError("modbus timeout")))
            r = c.post("/drive/move_to_position", json=_MOVE)
            err = _v1_err(r, 504)
            assert err["code"] == "TIMEOUT"

    def test_unclassified_runtime_error_maps_to_500(self, noop_lifecycle):
        """IT-10: RuntimeError → 500 + INTERNAL_ERROR."""
        with TestClient(main.app) as c:
            _setup_state(main.app, drive=FakeDrive(raise_on_move=RuntimeError("unexpected")))
            r = c.post("/drive/move_to_position", json=_MOVE)
            err = _v1_err(r, 500)
            assert err["code"] == "INTERNAL_ERROR"


# ===========================================================================
# GAP 5: Jog lifecycle sequence through HTTP
# ===========================================================================


class TestJogLifecycleSequence:
    """Verify jog start→stop→restart through HTTP doesn't leave stale state."""

    def test_jog_full_lifecycle_start_stop_restart(self, noop_lifecycle):
        """IT-11: jog_start(+) → jog_stop → jog_start(-) → correct driver calls."""
        with TestClient(main.app) as c:
            drive = _setup_state(main.app)

            d1 = _v1_ok(c.post("/drive/jog_start", json=_JOG_POS))
            assert d1["velocity"] > 0
            assert drive._jog_active is True

            _v1_ok(c.post("/drive/jog_stop"))
            assert drive._jog_active is False

            d2 = _v1_ok(c.post("/drive/jog_start", json=_JOG_NEG))
            assert d2["velocity"] < 0
            assert drive._jog_active is True

            ops = [call[0] for call in drive.calls]
            assert ops == ["jog_start", "jog_stop", "jog_start"]

            # Verify direction changed
            assert drive.calls[0][1]["velocity"] > 0
            assert drive.calls[2][1]["velocity"] < 0


# ===========================================================================
# GAP 6: Idle shutdown guard (jog active / low-power state)
# ===========================================================================


@dataclass
class _JogState:
    active: bool


class _FakeJog:
    def __init__(self, active: bool):
        self._active = active

    @property
    def state(self):
        return _JogState(active=self._active)


async def test_idle_shutdown_skips_when_jog_active() -> None:
    """IT-12: _idle_shutdown_action must not call disable_voltage while jog is active.

    Regression test for production bug: keepalive reads interfered with
    disable_voltage on real dryve D1 hardware.
    """
    from drivers.dryve_d1.api.idle_shutdown import IdleShutdownMixin
    from drivers.dryve_d1.od.statusword import CiA402State

    calls: list[str] = []

    class _FakeSM:
        async def current_state(self):
            return CiA402State.OPERATION_ENABLED

    class _Testable(IdleShutdownMixin):
        _idle_shutdown_handle = None
        _idle_shutdown_task = None
        _idle_shutdown_delay_s = 0.0

        def __init__(self, jog_active: bool):
            self._sm = _FakeSM()
            self._jog = _FakeJog(jog_active)

        async def disable_voltage(self):
            calls.append("disable_voltage")

    # Jog active → must NOT disable
    await _Testable(jog_active=True)._idle_shutdown_action()
    assert calls == [], "disable_voltage must not be called when jog is active"

    # Jog inactive → must disable
    await _Testable(jog_active=False)._idle_shutdown_action()
    assert calls == ["disable_voltage"]


async def test_idle_shutdown_skips_when_already_low_power() -> None:
    """IT-13: Idle shutdown skips disable_voltage when already SWITCH_ON_DISABLED."""
    from drivers.dryve_d1.api.idle_shutdown import IdleShutdownMixin
    from drivers.dryve_d1.od.statusword import CiA402State

    called = False

    class _FakeSM:
        async def current_state(self):
            return CiA402State.SWITCH_ON_DISABLED

    class _Testable(IdleShutdownMixin):
        _idle_shutdown_handle = None
        _idle_shutdown_task = None
        _idle_shutdown_delay_s = 0.0
        _jog = None

        def __init__(self):
            self._sm = _FakeSM()

        async def disable_voltage(self):
            nonlocal called
            called = True

    await _Testable()._idle_shutdown_action()
    assert called is False, "disable_voltage must not be called when already in low-power state"
