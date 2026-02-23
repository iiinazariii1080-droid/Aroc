"""Metrics collection for test scenarios.

Provides TestMetrics class for tracking latency, disconnect counts,
position samples, and computing statistics (avg, p95, etc.).
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class TestMetrics:  # noqa: N801
    __test__ = False  # Tell pytest this is not a test class
    """Collects metrics during test execution.

    Attributes:
        latencies: Ring buffer of request latencies (seconds)
        disconnect_count: Number of connection disconnects
        position_samples: Ring buffer of position samples
        statusword_samples: Ring buffer of statusword samples
        max_samples: Maximum number of samples to keep in buffers
    """

    max_samples: int = 100
    latencies: Deque[float] = field(default_factory=deque)
    disconnect_count: int = 0
    position_samples: Deque[int] = field(default_factory=deque)
    statusword_samples: Deque[int] = field(default_factory=deque)

    def record_latency(self, latency_s: float) -> None:
        """Record a request latency."""
        self.latencies.append(latency_s)
        if len(self.latencies) > self.max_samples:
            self.latencies.popleft()

    def record_disconnect(self) -> None:
        """Record a disconnect event."""
        self.disconnect_count += 1

    def record_position(self, position: int) -> None:
        """Record a position sample."""
        self.position_samples.append(position)
        if len(self.position_samples) > self.max_samples:
            self.position_samples.popleft()

    def record_statusword(self, statusword: int) -> None:
        """Record a statusword sample."""
        self.statusword_samples.append(statusword)
        if len(self.statusword_samples) > self.max_samples:
            self.statusword_samples.popleft()

    def avg_latency(self) -> float | None:
        """Compute average latency."""
        if not self.latencies:
            return None
        return statistics.mean(self.latencies)

    def p95_latency(self) -> float | None:
        """Compute 95th percentile latency."""
        if not self.latencies:
            return None
        sorted_latencies = sorted(self.latencies)
        index = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(index, len(sorted_latencies) - 1)]

    def p99_latency(self) -> float | None:
        """Compute 99th percentile latency."""
        if not self.latencies:
            return None
        sorted_latencies = sorted(self.latencies)
        index = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[min(index, len(sorted_latencies) - 1)]

    def get_recent_positions(self, n: int = 10) -> list[int]:
        """Get the last N position samples."""
        return list(self.position_samples)[-n:]

    def get_recent_statuswords(self, n: int = 10) -> list[int]:
        """Get the last N statusword samples."""
        return list(self.statusword_samples)[-n:]

    def reset(self) -> None:
        """Reset all metrics."""
        self.latencies.clear()
        self.disconnect_count = 0
        self.position_samples.clear()
        self.statusword_samples.clear()

