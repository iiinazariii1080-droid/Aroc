"""Runtime reliability metrics (in-process, lightweight).

This module intentionally avoids external dependencies and provides
best-effort counters for production debugging.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections import deque
from typing import Dict, Any


class ReliabilityMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = defaultdict(int)
        self._duration_count: Dict[str, int] = defaultdict(int)
        self._duration_sum_s: Dict[str, float] = defaultdict(float)
        self._duration_max_s: Dict[str, float] = defaultdict(float)
        self._events: Dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._rate_window_max_s = 300.0
        self._started_at = time.time()

    def inc(self, key: str, delta: int = 1) -> None:
        if not key:
            return
        now = time.time()
        d = int(delta)
        with self._lock:
            self._counters[key] += d
            q = self._events[key]
            q.append((now, d))
            cutoff = now - self._rate_window_max_s
            while q and q[0][0] < cutoff:
                q.popleft()

    def observe_duration(self, key: str, seconds: float) -> None:
        if not key:
            return
        value = float(seconds)
        if value < 0:
            value = 0.0
        with self._lock:
            self._duration_count[key] += 1
            self._duration_sum_s[key] += value
            if value > self._duration_max_s[key]:
                self._duration_max_s[key] = value

    def reset(self) -> None:
        """Reset all counters and duration aggregates (mainly for tests)."""
        with self._lock:
            self._counters.clear()
            self._duration_count.clear()
            self._duration_sum_s.clear()
            self._duration_max_s.clear()
            self._events.clear()
            self._started_at = time.time()

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        rate_window_s = 60.0
        cutoff = now - rate_window_s
        with self._lock:
            counters = dict(self._counters)
            duration = {
                key: {
                    "count": int(self._duration_count.get(key, 0) or 0),
                    "sum_s": float(self._duration_sum_s.get(key, 0.0) or 0.0),
                    "max_s": float(self._duration_max_s.get(key, 0.0) or 0.0),
                }
                for key in set(self._duration_count.keys())
            }
            rates_60s: Dict[str, float] = {}
            for key, q in self._events.items():
                while q and q[0][0] < cutoff:
                    q.popleft()
                if not q:
                    continue
                total = sum(delta for _, delta in q)
                rates_60s[key] = float(total) / rate_window_s
        return {
            "uptime_s": max(0.0, time.time() - self._started_at),
            "counters": counters,
            "duration": duration,
            "rates_60s": rates_60s,
        }


reliability_metrics = ReliabilityMetrics()
