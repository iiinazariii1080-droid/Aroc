import asyncio
import time

import pytest

from manipulator_commands import ManipulatorCommands


@pytest.mark.asyncio
async def test_manipulator_commands_build_and_send():
    sent_payloads = []

    async def fake_sender(payload):
        sent_payloads.append(payload)

    commands = ManipulatorCommands(fake_sender, user_id="test", version="xarm6")

    # send move_step
    await commands.move_step(
        direction="attitude-pitch-decrease",
        acc=500,
        mode=0,
        is_move_tool=True,
    )

    # send move_step_over
    await commands.move_step_over()

    # send stop
    await commands.stop("manual_test")

    assert len(sent_payloads) == 3

    # 1) move_step payload
    p0 = sent_payloads[0]
    assert p0["type"] == "request"
    assert p0["cmd"] == "xarm_move_step"
    assert p0["id"] == "0"
    assert p0["data"]["userId"] == "test"
    assert p0["data"]["version"] == "xarm6"
    assert isinstance(p0["data"]["ts"], float)
    assert p0["data"]["direction"] == "attitude-pitch-decrease"
    assert p0["data"]["isLoop"] is True
    assert p0["data"]["isMoveTool"] is True
    assert p0["data"]["acc"] == 500
    assert p0["data"]["mode"] == 0

    # 2) move_step_over
    p1 = sent_payloads[1]
    assert p1["type"] == "request"
    assert p1["cmd"] == "xarm_move_step_over"
    assert p1["id"] == "1"
    assert p1["data"]["userId"] == "test"
    assert isinstance(p1["data"]["ts"], float)

    # 3) stop
    p2 = sent_payloads[2]
    assert p2["type"] == "request"
    assert p2["cmd"] == "emergency_stop"
    assert p2["id"] == "2"
    assert p2["data"]["reason"] == "manual_test"
    assert isinstance(p2["data"]["ts"], float)


