import asyncio
import time
import pytest

import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.joystick_pipeline import JoystickPipeline


class FakeXarm:
    def __init__(self):
        self.calls = []

    async def move_step(self, direction: str, acc: int = 0, mode: int = 0, is_move_tool: bool = False, is_loop: bool = False, wait_response: bool = False, timeout: float = 0, ttl_ms=None):
        self.calls.append({
            "type": "move_step",
            "direction": direction,
            "acc": acc,
            "mode": mode,
            "is_move_tool": is_move_tool,
            "is_loop": is_loop,
        })

    async def move_step_over(self, wait_response: bool = False, timeout: float = 0):
        self.calls.append({"type": "move_step_over"})

    async def stop(self, reason: str = "", wait_response: bool = False, timeout: float = 0):
        self.calls.append({"type": "stop", "reason": reason})

    async def ping(self, wait_response: bool = False, timeout: float = 0):
        self.calls.append({"type": "ping"})


class FakeXarmHttp:
    def __init__(self):
        self.calls = []

    async def gripper_take(self):
        self.calls.append({"type": "gripper_take"})

    async def gripper_drop(self):
        self.calls.append({"type": "gripper_drop"})


class FakeIgus:
    def __init__(self, has_error: bool = False):
        self.calls = []
        self.has_error = has_error

    async def status(self):
        self.calls.append({"type": "status"})
        return {"error": self.has_error}

    async def fault_reset(self):
        self.calls.append({"type": "fault_reset"})
        self.has_error = False
        return {"success": True}

    async def jog_start(self, *, direction: str, speed: float | None = None, ttl_ms: int = 200, duration_ms=None):
        self.calls.append(
            {
                "type": "jog_start",
                "direction": direction,
                "speed": speed,
                "ttl_ms": ttl_ms,
                "duration_ms": duration_ms,
            }
        )
        return {"success": True}

    async def jog_update(self, *, direction: str, speed: float | None = None, ttl_ms: int = 200, duration_ms=None):
        self.calls.append(
            {
                "type": "jog_update",
                "direction": direction,
                "speed": speed,
                "ttl_ms": ttl_ms,
                "duration_ms": duration_ms,
            }
        )
        return {"success": True}

    async def jog_stop(self):
        self.calls.append({"type": "jog_stop"})
        return {"success": True}


