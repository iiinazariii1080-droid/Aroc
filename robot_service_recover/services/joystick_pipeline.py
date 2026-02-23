import asyncio
import time
import logging
from typing import Dict, Any, Optional

import aiohttp

from services.joystick_interpreter import JoystickInterpreter


class JoystickPipeline:
    def __init__(
        self,
        xarm_commands,
        xarm_http=None,
        autotake_func=None,
        autotake_velocity: int = 40,
        safety_layer=None,
        *,
        deadzone: float = 0.12,
        default_ttl_ms: int = 150,
        hold_timeout_ms: int = 1000,
        acc: int = 1000,
        is_move_tool: bool = True,
        mode: int = 0,
        queue_maxsize: int = 256,
        symovo_teleop_url: Optional[str] = None,
        symovo_teleop_duration: float = 0.25,
        symovo_linear: float = 0.1,
        symovo_angular: float = 0.5,
    ) -> None:
        self._log = logging.getLogger(__name__)
        self._xcmd = xarm_commands
        self._xhttp = xarm_http
        self._autotake_func = autotake_func
        self._autotake_velocity = int(autotake_velocity)
        self._safety = safety_layer
        self._deadzone = float(deadzone)
        self._default_ttl_ms = int(default_ttl_ms)
        self._inactivity_timeout_ms = int(hold_timeout_ms)
        self._inactivity_timeout_sec = max(0.05, self._inactivity_timeout_ms / 1000.0)
        self._acc = int(acc)
        self._is_move_tool = bool(is_move_tool)
        self._mode = int(mode)
        self._symovo_teleop_url = symovo_teleop_url
        self._symovo_teleop_duration = float(symovo_teleop_duration)
        self._symovo_linear = float(symovo_linear)
        self._symovo_angular = float(symovo_angular)

        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # State
        self._prev_buttons: Optional[list[int]] = None
        self._scheduled_stop_task: Optional[asyncio.Task] = None
        self._inactivity_task: Optional[asyncio.Task] = None
        self._last_activity_ts: float = time.time()
        self._last_frame_ts: float = time.time()
        self._motion_active: bool = False

        # Metrics
        self.enqueued_total = 0
        self.dropped_total = 0
        self.processed_total = 0

        self._interpreter = JoystickInterpreter(self, deadzone=self._deadzone)

    def get_symovo_teleop_config(self) -> Dict[str, float]:
        """Current AGV teleop parameters (duration, linear, angular) for API."""
        return {
            "duration": self._symovo_teleop_duration,
            "linear_m_s": self._symovo_linear,
            "angular_rad_s": self._symovo_angular,
        }

    def update_symovo_teleop_config(
        self,
        duration: Optional[float] = None,
        linear_m_s: Optional[float] = None,
        angular_rad_s: Optional[float] = None,
    ) -> None:
        """Update teleop parameters (only provided fields). Values must already be valid per API ranges."""
        if duration is not None:
            self._symovo_teleop_duration = float(duration)
        if linear_m_s is not None:
            self._symovo_linear = float(linear_m_s)
        if angular_rad_s is not None:
            self._symovo_angular = float(angular_rad_s)

    async def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(), name="joystick-pipeline-worker")
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="joystick-heartbeat")
        if self._inactivity_task is None or self._inactivity_task.done():
            self._inactivity_task = asyncio.create_task(self._inactivity_loop(), name="joystick-inactivity")

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except Exception:
                pass
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except Exception:
                pass
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
        if self._inactivity_task:
            self._inactivity_task.cancel()
            try:
                await self._inactivity_task
            except Exception:
                pass
        if self._inactivity_task and not self._inactivity_task.done():
            self._inactivity_task.cancel()

    def submit(self, frame: Dict[str, Any]) -> bool:
        # Best-effort enqueue; drop oldest if full (last-wins)
        try:
            if self._queue.full():
                try:
                    _ = self._queue.get_nowait()
                    self._queue.task_done()
                    self.dropped_total += 1
                    self._log.debug("joystick_pipeline: queue full -> dropped oldest (dropped_total=%s)", self.dropped_total)
                except Exception:
                    pass
            self._queue.put_nowait(frame)
            self.enqueued_total += 1
            return True
        except Exception as e:
            self._log.warning("joystick_pipeline: submit failed: %s", e)
            return False

    async def _worker_loop(self) -> None:
        self._log.info("joystick_pipeline: worker started")
        try:
            while True:
                frame = await self._queue.get()
                try:
                    await self._process_frame(frame)
                    self._last_activity_ts = time.time()
                except Exception as e:
                    self._log.exception("joystick_pipeline: processing error: %s", e)
                finally:
                    self._queue.task_done()
                    self.processed_total += 1
        except asyncio.CancelledError:
            self._log.info("joystick_pipeline: worker cancelled")
        except Exception as e:
            self._log.warning("joystick_pipeline: worker crashed: %s", e)

    async def _heartbeat_loop(self) -> None:
        """Send heartbeat pings when joystick is inactive to keep watchdog alive"""
        self._log.debug("joystick_pipeline: heartbeat loop started")
        try:
            while True:
                await asyncio.sleep(0.5)  # Check more frequently
                now = time.time()
                if now - self._last_activity_ts > 1.5:  # Send heartbeat sooner
                    await self._send_heartbeat()
        except asyncio.CancelledError:
            self._log.debug("joystick_pipeline: heartbeat loop cancelled")
        except Exception as e:
            self._log.warning("joystick_pipeline: heartbeat loop crashed: %s", e)

    async def _process_frame(self, frame: Dict[str, Any]) -> None:
        now = time.time()
        self._last_frame_ts = now
        ts = float(frame.get("ts", now))
        axes = list(frame.get("axes", []))
        buttons = list(frame.get("buttons", []))
        ttl_ms = int(frame.get("ttl", self._default_ttl_ms))

        age_ms = (now - ts) * 1000.0
        if age_ms > ttl_ms:
            self._log.debug("joystick_pipeline: stale frame dropped (age_ms=%.1f > ttl_ms=%s)", age_ms, ttl_ms)
            return

        if len(axes) < 6:
            axes = axes + [0.0] * (6 - len(axes))
        if len(buttons) < 18:
            buttons = buttons + [0] * (18 - len(buttons))

        if self._prev_buttons is None:
            self._prev_buttons = [0] * len(buttons)

        has_activity = self._frame_has_activity(axes, buttons)
        if not has_activity:
            if self._motion_active:
                self._log.info("joystick_pipeline: zero frame -> move_step_over")
                await self._move_step_over()
            else:
                self._last_frame_ts = now
            self._prev_buttons = buttons[:]
            return
        else:
            self._last_frame_ts = now

        payload = {
            "ts": ts,
            "axes": axes,
            "buttons": buttons,
            "prev_buttons": self._prev_buttons[:],
            "ttl_ms": ttl_ms,
        }

        await self._interpreter.process_frame(payload)
        self._prev_buttons = buttons[:]

    async def _send_move(self, direction: str, *, is_loop: bool) -> None:
        try:
            self._motion_active = True
            await self._xcmd.move_step(
                direction,
                acc=self._acc,
                mode=self._mode,
                is_move_tool=self._is_move_tool,
                is_loop=is_loop,
                wait_response=False,
            )
        except Exception as e:
            self._log.exception("joystick_pipeline: move_step failed: %s", e)

    async def _send_heartbeat(self) -> None:
        """Send heartbeat to keep watchdog alive when joystick is inactive"""
        try:
            if self._safety:
                self._safety.heartbeat()
                self._log.debug("joystick_pipeline: heartbeat sent to safety layer")
            else:
                # Fallback to ping if no safety layer
                await self._xcmd.ping(wait_response=False)
        except Exception as e:
            self._log.debug("joystick_pipeline: heartbeat failed: %s", e)

    def _schedule_step_over(self, ttl_ms: int) -> None:
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._log.debug("joystick_pipeline: cancel previous scheduled stop task")
            self._scheduled_stop_task.cancel()

        async def _stop_after_ttl():
            try:
                delay = max(0.0, float(ttl_ms) / 1000.0)
                self._log.debug("joystick_pipeline: schedule move_step_over in %.3fs", delay)
                await asyncio.sleep(delay)
                self._log.info("joystick_pipeline: executing scheduled move_step_over")
                await self._move_step_over()
            except Exception:
                pass
            finally:
                self._scheduled_stop_task = None

        self._scheduled_stop_task = asyncio.create_task(_stop_after_ttl())

    def _cancel_scheduled_stop(self) -> None:
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
        self._scheduled_stop_task = None

    async def _move_step_over(self) -> None:
        try:
            self._cancel_scheduled_stop()
            self._log.debug("joystick_pipeline: idle -> move_step_over")
            await self._xcmd.move_step_over(wait_response=False)
        except Exception as e:
            self._log.exception("joystick_pipeline: move_step_over failed: %s", e)
        finally:
            self._motion_active = False

    async def _safe_stop(self, reason: str) -> None:
        try:
            await self._xcmd.stop(reason)
        except Exception as e:
            self._log.exception("joystick_pipeline: stop failed: %s", e)

    async def _inactivity_loop(self) -> None:
        self._log.debug("joystick_pipeline: inactivity loop started")
        try:
            while True:
                await asyncio.sleep(0.05)
                if not self._motion_active:
                    continue
                if time.time() - self._last_frame_ts >= self._inactivity_timeout_sec:
                    self._log.info("joystick_pipeline: inactivity timeout -> move_step_over")
                    await self._move_step_over()
        except asyncio.CancelledError:
            self._log.debug("joystick_pipeline: inactivity loop cancelled")
        except Exception as exc:
            self._log.warning("joystick_pipeline: inactivity loop crashed: %s", exc)

    def is_running(self) -> bool:
        try:
            return bool(self._worker_task) and not self._worker_task.done()
        except Exception:
            return False

    def queue_size(self) -> int:
        try:
            return self._queue.qsize()
        except Exception:
            return 0

    def _frame_has_activity(self, axes: list[float], buttons: list[int]) -> bool:
        axis_active = any(abs(float(val)) > self._deadzone for val in axes)
        button_active = any(bool(b) for b in buttons)
        return axis_active or button_active

    async def _toggle_gripper(self) -> None:
        try:
            if not self._xhttp:
                self._log.warning("joystick_pipeline: xarm_http unavailable; cannot toggle gripper")
                return
            # Query is not available; keep internal state
            state = getattr(self, "_gripper_closed", False)
            if state:
                await self._xhttp.gripper_drop()
                self._gripper_closed = False
                self._log.info("joystick_pipeline: gripper -> drop")
            else:
                await self._xhttp.gripper_take()
                self._gripper_closed = True
                self._log.info("joystick_pipeline: gripper -> take")
        except Exception as e:
            self._log.exception("joystick_pipeline: toggle_gripper failed: %s", e)

    async def _call_autotake(self) -> None:
        try:
            if self._autotake_func is not None:
                await self._autotake_func(self._autotake_velocity)
                self._log.info("joystick_pipeline: robot_scripts.autotake called")
                return
            self._log.warning("joystick_pipeline: no autotake handler available")
        except Exception as e:
            self._log.exception("joystick_pipeline: autotake failed: %s", e)

    async def _send_symovo_speed(
        self, speed: float, angular_speed: float, duration: Optional[float] = None
    ) -> None:
        if not self._symovo_teleop_url:
            self._log.debug("joystick_pipeline: symovo teleop disabled (no URL)")
            return
        dur = duration if duration is not None else self._symovo_teleop_duration
        body = {"speed": speed, "angular_speed": angular_speed, "duration": dur}
        try:
            timeout = aiohttp.ClientTimeout(total=float(dur) + 3.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.put(
                    self._symovo_teleop_url, json=body
                ) as resp:
                    if resp.status >= 400:
                        self._log.warning(
                            "joystick_pipeline: symovo teleop PUT %s -> %s",
                            self._symovo_teleop_url,
                            resp.status,
                        )
                    else:
                        self._log.debug(
                            "joystick_pipeline: symovo teleop speed=%.2f angular=%.2f -> %s",
                            speed,
                            angular_speed,
                            resp.status,
                        )
        except Exception as e:
            self._log.warning("joystick_pipeline: symovo teleop request failed: %s", e)


