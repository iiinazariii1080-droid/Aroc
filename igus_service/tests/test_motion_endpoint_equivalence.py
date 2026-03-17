from __future__ import annotations

from fastapi.testclient import TestClient

import main
from app.events import EventType
from tests.fakes import AsyncNoopLock, FakeDrive, FakeEventBus, set_app_state


def _set_state(app) -> FakeDrive:
    bus = FakeEventBus()
    drive = FakeDrive()
    set_app_state(app, drive=drive, event_bus=bus, motor_lock=AsyncNoopLock())
    return drive


def test_move_legacy_and_v1_use_equivalent_driver_calls(noop_lifecycle) -> None:
    drive = _set_state(main.app)

    with TestClient(main.app) as client:
        legacy_resp = client.post(
            "/move",
            json={
                "position": 25000,
                "velocity_percent": 50,
                "acceleration_percent": 50,
            },
        )
        v1_resp = client.post(
            "/drive/move_to_position",
            json={
                "target_position": 25000,
                "relative": False,
                "profile": {
                    "velocity": 5000,
                    "acceleration": 2500,
                    "deceleration": 2500,
                },
                "timeout_ms": 30000,
            },
        )

    assert legacy_resp.status_code == 200
    assert legacy_resp.json()["success"] is True
    assert v1_resp.status_code == 200
    assert v1_resp.json()["ok"] is True

    move_calls = [c for c in drive.calls if c[0] == "move_to_position"]
    assert len(move_calls) == 2
    assert move_calls[0][1]["target_position"] == move_calls[1][1]["target_position"] == 25000
    assert move_calls[0][1]["velocity"] == move_calls[1][1]["velocity"] == 5000
    assert move_calls[0][1]["accel"] == move_calls[1][1]["accel"] == 2500
    assert move_calls[0][1]["decel"] == move_calls[1][1]["decel"] == 2500


def test_reference_and_fault_reset_legacy_and_v1_equivalent(noop_lifecycle) -> None:
    drive = _set_state(main.app)

    with TestClient(main.app) as client:
        legacy_ref = client.post("/reference")
        v1_ref = client.post("/drive/reference", json={"timeout_ms": 60000})

        legacy_fault = client.post("/fault_reset")
        v1_fault = client.post(
            "/drive/fault_reset",
            json={"auto_enable": True},
        )

    assert legacy_ref.status_code == 200
    assert legacy_ref.json()["success"] is True
    assert v1_ref.status_code == 200
    assert v1_ref.json()["ok"] is True

    assert legacy_fault.status_code == 200
    assert legacy_fault.json()["success"] is True
    assert v1_fault.status_code == 200
    assert v1_fault.json()["ok"] is True

    home_calls = [c for c in drive.calls if c[0] == "home"]
    fault_calls = [c for c in drive.calls if c[0] == "fault_reset"]
    assert len(home_calls) == 2
    assert len(fault_calls) == 2
    assert all(c[1].get("recover") is True for c in fault_calls)


def test_v1_command_endpoints_emit_command_id_but_status_does_not(noop_lifecycle) -> None:
    _set_state(main.app)

    with TestClient(main.app) as client:
        move_resp = client.post(
            "/drive/move_to_position",
            json={
                "target_position": 12345,
                "relative": False,
                "profile": {
                    "velocity": 5000,
                    "acceleration": 2500,
                    "deceleration": 2500,
                },
                "timeout_ms": 30000,
            },
        )
        stop_resp = client.post("/drive/stop", json={"mode": "quick_stop", "timeout_ms": 5000})
        status_resp = client.get("/drive/status")

    assert move_resp.status_code == 200
    assert stop_resp.status_code == 200
    assert status_resp.status_code == 200

    move_cmd_id = move_resp.json()["meta"]["command_id"]
    stop_cmd_id = stop_resp.json()["meta"]["command_id"]
    status_cmd_id = status_resp.json()["meta"]["command_id"]

    assert isinstance(move_cmd_id, str)
    assert len(move_cmd_id) > 0
    assert isinstance(stop_cmd_id, str)
    assert len(stop_cmd_id) > 0
    assert move_cmd_id != stop_cmd_id
    assert status_cmd_id is None

    command_events = [
        payload
        for event_type, payload in main.app.state.event_bus.published
        if event_type == EventType.COMMAND
    ]
    assert len(command_events) >= 2

    event_by_op = {event["operation"]: event for event in command_events}
    assert event_by_op["move_to_position"]["command_id"] == move_cmd_id
    assert event_by_op["stop"]["command_id"] == stop_cmd_id
    assert isinstance(event_by_op["move_to_position"]["op_id"], str)
    assert len(event_by_op["move_to_position"]["op_id"]) > 0
    assert isinstance(event_by_op["stop"]["op_id"], str)
    assert len(event_by_op["stop"]["op_id"]) > 0


