"""Structured logging for test scenarios.

Provides TestLogger for logging test stages, samples, requests,
and response diagnostics (MBAP length, actual response size).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass
class RequestLog:
    """Log entry for a single request."""

    timestamp: float
    index: int
    subindex: int
    length: int
    value: Optional[int] = None
    mbap_length: Optional[int] = None
    actual_response_size: Optional[int] = None


@dataclass
class TestLogger:  # noqa: N801
    __test__ = False  # Tell pytest this is not a test class
    """Structured logger for test execution.

    Logs:
    - Test stages (step names with timestamps)
    - Last 10 samples of statusword/position (ring buffer)
    - Last 10 requests (index/subindex/value)
    - MBAP Length + actual response size for diagnostics
    """

    logger: logging.Logger
    max_samples: int = 10
    statusword_samples: Deque[tuple[float, int]] = field(
        default_factory=deque
    )
    position_samples: Deque[tuple[float, int]] = field(default_factory=deque)
    request_logs: Deque[RequestLog] = field(default_factory=deque)

    def __init__(
        self, name: str = "test", max_samples: int = 10
    ) -> None:
        """Initialize test logger.

        Args:
            name: Logger name
            max_samples: Maximum samples to keep in ring buffers
        """
        self.logger = logging.getLogger(name)
        self.max_samples = max_samples
        self.statusword_samples = deque(maxlen=max_samples)
        self.position_samples = deque(maxlen=max_samples)
        self.request_logs = deque(maxlen=max_samples)

    def log_stage(self, stage_name: str, **kwargs) -> None:
        """Log a test stage with optional context."""
        context = " ".join(f"{k}={v}" for k, v in kwargs.items())
        msg = f"[STAGE] {stage_name}"
        if context:
            msg += f" | {context}"
        self.logger.info(msg)

    def log_statusword(self, statusword: int) -> None:
        """Log a statusword sample."""
        timestamp = time.time()
        self.statusword_samples.append((timestamp, statusword))
        self.logger.debug(
            f"[STATUSWORD] 0x{statusword & 0xFFFF:04X} "
            f"(t={timestamp:.3f})"
        )

    def log_position(self, position: int) -> None:
        """Log a position sample."""
        timestamp = time.time()
        self.position_samples.append((timestamp, position))
        self.logger.debug(
            f"[POSITION] {position} (t={timestamp:.3f})"
        )

    def log_request(
        self,
        index: int,
        subindex: int,
        length: int,
        value: Optional[int] = None,
        mbap_length: Optional[int] = None,
        actual_response_size: Optional[int] = None,
    ) -> None:
        """Log a request with diagnostics."""
        log_entry = RequestLog(
            timestamp=time.time(),
            index=index,
            subindex=subindex,
            length=length,
            value=value,
            mbap_length=mbap_length,
            actual_response_size=actual_response_size,
        )
        self.request_logs.append(log_entry)

        msg = (
            f"[REQUEST] index=0x{index:04X} subindex={subindex} "
            f"length={length}"
        )
        if value is not None:
            msg += f" value=0x{value:X}"
        if mbap_length is not None:
            msg += f" mbap_length={mbap_length}"
        if actual_response_size is not None:
            msg += f" actual_size={actual_response_size}"
        self.logger.debug(msg)

    def get_recent_statuswords(self) -> list[tuple[float, int]]:
        """Get recent statusword samples."""
        return list(self.statusword_samples)

    def get_recent_positions(self) -> list[tuple[float, int]]:
        """Get recent position samples."""
        return list(self.position_samples)

    def get_recent_requests(self) -> list[RequestLog]:
        """Get recent request logs."""
        return list(self.request_logs)

    def log_summary(self) -> None:
        """Log a summary of recent samples and requests."""
        self.logger.info("[SUMMARY] Recent samples:")
        if self.statusword_samples:
            sw_list = [
                f"0x{sw:04X}" for _, sw in self.statusword_samples
            ]
            self.logger.info(f"  Statuswords: {', '.join(sw_list)}")
        if self.position_samples:
            pos_list = [str(pos) for _, pos in self.position_samples]
            self.logger.info(f"  Positions: {', '.join(pos_list)}")
        if self.request_logs:
            self.logger.info("  Recent requests:")
            for req in self.request_logs:
                self.logger.info(
                    f"    0x{req.index:04X}/{req.subindex} "
                    f"len={req.length} "
                    f"mbap={req.mbap_length} "
                    f"actual={req.actual_response_size}"
                )