@pytest.mark.asyncio
async def test_single_click_and_scheduled_stop():
    fake = FakeXarm()
    fake_safety = type('FakeSafety', (), {'heartbeat': lambda self: None})()
    jp = JoystickPipeline(fake, safety_layer=fake_safety, deadzone=0.0, default_ttl_ms=100, acc=1000, is_move_tool=False, mode=0)
    await jp.start()
    try:
        frame = {
            "ts": time.time(),
            "axes": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "buttons": [0]*13 + [1] + [0]*(18-14),  # button13 rising
            "ttl": 100,
        }
        assert await jp.submit(frame) is True

        # Wait for move to be processed
        await asyncio.sleep(0.05)
        # Should have one move_step isLoop=False
        assert any(c.get("type") == "move_step" and c.get("is_loop") is False for c in fake.calls)

        # Wait for scheduled step_over
        await asyncio.sleep(0.15)
        assert any(c.get("type") == "move_step_over" for c in fake.calls)
    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_hold_then_release_immediate_stop():
    fake = FakeXarm()
    fake_safety = type('FakeSafety', (), {'heartbeat': lambda self: None})()
    jp = JoystickPipeline(fake, safety_layer=fake_safety, deadzone=0.0, default_ttl_ms=200, acc=1000, is_move_tool=False, mode=0)
    await jp.start()
    try:
        # First send neutral frame to initialize prev_buttons
        neutral = {"ts": time.time(), "axes": [0.0]*6, "buttons": [0]*18, "ttl": 200}
        assert await jp.submit(neutral)
        await asyncio.sleep(0.01)

        # First hold frame (rising edge -> single click)
        hold1 = {
            "ts": time.time(),
            "axes": [0.0]*6,
            "buttons": [0]*11 + [1] + [0]*(18-12),
            "ttl": 200,
        }
        assert await jp.submit(hold1)
        await asyncio.sleep(0.01)

        # Second hold frame (continuous hold -> loop move)
        hold2 = {
            "ts": time.time(),
            "axes": [0.0]*6,
            "buttons": [0]*11 + [1] + [0]*(18-12),
            "ttl": 200,
        }
        assert await jp.submit(hold2)
        await asyncio.sleep(0.05)
        assert any(c.get("type") == "move_step" and c.get("is_loop") is True for c in fake.calls)

        # Release
        release = {"ts": time.time(), "axes": [0.0]*6, "buttons": [0]*18, "ttl": 200}
        assert await jp.submit(release)
        await asyncio.sleep(0.05)
        # Idle path should send move_step_over immediately
        assert any(c.get("type") == "move_step_over" for c in fake.calls)
    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_stale_frame_ignored():
    fake = FakeXarm()
    fake_safety = type('FakeSafety', (), {'heartbeat': lambda self: None})()
    jp = JoystickPipeline(fake, safety_layer=fake_safety, default_ttl_ms=100)
    await jp.start()
    try:
        stale = {"ts": time.time() - 5, "axes": [0.0]*6, "buttons": [0]*18, "ttl": 100}
        assert await jp.submit(stale)
        await asyncio.sleep(0.05)
        assert fake.calls == []
    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_emergency_stop_button0():
    fake = FakeXarm()
    fake_http = FakeXarmHttp()
    fake_safety = type('FakeSafety', (), {'heartbeat': lambda self: None})()
    jp = JoystickPipeline(fake, xarm_http=fake_http, safety_layer=fake_safety)
    await jp.start()
    try:
        frame = {"ts": time.time(), "axes": [0.0]*6, "buttons": [1] + [0]*17, "ttl": 150}
        assert await jp.submit(frame)
        await asyncio.sleep(0.05)
        # Button 0 should toggle gripper (take on first press)
        assert any(c.get("type") == "gripper_take" for c in fake_http.calls)
    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_heartbeat_when_inactive():
    """Test that heartbeat is sent when joystick is inactive for 2+ seconds"""
    fake = FakeXarm()
    heartbeat_calls = []

    class FakeSafety:
        def __init__(self):
            self.heartbeat_calls = heartbeat_calls

        def heartbeat(self):
            self.heartbeat_calls.append(time.time())

    fake_safety = FakeSafety()
    jp = JoystickPipeline(fake, safety_layer=fake_safety, deadzone=0.0, default_ttl_ms=200)

    await jp.start()
    try:
        # Send one frame to start activity
        frame = {"ts": time.time(), "axes": [0.0]*6, "buttons": [0]*18, "ttl": 200}
        assert await jp.submit(frame)
        await asyncio.sleep(0.1)

        # Wait for heartbeat to trigger (inactive for 2+ seconds)
        await asyncio.sleep(2.5)

        # Should have received heartbeat calls
        assert len(heartbeat_calls) > 0, "Heartbeat should be called when inactive"

        # Check timing - first heartbeat should be after ~1.5 seconds of inactivity
        first_heartbeat = heartbeat_calls[0]
        assert first_heartbeat - frame["ts"] > 1.5, "Heartbeat should trigger after 1.5 seconds of inactivity"

    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_no_heartbeat_when_active():
    """Test that heartbeat is not sent when joystick is active"""
    fake = FakeXarm()
    heartbeat_calls = []

    class FakeSafety:
        def __init__(self):
            self.heartbeat_calls = heartbeat_calls

        def heartbeat(self):
            self.heartbeat_calls.append(time.time())

    fake_safety = FakeSafety()
    jp = JoystickPipeline(fake, safety_layer=fake_safety, deadzone=0.0, default_ttl_ms=200)

    await jp.start()
    try:
        start_time = time.time()

        # Send frames regularly to keep active
        for i in range(5):
            frame = {"ts": time.time(), "axes": [0.0]*6, "buttons": [0]*18, "ttl": 200}
            assert await jp.submit(frame)
            await asyncio.sleep(0.5)  # Send frame every 0.5 seconds

        # Wait a bit more but not enough for heartbeat
        await asyncio.sleep(1.0)

        # Should not have received heartbeat calls since we were active
        assert len(heartbeat_calls) == 0, f"Should not heartbeat when active, but got {len(heartbeat_calls)} calls"

    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_heartbeat_integration():
    """Integration test: joystick pipeline with safety layer heartbeat prevents watchdog timeout"""
    from services.xarm_websocket_client.safety_layer import SafetyLayer

    stop_calls = []
    forwarded_messages = []

    async def fake_send_stop(reason: str):
        stop_calls.append(reason)

    async def fake_forward_command(msg: dict):
        forwarded_messages.append(msg)

    # Create safety layer with longer timeout since heartbeat triggers every 2 seconds
    safety = SafetyLayer(
        send_stop=fake_send_stop,
        forward_command=fake_forward_command,
        watchdog_timeout=5.0,  # Longer timeout for heartbeat to work
        hold_timeout=0.5
    )

    fake_xarm = FakeXarm()
    jp = JoystickPipeline(fake_xarm, safety_layer=safety, deadzone=0.0, default_ttl_ms=200)

    await jp.start()
    try:
        # Send initial frame
        frame1 = {"ts": time.time(), "axes": [0.0]*6, "buttons": [0]*18, "ttl": 200}
        assert await jp.submit(frame1)
        await asyncio.sleep(0.1)

        # Wait for heartbeat to trigger (inactive for 1.5+ seconds)
        await asyncio.sleep(2.0)

        # Should not have stop calls due to heartbeat
        assert len(stop_calls) == 0, f"Watchdog should not trigger due to heartbeat, but got stops: {stop_calls}"

        # Send another frame to reset activity
        frame2 = {"ts": time.time(), "axes": [0.0]*6, "buttons": [0]*18, "ttl": 200}
        assert await jp.submit(frame2)
        await asyncio.sleep(0.1)

        # Wait again for heartbeat
        await asyncio.sleep(2.0)

        # Still should not have stop calls
        assert len(stop_calls) == 0, f"Watchdog should still not trigger, but got stops: {stop_calls}"

    finally:
        await safety.stop()
        await jp.stop()


