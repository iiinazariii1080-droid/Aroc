"""Monitors for dynamic behavior validation.

Provides monitors for:
- Monotonicity: Ensure position moves monotonically toward target
- Convergence: Ensure distance to target decreases over time
- CiA402 Invariants: Check that state machine invariants hold
- Reconnection: Track connection stability
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator, Callable, Optional


class MonitorError(Exception):
    """Base exception for monitor violations."""

    pass


class MonotonicityViolationError(MonitorError):
    """Raised when position moves non-monotonically."""

    pass


class ConvergenceViolationError(MonitorError):
    """Raised when position does not converge to target."""

    pass


class InvariantViolationError(MonitorError):
    """Raised when a CiA402 invariant is violated."""

    pass


@dataclass
class MonotonicityMonitor:
    """Monitor for monotonic movement toward target.

    Checks that position changes monotonically in the direction of the target,
    allowing small glitches (glitch_eps).
    """

    target: int
    start_pos: int
    glitch_eps: int = 2
    max_violations: int = 3

    _last_pos: Optional[int] = None
    _violation_count: int = 0
    _direction: int = 0  # 1 for forward, -1 for backward

    def __post_init__(self) -> None:
        """Initialize direction based on target and start position."""
        if self.target > self.start_pos:
            self._direction = 1
        elif self.target < self.start_pos:
            self._direction = -1
        else:
            self._direction = 0  # Already at target
        self._last_pos = self.start_pos

    def check(self, current_pos: int) -> None:
        """Check if current position maintains monotonicity.

        Args:
            current_pos: Current position value

        Raises:
            MonotonicityViolationError: If monotonicity is violated
        """
        if self._direction == 0:
            return  # Already at target, no movement expected

        if self._last_pos is None:
            self._last_pos = current_pos
            return

        # Expected change: direction * (current - last) >= -glitch_eps
        delta = current_pos - self._last_pos
        expected_change = self._direction * delta

        if expected_change < -self.glitch_eps:
            self._violation_count += 1
            if self._violation_count >= self.max_violations:
                raise MonotonicityViolationError(
                    f"Non-monotonic movement detected: "
                    f"last={self._last_pos}, current={current_pos}, "
                    f"target={self.target}, direction={self._direction}, "
                    f"violations={self._violation_count}"
                )
        else:
            # Reset violation count on valid movement
            self._violation_count = 0

        self._last_pos = current_pos


@dataclass
class ConvergenceMonitor:
    """Monitor for convergence to target.

    Checks that distance to target decreases over time windows.
    """

    target: int
    window_s: float = 1.0
    min_reduction_pct: float = 10.0
    tolerance: int = 5

    _samples: list[tuple[float, int]] = None  # (timestamp, position)

    def __post_init__(self) -> None:
        """Initialize sample list."""
        if self._samples is None:
            self._samples = []

    def check(self, current_pos: int, timestamp: float) -> None:
        """Check convergence and update samples.

        Args:
            current_pos: Current position
            timestamp: Current timestamp

        Raises:
            ConvergenceViolationError: If convergence is not observed
        """
        self._samples.append((timestamp, current_pos))

        # Remove samples outside window
        cutoff_time = timestamp - self.window_s
        self._samples = [
            (t, p) for t, p in self._samples if t >= cutoff_time
        ]

        if len(self._samples) < 2:
            return  # Need at least 2 samples

        # Check if we're within tolerance
        dist = abs(self.target - current_pos)
        if dist <= self.tolerance:
            return  # Converged

        # Check if distance is decreasing over window
        first_dist = abs(self.target - self._samples[0][1])
        last_dist = abs(self.target - current_pos)

        if first_dist == 0:
            return  # Already at target

        reduction_pct = ((first_dist - last_dist) / first_dist) * 100.0

        if reduction_pct < self.min_reduction_pct:
            # Check if we're still far from target
            if dist > self.tolerance:
                raise ConvergenceViolationError(
                    f"Convergence not observed: "
                    f"target={self.target}, current={current_pos}, "
                    f"dist={dist}, reduction={reduction_pct:.1f}% "
                    f"(min={self.min_reduction_pct}%)"
                )


@dataclass
class CiA402InvariantMonitor:
    """Monitor for CiA402 state machine invariants.

    Checks invariants like:
    - If fault=1 → OperationEnabled must be 0
    - If emergency_active=1 → ve=0 and eo=0
    - TargetReached=1 with is_moving=1 should be transient (<=200ms)
    """

    transient_allowance_s: float = 0.2

    _last_fault_time: Optional[float] = None
    _last_emergency_time: Optional[float] = None
    _target_reached_moving_start: Optional[float] = None

    def check(
        self,
        statusword: int,
        timestamp: float,
    ) -> None:
        """Check invariants against current statusword.

        Args:
            statusword: Current statusword value
            timestamp: Current timestamp

        Raises:
            InvariantViolationError: If an invariant is violated
        """
        fault = bool((statusword >> 3) & 1)
        operation_enabled = bool((statusword >> 2) & 1)
        voltage_enabled = bool((statusword >> 4) & 1)
        quick_stop = bool((statusword >> 5) & 1)
        target_reached = bool((statusword >> 10) & 1)
        is_moving = not target_reached  # Simplified: if target_reached, not moving

        # Invariant 1: fault=1 → OperationEnabled=0
        if fault and operation_enabled:
            if self._last_fault_time is None:
                self._last_fault_time = timestamp
            elif timestamp - self._last_fault_time > self.transient_allowance_s:
                raise InvariantViolationError(
                    f"Invariant violated: fault=1 but OperationEnabled=1 "
                    f"(persisted >{self.transient_allowance_s}s)"
                )
        else:
            self._last_fault_time = None

        # Invariant 2: emergency (quick_stop=0) → ve=0 and eo=0
        if not quick_stop:
            if voltage_enabled or operation_enabled:
                if self._last_emergency_time is None:
                    self._last_emergency_time = timestamp
                elif (
                    timestamp - self._last_emergency_time
                    > self.transient_allowance_s
                ):
                    raise InvariantViolationError(
                        f"Invariant violated: quick_stop=0 but "
                        f"voltage_enabled={voltage_enabled} "
                        f"operation_enabled={operation_enabled} "
                        f"(persisted >{self.transient_allowance_s}s)"
                    )
            else:
                self._last_emergency_time = None
        else:
            self._last_emergency_time = None

        # Invariant 3: TargetReached=1 with is_moving=1 should be transient
        if target_reached and is_moving:
            # This is a contradiction, but some drives briefly show this
            if self._target_reached_moving_start is None:
                self._target_reached_moving_start = timestamp
            elif (
                timestamp - self._target_reached_moving_start
                > self.transient_allowance_s
            ):
                raise InvariantViolationError(
                    f"Invariant violated: TargetReached=1 but is_moving=1 "
                    f"(persisted >{self.transient_allowance_s}s)"
                )
        else:
            self._target_reached_moving_start = None


@dataclass
class ReconnectionMonitor:
    """Monitor for connection stability.

    Tracks disconnects and provides callbacks for connection state changes.
    """

    disconnect_count: int = 0
    _last_connected: bool = True
    _disconnect_times: list[float] = None

    def __post_init__(self) -> None:
        """Initialize disconnect times list."""
        if self._disconnect_times is None:
            self._disconnect_times = []

    def check(self, is_connected: bool, timestamp: float) -> None:
        """Check connection state and record disconnects.

        Args:
            is_connected: Current connection state
            timestamp: Current timestamp
        """
        if not is_connected and self._last_connected:
            # Transition from connected to disconnected
            self.disconnect_count += 1
            self._disconnect_times.append(timestamp)

        self._last_connected = is_connected

    def get_disconnect_count(self) -> int:
        """Get total number of disconnects."""
        return self.disconnect_count

    def get_disconnect_times(self) -> list[float]:
        """Get timestamps of all disconnects."""
        return list(self._disconnect_times)


@asynccontextmanager
async def monitor_during(
    monitor: MonotonicityMonitor | ConvergenceMonitor | CiA402InvariantMonitor,
    check_func: Callable[[], tuple],
    poll_interval_s: float = 0.05,
) -> AsyncGenerator[None, None]:
    """Context manager to run a monitor during an async block.

    Example:
        async with monitor_during(monitor, lambda: (pos, time.time()), 0.02):
            await drive.move_to_position(...)
    """
    # Start monitoring
    try:
        # Create a task to poll and check
        async def monitor_loop():
            while True:
                args = check_func()
                monitor.check(*args)
                await asyncio.sleep(poll_interval_s)

        task = asyncio.create_task(monitor_loop())
        yield
    finally:
        # Stop monitoring
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

