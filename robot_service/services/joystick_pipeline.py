import asyncio
import logging
import time
from typing import Callable, Dict, Any, Optional

import aiohttp

from services.joystick_interpreter import JoystickInterpreter

_log = logging.getLogger(__name__)


def _ts_to_sec(ts_raw: Any, now_sec: float) -> float:
    """Normalize frame timestamp to seconds. Browser sends Date.now() → ms (~1e12..1e13)."""
    try:
        ts = float(ts_raw)
    except (TypeError, ValueError):
        return now_sec
    if ts > 1e11:
        return ts / 1000.0
    return ts


class JoystickPipeline:
    def __init__(
        self,
        xarm_commands,
        xarm_http=None,
        autotake_func=None,
        autotake_velocity: int = 40,
        safety_layer=None,
        *,
        deadzone: float = 0.12,  # typical 0.05–0.10; 0.12 can eat small stick movements
        default_ttl_ms: int = 150,
        hold_timeout_ms: int = 1000,
        acc: int = 1000,
        is_move_tool: bool = True,
        mode: int = 0,
        queue_maxsize: int = 2,  # low latency: keep only latest; 1–2 avoids 200–400 ms backlog
        symovo_teleop_url: Optional[str] = None,
        symovo_teleop_duration: float = 0.25,
        symovo_linear: float = 0.1,
        symovo_angular: float = 0.5,
        igus_client=None,
        lift_jog_speed: float = 2000.0,
        lift_jog_ttl_ms: int = 200,
        task_busy_check: Optional[Callable[[], bool]] = None,
        safety_lockout_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._xcmd = xarm_commands
        self._xhttp = xarm_http
        self._autotake_func = autotake_func
        self._autotake_velocity = int(autotake_velocity)
        self._safety = safety_layer
        self._deadzone = float(deadzone)
        self._default_ttl_ms = int(default_ttl_ms)
        self._inactivity_timeout_ms = int(hold_timeout_ms)
        self._inactivity_timeout_sec = max(0.05, self._inactivity_timeout_ms / 1000.0)
        # When motion is active, stop within TTL if no frames arrive (avoids moving longer than TTL after release)
        self._motion_idle_threshold_sec = max(0.10, self._inactivity_timeout_sec)
        self._acc = int(acc)
        self._is_move_tool = bool(is_move_tool)
        self._mode = int(mode)
        self._symovo_teleop_url = symovo_teleop_url
        self._symovo_teleop_duration = float(symovo_teleop_duration)
        self._symovo_linear = float(symovo_linear)
        self._symovo_angular = float(symovo_angular)
        self._igus_client = igus_client
        self._lift_jog_speed = float(lift_jog_speed)
        self._lift_jog_ttl_ms = int(lift_jog_ttl_ms)
        self._task_busy_check = task_busy_check
        self._safety_lockout_check = safety_lockout_check

        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # State
        self._prev_buttons: Optional[list[int]] = None
        self._scheduled_stop_task: Optional[asyncio.Task] = None
        self._inactivity_task: Optional[asyncio.Task] = None
        # IMPORTANT: use monotonic time for all timeouts/TTLs.
        # Never rely on remote joystick timestamps (clock skew) or wall-clock
        # time (NTP jumps). This is critical for jog safety.
        self._last_activity_ts: float = time.monotonic()
        self._last_frame_ts: float = time.monotonic()

        # Track loop state to avoid queuing repeated "start loop" commands.
        # Re-sending move_step(is_loop=True) at 20-50 Hz often builds up command
        # backlog in the arm controller and causes motion after button release.
        self._loop_active_direction: Optional[str] = None
        self._motion_active: bool = False
        self._lift_jog_direction: Optional[str] = None

        # Metrics
        self.enqueued_total = 0
        self.dropped_total = 0
        self.processed_total = 0
        self.errors_total = 0
        self.suppressed_total = 0
        self._last_error_log_ts: float = 0.0
        self._error_log_interval_sec: float = 2.0

        self._symovo_session: Optional[aiohttp.ClientSession] = None
        self._frame_seq: int = 0
        self._cmd_seq: int = 0
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

    def _record_error(self, context: str, exc: Exception) -> None:
        self.errors_total += 1
        now = time.time()
        if now - self._last_error_log_ts >= self._error_log_interval_sec:
            _log.warning(
                "joystick_pipeline: %s failed (errors_total=%s, last=%s)",
                context,
                self.errors_total,
                exc,
                exc_info=False,
            )
            self._last_error_log_ts = now

    async def start(self) -> None:
        if self._symovo_teleop_url and self._symovo_session is None:
            self._symovo_session = aiohttp.ClientSession()
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(), name="joystick-pipeline-worker")
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="joystick-heartbeat")
        if self._inactivity_task is None or self._inactivity_task.done():
            self._inactivity_task = asyncio.create_task(self._inactivity_loop(), name="joystick-inactivity")

    async def stop(self) -> None:
        try:
            await self._stop_lift_jog()
        except Exception as e:
            self._record_error("lift_jog_stop", e)
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._record_error("worker_stop", e)
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._record_error("heartbeat_stop", e)
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
        if self._inactivity_task:
            self._inactivity_task.cancel()
            try:
                await self._inactivity_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._record_error("inactivity_stop", e)
        if getattr(self, "_symovo_session", None) is not None:
            try:
                await self._symovo_session.close()
            except Exception as e:
                self._record_error("symovo_session_close", e)
            self._symovo_session = None

    async def submit(self, frame: Dict[str, Any]) -> bool:
        # Best-effort enqueue; drop oldest if full (last-wins)
        try:
            if self._queue.full():
                try:
                    _ = self._queue.get_nowait()
                    self._queue.task_done()
                    self.dropped_total += 1
                except Exception as e:
                    self._record_error("submit_drop", e)
            self._queue.put_nowait(frame)
            self.enqueued_total += 1
            return True
        except Exception as e:
            self._record_error("submit", e)
            return False

    async def _worker_loop(self) -> None:
        try:
            while True:
                frame = await self._queue.get()
                try:
                    await self._process_frame(frame)
                    self._last_activity_ts = time.monotonic()
                except Exception as e:
                    self._record_error("process_frame", e)
                finally:
                    self._queue.task_done()
                    self.processed_total += 1
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._record_error("worker_loop", e)

    async def _heartbeat_loop(self) -> None:
        """Send heartbeat pings when joystick is inactive to keep watchdog alive"""
        try:
            while True:
                await asyncio.sleep(0.5)  # Check more frequently
                now = time.monotonic()
                if now - self._last_activity_ts > 1.5:  # Send heartbeat sooner
                    await self._send_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _process_frame(self, frame: Dict[str, Any]) -> None:
        # All timing decisions MUST be based on local monotonic time.
        now_mono = time.monotonic()

        # Local receive timestamp (set by ingress/scheduler). If absent, assume "now".
        rx_mono = float(frame.get("_rx_mono", now_mono))
        self._last_frame_ts = rx_mono

        # Keep remote ts only for debugging/traceability.
        now_wall = time.time()
        ts_sec = _ts_to_sec(frame.get("ts"), now_wall)
        axes = list(frame.get("axes", []))
        buttons = list(frame.get("buttons", []))
        ttl_ms = int(frame.get("ttl_ms") or frame.get("ttl") or self._default_ttl_ms)

        if len(axes) < 6:
            axes = axes + [0.0] * (6 - len(axes))
        if len(buttons) < 18:
            buttons = buttons + [0] * (18 - len(buttons))
        if self._prev_buttons is None:
            self._prev_buttons = [0] * len(buttons)

        # Gate: suppress joystick during orchestrated tasks (robot_lock held)
        # or when safety lockout is active (E-stop, relay open, etc.)
        _suppress = False
        if self._task_busy_check is not None and self._task_busy_check():
            _suppress = True
        elif self._safety_lockout_check is not None and self._safety_lockout_check():
            _suppress = True

        if _suppress:
            if self._motion_active:
                try:
                    await self._xcmd.move_step_over()
                except Exception:
                    pass
                self._motion_active = False
                self._loop_active_direction = None
            if self._lift_jog_direction is not None:
                try:
                    if self._igus_client:
                        await self._igus_client.jog_stop()
                except Exception:
                    pass
                self._lift_jog_direction = None
            self._prev_buttons = list(buttons)
            self.suppressed_total += 1
            return

        lift_was_pressed = any(
            int(self._prev_buttons[i]) == 1 for i in (8, 10) if i < len(self._prev_buttons)
        )
        lift_is_pressed = any(
            int(buttons[i]) == 1 for i in (8, 10) if i < len(buttons)
        )
        lift_released = lift_was_pressed and not lift_is_pressed

        self._frame_seq += 1
        prev = self._prev_buttons
        edge_down = [i for i in range(min(len(prev), len(buttons))) if prev[i] == 0 and buttons[i] == 1]
        edge_up = [i for i in range(min(len(prev), len(buttons))) if prev[i] == 1 and buttons[i] == 0]
        # Per-frame logs at INFO can easily become the bottleneck and create lag.
        # Keep the raw stream at DEBUG, and log only state transitions at INFO.
        _log.debug(
            "[joy] seq=%d ts=%.3f btns=%s axes=%s edge_DOWN=%s edge_UP=%s",
            self._frame_seq, ts_sec, buttons, axes, edge_down, edge_up,
        )

        # Age based on local receive time.
        age_ms_val = (now_mono - rx_mono) * 1000.0
        if age_ms_val > ttl_ms:
            # Stale frame: if it has no activity and we were moving, stop anyway
            if self._motion_active and not self._frame_has_activity(axes, buttons):
                await self._move_step_over(last_input_age_ms=age_ms_val)
            if self._lift_jog_direction is not None and lift_released:
                await self._stop_lift_jog()
            self._prev_buttons = buttons[:]
            return

        has_activity = self._frame_has_activity(axes, buttons)
        if not has_activity:
            if self._motion_active:
                await self._move_step_over(last_input_age_ms=age_ms_val)
            else:
                self._last_frame_ts = rx_mono
            if self._lift_jog_direction is not None and lift_released:
                await self._stop_lift_jog()
            self._prev_buttons = buttons[:]
            return
        else:
            self._last_frame_ts = rx_mono

        payload = {
            "ts": ts_sec,
            "axes": axes,
            "buttons": buttons,
            "prev_buttons": self._prev_buttons[:],
            "ttl_ms": ttl_ms,
            "age_ms": age_ms_val,
            "_rx_mono": rx_mono,
        }

        await self._interpreter.process_frame(payload)
        self._prev_buttons = buttons[:]

    async def _send_move(
        self,
        direction: str,
        *,
        is_loop: bool,
        ttl_ms: Optional[int] = None,
        last_input_age_ms: Optional[float] = None,
    ) -> None:
        # IMPORTANT: never spam the xArm controller with repeated move_step commands.
        # If we queue move_step at 20-30Hz, the remote controller accumulates a backlog and
        # may continue moving *seconds* after the operator has released the button.
        # Correct jog pattern here: "START (loop) once" + "STOP once" + TTL watchdog.

        reason = "hold_by_ttl" if is_loop else "pressed"
        _log.debug(
            "[pipe] decision move active_direction=%s reason=%s last_input_age_ms=%s",
            direction,
            reason,
            last_input_age_ms if last_input_age_ms is not None else "-",
        )

        # If a loop is already active with the same direction, DO NOT enqueue a new move.
        if is_loop and self._motion_active and self._loop_active_direction == direction:
            return

        # If direction changes while a loop is active: stop first, then start.
        if self._motion_active and self._loop_active_direction and self._loop_active_direction != direction:
            await self._move_step_over(last_input_age_ms=last_input_age_ms)

        # If a one-shot command arrives while a loop is active, stop the loop first.
        if not is_loop and self._motion_active:
            await self._move_step_over(last_input_age_ms=last_input_age_ms)

        self._cmd_seq += 1
        cmd_seq = self._cmd_seq
        result_code = "ok"
        try:
            await self._xcmd.move_step(
                direction,
                acc=self._acc,
                mode=self._mode,
                is_move_tool=self._is_move_tool,
                is_loop=is_loop,
                wait_response=False,
                ttl_ms=ttl_ms,
            )
            if is_loop:
                self._motion_active = True
                self._loop_active_direction = direction
        except Exception as e:
            result_code = str(e)[:80]
            self._record_error("send_move", e)

        # Log only when a command is actually sent.
        _log.info(
            "[xarm] cmd_seq=%d op=%s direction=%s ttl_ms=%s result=%s",
            cmd_seq,
            "loop_start" if is_loop else "step",
            direction,
            ttl_ms if ttl_ms is not None else "-",
            result_code,
        )

    async def _send_heartbeat(self) -> None:
        """Send heartbeat to keep watchdog alive when joystick is inactive"""
        try:
            if self._safety:
                self._safety.heartbeat()
            else:
                await self._xcmd.ping(wait_response=False)
        except Exception as e:
            self._record_error("heartbeat", e)

    def _schedule_step_over(self, ttl_ms: int) -> None:
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()

        async def _stop_after_ttl():
            my_task = asyncio.current_task()
            try:
                delay = max(0.0, float(ttl_ms) / 1000.0)
                await asyncio.sleep(delay)
                # If a newer stop task has been scheduled, do nothing.
                if self._scheduled_stop_task is not my_task:
                    return
                await self._move_step_over()
            except asyncio.CancelledError:
                # Cancellation is expected when new input arrives and we reschedule the TTL.
                raise
            except Exception:
                pass
            finally:
                # Only clear the handle if we are still the currently scheduled stop task.
                if self._scheduled_stop_task is my_task:
                    self._scheduled_stop_task = None

        self._scheduled_stop_task = asyncio.create_task(_stop_after_ttl())

    def _cancel_scheduled_stop(self) -> None:
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
        self._scheduled_stop_task = None

    async def _move_step_over(self, last_input_age_ms: Optional[float] = None) -> None:
        if last_input_age_ms is None:
            # _last_frame_ts holds monotonic time (seconds)
            last_input_age_ms = (time.monotonic() - self._last_frame_ts) * 1000.0
        self._cmd_seq += 1
        cmd_seq = self._cmd_seq
        result_code = "ok"
        try:
            self._cancel_scheduled_stop()
            await self._xcmd.move_step_over(wait_response=False)
        except Exception as e:
            result_code = str(e)[:80]
            self._record_error("move_step_over", e)
        finally:
            self._motion_active = False
            self._loop_active_direction = None
        _log.info(
            "[xarm] cmd_seq=%d direction=stop ttl_ms=- target=- result=%s",
            cmd_seq, result_code,
        )

    async def _safe_stop(self, reason: str) -> None:
        try:
            await self._xcmd.stop(reason)
        except Exception:
            pass

    async def _inactivity_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.05)
                if not self._motion_active:
                    continue
                # Use TTL-based threshold when in motion so robot stops within TTL of last frame (e.g. lost release)
                threshold = self._motion_idle_threshold_sec
                if time.monotonic() - self._last_frame_ts >= threshold:
                    await self._move_step_over()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

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
        if self._task_busy_check is not None and self._task_busy_check():
            return
        if self._safety_lockout_check is not None and self._safety_lockout_check():
            return
        try:
            if not self._xhttp:
                return
            state = getattr(self, "_gripper_closed", False)
            if state:
                await self._xhttp.gripper_drop()
                self._gripper_closed = False
            else:
                await self._xhttp.gripper_take()
                self._gripper_closed = True
        except Exception:
            pass

    async def _call_autotake(self) -> None:
        if self._task_busy_check is not None and self._task_busy_check():
            return
        if self._safety_lockout_check is not None and self._safety_lockout_check():
            return
        try:
            if self._autotake_func is not None:
                await self._autotake_func(self._autotake_velocity)
        except Exception as e:
            self._record_error("autotake", e)

    async def _send_lift_jog(self, direction: str, *, ttl_ms: Optional[int] = None) -> None:
        if self._igus_client is None:
            return
        if direction not in ("up", "down"):
            return

        igus_direction = "positive" if direction == "up" else "negative"
        effective_ttl_ms = int(ttl_ms if ttl_ms is not None else self._lift_jog_ttl_ms)

        try:
            if self._lift_jog_direction is None:
                await self._reset_lift_error_if_needed()
                await self._igus_client.jog_start(
                    direction=igus_direction,
                    ttl_ms=effective_ttl_ms,
                )
                self._lift_jog_direction = direction
                return

            if self._lift_jog_direction == direction:
                await self._igus_client.jog_update(
                    direction=igus_direction,
                    ttl_ms=effective_ttl_ms,
                )
                return

            await self._igus_client.jog_stop()
            await self._reset_lift_error_if_needed()
            await self._igus_client.jog_start(
                direction=igus_direction,
                ttl_ms=effective_ttl_ms,
            )
            self._lift_jog_direction = direction
        except Exception as e:
            self._record_error("lift_jog", e)

    async def _stop_lift_jog(self) -> None:
        if self._igus_client is None or self._lift_jog_direction is None:
            return
        try:
            await self._igus_client.jog_stop()
        except Exception as e:
            self._record_error("lift_jog_stop", e)
        finally:
            self._lift_jog_direction = None

    async def _reset_lift_error_if_needed(self) -> None:
        if self._igus_client is None:
            return

        has_error = False
        try:
            status = await self._igus_client.status()
        except Exception as e:
            self._record_error("lift_status", e)
            return

        if isinstance(status, dict):
            has_error = bool(status.get("error", False))
            if not has_error and isinstance(status.get("fault"), dict):
                has_error = bool(status["fault"].get("active", False))

            data = status.get("data")
            if not has_error and isinstance(data, dict):
                has_error = bool(data.get("error", False))
                if not has_error and isinstance(data.get("fault"), dict):
                    has_error = bool(data["fault"].get("active", False))

        if has_error:
            await self._igus_client.fault_reset()

    async def _send_symovo_motion(
        self, linear_dir: int, angular_dir: int, duration: Optional[float] = None
    ) -> None:
        if not self._symovo_teleop_url:
            return
        if self._task_busy_check is not None and self._task_busy_check():
            return
        if self._safety_lockout_check is not None and self._safety_lockout_check():
            return
        session = getattr(self, "_symovo_session", None)
        if session is None:
            return
        dur = duration if duration is not None else self._symovo_teleop_duration
        body = {
            "linear_dir": int(max(-1, min(1, linear_dir))),
            "angular_dir": int(max(-1, min(1, angular_dir))),
            "duration": dur,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=float(dur) + 3.0)
            async with session.put(
                self._symovo_teleop_url, json=body, timeout=timeout
            ) as _resp:
                pass
        except Exception as e:
            self._record_error("symovo_motion", e)