@pytest.mark.asyncio
async def test_zero_frame_triggers_immediate_stop():
    fake = FakeXarm()
    fake_safety = type("FakeSafety", (), {"heartbeat": lambda self: None})()
    jp = JoystickPipeline(fake, safety_layer=fake_safety, deadzone=0.0, default_ttl_ms=150, hold_timeout_ms=1000)
    await jp.start()
    try:
        neutral = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 18, "ttl": 150}
        assert await jp.submit(neutral)
        await asyncio.sleep(0.01)

        press = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 12 + [1] + [0] * (18 - 13), "ttl": 150}
        assert await jp.submit(press)
        await asyncio.sleep(0.01)

        hold = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 12 + [1] + [0] * (18 - 13), "ttl": 150}
        assert await jp.submit(hold)
        await asyncio.sleep(0.05)
        assert any(call.get("type") == "move_step" for call in fake.calls)

        zero = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 18, "ttl": 150}
        assert await jp.submit(zero)
        await asyncio.sleep(0.05)
        stop_calls = [c for c in fake.calls if c.get("type") == "move_step_over"]
        assert stop_calls, "zero frame should trigger immediate move_step_over"

        prev_count = len(stop_calls)
        assert await jp.submit(zero)
        await asyncio.sleep(0.05)
        stop_calls = [c for c in fake.calls if c.get("type") == "move_step_over"]
        assert len(stop_calls) == prev_count, "repeated zero frames should not spam stop commands"
    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_inactivity_timer_restarts_with_active_frames():
    fake = FakeXarm()
    fake_safety = type("FakeSafety", (), {"heartbeat": lambda self: None})()
    jp = JoystickPipeline(fake, safety_layer=fake_safety, deadzone=0.0, default_ttl_ms=150, hold_timeout_ms=1000)
    await jp.start()
    try:
        assert await jp.submit({"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 18, "ttl": 150})
        await asyncio.sleep(0.02)
        assert await jp.submit({"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 12 + [1] + [0] * (18 - 13), "ttl": 150})
        await asyncio.sleep(0.02)

        for _ in range(3):
            hold_frame = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 12 + [1] + [0] * (18 - 13), "ttl": 150}
            assert await jp.submit(hold_frame)
            await asyncio.sleep(0.3)

        assert not any(call.get("type") == "move_step_over" for call in fake.calls), "inactivity timer should reset on active frames"

        await asyncio.sleep(1.2)
        assert any(call.get("type") == "move_step_over" for call in fake.calls), "timer should stop robot after 1s of silence"
    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_lift_jog_up_with_fault_reset_on_button10():
    fake = FakeXarm()
    fake_safety = type("FakeSafety", (), {"heartbeat": lambda self: None})()
    fake_igus = FakeIgus(has_error=True)
    jp = JoystickPipeline(
        fake,
        safety_layer=fake_safety,
        igus_client=fake_igus,
        lift_jog_speed=1800,
        lift_jog_ttl_ms=220,
        default_ttl_ms=220,
    )
    await jp.start()
    try:
        neutral = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 18, "ttl": 220}
        assert await jp.submit(neutral)
        await asyncio.sleep(0.02)

        press_up = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 10 + [1] + [0] * (18 - 11), "ttl": 220}
        assert await jp.submit(press_up)
        await asyncio.sleep(0.05)

        assert any(call.get("type") == "status" for call in fake_igus.calls)
        assert any(call.get("type") == "fault_reset" for call in fake_igus.calls)
        assert any(
            call.get("type") == "jog_start" and call.get("direction") == "positive"
            for call in fake_igus.calls
        )
    finally:
        await jp.stop()


@pytest.mark.asyncio
async def test_lift_jog_release_sends_jog_stop():
    fake = FakeXarm()
    fake_safety = type("FakeSafety", (), {"heartbeat": lambda self: None})()
    fake_igus = FakeIgus(has_error=False)
    jp = JoystickPipeline(fake, safety_layer=fake_safety, igus_client=fake_igus, default_ttl_ms=200)
    await jp.start()
    try:
        neutral = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 18, "ttl": 200}
        assert await jp.submit(neutral)
        await asyncio.sleep(0.02)

        press_down = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 8 + [1] + [0] * (18 - 9), "ttl": 200}
        assert await jp.submit(press_down)
        await asyncio.sleep(0.05)

        release = {"ts": time.time(), "axes": [0.0] * 6, "buttons": [0] * 18, "ttl": 200}
        assert await jp.submit(release)
        await asyncio.sleep(0.05)

        assert any(call.get("type") == "jog_start" and call.get("direction") == "negative" for call in fake_igus.calls)
        assert any(call.get("type") == "jog_stop" for call in fake_igus.calls)
    finally:
        await jp.stop()