def test_legacy_command_endpoints_emit_command_id_and_publish_events(noop_lifecycle) -> None:
    _set_state(main.app)

    with TestClient(main.app) as client:
        move_resp = client.post(
            "/move",
            json={
                "position": 15000,
                "velocity_percent": 50,
                "acceleration_percent": 50,
            },
        )
        ref_resp = client.post("/reference")

    assert move_resp.status_code == 200
    assert ref_resp.status_code == 200

    move_cmd_id = move_resp.json()["command_id"]
    ref_cmd_id = ref_resp.json()["command_id"]
    assert isinstance(move_cmd_id, str)
    assert len(move_cmd_id) > 0
    assert isinstance(ref_cmd_id, str)
    assert len(ref_cmd_id) > 0
    assert move_cmd_id != ref_cmd_id

    command_events = [
        payload
        for event_type, payload in main.app.state.event_bus.published
        if event_type == EventType.COMMAND
    ]
    event_by_op = {event["operation"]: event for event in command_events}
    assert event_by_op["move_to_position"]["command_id"] == move_cmd_id
    assert event_by_op["reference"]["command_id"] == ref_cmd_id
    assert isinstance(event_by_op["move_to_position"]["op_id"], str)
    assert len(event_by_op["move_to_position"]["op_id"]) > 0
    assert isinstance(event_by_op["reference"]["op_id"], str)
    assert len(event_by_op["reference"]["op_id"]) > 0


def test_latest_trace_endpoint_reports_empty_and_then_updates(noop_lifecycle) -> None:
    _set_state(main.app)
    main.app.state.latest_command_trace = None

    with TestClient(main.app) as client:
        empty_resp = client.get("/drive/trace/latest")
        assert empty_resp.status_code == 200
        empty_data = empty_resp.json()["data"]
        assert empty_data["has_trace"] is False
        assert empty_data["trace"] is None

        move_resp = client.post(
            "/drive/move_to_position",
            json={
                "target_position": 12345,
                "relative": False,
                "profile": {
                    "velocity": 5000,
                    "acceleration": 2500,
                    "deceleration": 2500,
                },
                "timeout_ms": 30000,
            },
        )
        assert move_resp.status_code == 200
        move_cmd_id = move_resp.json()["meta"]["command_id"]

        traced_resp = client.get("/drive/trace/latest")
        assert traced_resp.status_code == 200
        trace_data = traced_resp.json()["data"]
        assert trace_data["has_trace"] is True
        trace = trace_data["trace"]
        assert trace["command_id"] == move_cmd_id
        assert trace["operation"] == "move_to_position"
        assert isinstance(trace["op_id"], str)
        assert len(trace["op_id"]) > 0

        legacy_resp = client.post(
            "/move",
            json={
                "position": 5000,
                "velocity_percent": 50,
                "acceleration_percent": 50,
            },
        )
        assert legacy_resp.status_code == 200
        legacy_cmd_id = legacy_resp.json()["command_id"]

        legacy_trace_resp = client.get("/drive/trace/latest")
        assert legacy_trace_resp.status_code == 200
        legacy_trace = legacy_trace_resp.json()["data"]["trace"]
        assert legacy_trace["command_id"] == legacy_cmd_id
        assert legacy_trace["operation"] == "move_to_position"


def test_failed_command_updates_trace_with_error(noop_lifecycle) -> None:
    """D7 regression: a failed command must publish to latest_command_trace.

    Before D7 fix, publish_command_trace_event was only called on success.
    Verify that after a 409 DRIVE_IN_FAULT, the trace is updated rather than
    remaining stale from any prior success.
    """
    drive = FakeDrive(fault_mode=True)  # get_status_live returns fault=True
    set_app_state(main.app, drive=drive, motor_lock=AsyncNoopLock())
    main.app.state.latest_command_trace = None

    with TestClient(main.app) as client:
        resp = client.post(
            "/drive/move_to_position",
            json={
                "target_position": 1000,
                "relative": False,
                "profile": {"velocity": 100, "acceleration": 50, "deceleration": 50},
                "timeout_ms": 5000,
            },
        )

    assert resp.status_code == 409, "Expected DRIVE_IN_FAULT"
    trace = main.app.state.latest_command_trace
    assert trace is not None, "latest_command_trace must be updated even on failure (D7)"
    assert trace["operation"] == "move_to_position"
    assert isinstance(trace["command_id"], str)
    assert len(trace["command_id"]) > 0


def test_metrics_report_latest_trace_presence_and_age(noop_lifecycle) -> None:
    _set_state(main.app)
    main.app.state.latest_command_trace = None

    with TestClient(main.app) as client:
        before_metrics = client.get("/metrics")
        assert before_metrics.status_code == 200
        before_text = before_metrics.text
        assert "igus_drive_latest_command_trace_present 0" in before_text
        assert "igus_drive_latest_command_trace_age_seconds -1.000" in before_text

        cmd_resp = client.post(
            "/drive/stop",
            json={"mode": "quick_stop", "timeout_ms": 5000},
        )
        assert cmd_resp.status_code == 200

        after_metrics = client.get("/metrics")
        assert after_metrics.status_code == 200
        after_text = after_metrics.text
        assert "igus_drive_latest_command_trace_present 1" in after_text
        age_line = next(
            line
            for line in after_text.splitlines()
            if line.startswith("igus_drive_latest_command_trace_age_seconds ")
        )
        age_value = float(age_line.split(" ", 1)[1])
        assert age_value >= 0.0


