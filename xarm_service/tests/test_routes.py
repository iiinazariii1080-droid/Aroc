"""Tests for xarm_service HTTP route handlers (app/routes.py).

Strategy: mock the DI layer (get_command_service) with a fake that returns
configurable CommandResult objects, then hit endpoints via TestClient.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from drivers.xarm_driver.actor.commands import (
    Command,
    CommandResult,
    CommandType,
    ExecutionPolicy,
    ResultStatus,
)


# ── Fake CommandService ────────────────────────────────────────────────────

class FakeCommandService:
    """Records enqueued commands and returns configurable results."""

    def __init__(self, *, default_status: ResultStatus = ResultStatus.SUCCEEDED):
        self.commands: list[Command] = []
        self._default_status = default_status
        self._next_result: CommandResult | None = None

    def set_next_result(self, result: CommandResult) -> None:
        self._next_result = result

    async def enqueue(self, cmd: Command) -> CommandResult:
        self.commands.append(cmd)
        if self._next_result is not None:
            r = self._next_result
            self._next_result = None
            return r
        return CommandResult(
            command_id=cmd.command_id,
            status=self._default_status,
        )


@pytest.fixture
def fake_svc():
    return FakeCommandService()


@pytest.fixture
def client(fake_svc, monkeypatch):
    """Create TestClient with mocked DI and lifespan disabled."""
    # Prevent real hardware init
    monkeypatch.setattr("app.di._command_service", fake_svc)
    monkeypatch.setattr("app.di._state_store", MagicMock())
    monkeypatch.setattr("app.di._readiness_gate", MagicMock())
    monkeypatch.setattr("app.di._actor", MagicMock())
    monkeypatch.setattr("app.state.xarm_lock", asyncio.Lock())

    # Disable real lifespan (hardware init)
    from main import app
    app.router.lifespan_context = _noop_lifespan
    return TestClient(app, raise_server_exceptions=False)


from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def _noop_lifespan(app: FastAPI):
    yield


# ── Route Tests ────────────────────────────────────────────────────────────

class TestMoveJoints:
    def test_change_joints_success(self, client, fake_svc):
        r = client.post("/move/change_joints", json={
            "j1": 10.0, "j2": 20.0, "j3": 30.0,
            "j4": 0.0, "j5": 0.0, "j6": 0.0,
            "velocity_percent": 50.0,
            "reset_faults": False,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert len(fake_svc.commands) == 1
        assert fake_svc.commands[0].type == CommandType.MOVE_JOINTS

    def test_change_joints_rejected(self, client, fake_svc):
        fake_svc.set_next_result(CommandResult(
            command_id="x", status=ResultStatus.REJECTED,
            error_message="Robot busy",
        ))
        r = client.post("/move/change_joints", json={
            "j1": 0, "j2": 0, "j3": 0, "j4": 0, "j5": 0, "j6": 0,
            "velocity_percent": 50.0, "reset_faults": False,
        })
        assert r.status_code == 409


class TestMovePose:
    def test_change_pose_success(self, client, fake_svc):
        r = client.post("/move/change_pose", json={
            "name": "home",
            "velocity_percent": 80.0,
            "reset_faults": False,
        })
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert fake_svc.commands[0].type == CommandType.MOVE_POSE


class TestGripper:
    def test_gripper_take(self, client, fake_svc):
        r = client.post("/gripper/take")
        assert r.status_code == 200
        assert fake_svc.commands[0].type == CommandType.GRIP_CLOSE

    def test_gripper_drop(self, client, fake_svc):
        r = client.post("/gripper/drop")
        assert r.status_code == 200
        assert fake_svc.commands[0].type == CommandType.GRIP_OPEN


class TestRecoverStop:
    def test_recover(self, client, fake_svc):
        r = client.post("/recover")
        assert r.status_code == 200
        assert fake_svc.commands[0].type == CommandType.RECOVER_FAULTS

    def test_stop(self, client, fake_svc):
        r = client.post("/stop")
        assert r.status_code == 200
        assert fake_svc.commands[0].type == CommandType.STOP

    def test_enable_motion(self, client, fake_svc):
        r = client.post("/enable_motion")
        assert r.status_code == 200
        assert fake_svc.commands[0].type == CommandType.ENABLE_MOTION

    def test_disable_motion(self, client, fake_svc):
        r = client.post("/disable_motion")
        assert r.status_code == 200
        assert fake_svc.commands[0].type == CommandType.DISABLE_MOTION


class TestGetStatus:
    def test_current_position(self, client, fake_svc):
        fake_svc.set_next_result(CommandResult(
            command_id="status-1",
            status=ResultStatus.SUCCEEDED,
            telemetry_snapshot={"angles": [0, 0, 0, 0, 0, 0]},
        ))
        r = client.get("/current_position")
        assert r.status_code == 200
        body = r.json()
        assert "name" in body
        assert "joints" in body
        assert set(body["joints"].keys()) == {"j1", "j2", "j3", "j4", "j5", "j6"}

    def test_status_endpoint(self, client, fake_svc):
        fake_svc.set_next_result(CommandResult(
            command_id="status-2",
            status=ResultStatus.SUCCEEDED,
            telemetry_snapshot={
                "connected": True,
                "state": 0,
                "mode": 0,
                "motion_enabled": True,
                "ready": True,
                "fault": False,
                "warn_code": 0,
                "error_code": 0,
                "angles": [1, 2, 3, 4, 5, 6],
                "pose": [0, 0, 0, 0, 0, 0],
            },
        ))
        r = client.get("/status")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["state_code"] == 0
        assert body["has_error"] is False

    def test_status_endpoint_rejected_returns_503(self, client, fake_svc):
        fake_svc.set_next_result(CommandResult(
            command_id="status-3",
            status=ResultStatus.REJECTED,
            error_message="busy",
        ))
        r = client.get("/status")
        assert r.status_code == 503

    def test_status_safety_lockout_hint_true(self, client, fake_svc):
        """state_code=4 + error + motion_disabled → safety_lockout_hint=True."""
        fake_svc.set_next_result(CommandResult(
            command_id="status-lockout",
            status=ResultStatus.SUCCEEDED,
            telemetry_snapshot={
                "connected": True,
                "state": 4,        # error state
                "mode": 0,
                "motion_enabled": False,
                "ready": False,
                "fault": True,
                "warn_code": 0,
                "error_code": 19,  # non-zero error
                "angles": [0, 0, 0, 0, 0, 0],
                "pose": [0, 0, 0, 0, 0, 0],
            },
        ))
        r = client.get("/status")
        assert r.status_code == 200
        assert r.json()["safety_lockout_hint"] is True

    def test_status_safety_lockout_hint_false_normal(self, client, fake_svc):
        """Normal status → safety_lockout_hint=False."""
        fake_svc.set_next_result(CommandResult(
            command_id="status-normal",
            status=ResultStatus.SUCCEEDED,
            telemetry_snapshot={
                "connected": True,
                "state": 2,        # normal running state
                "mode": 0,
                "motion_enabled": True,
                "ready": True,
                "fault": False,
                "warn_code": 0,
                "error_code": 0,
                "angles": [0, 0, 0, 0, 0, 0],
                "pose": [0, 0, 0, 0, 0, 0],
            },
        ))
        r = client.get("/status")
        assert r.status_code == 200
        assert r.json()["safety_lockout_hint"] is False
