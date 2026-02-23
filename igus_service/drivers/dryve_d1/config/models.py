"""Configuration models for the dryve D1 driver.

This module provides typed configuration models with optional Pydantic support.
If Pydantic is available, models use BaseModel with validation.
Otherwise, dataclasses are used as a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from pydantic import BaseModel, Field

    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    BaseModel = object  # type: ignore[assignment, misc]
    Field = lambda **kwargs: field(**kwargs)  # type: ignore[assignment, misc]

from .defaults import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_JOG_TTL_MS,
    DEFAULT_KEEPALIVE_INTERVAL_S,
    DEFAULT_KEEPALIVE_MISS_LIMIT,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_SOCKET_IDLE_TIMEOUT_S,
    DEFAULT_STATUS_POLL_S,
    DEFAULT_TELEMETRY_POLL_S,
)


def _require_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


def _require_non_negative(name: str, value: float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")


if HAS_PYDANTIC:
    # Pydantic-based models (with validation)

    class RetryPolicy(BaseModel):
        max_attempts: int | None = Field(default=None, ge=1)
        base_delay_s: float = Field(default=0.25, gt=0)
        max_delay_s: float = Field(default=5.0, gt=0)
        jitter_s: float = Field(default=0.1, ge=0)

    class ConnectionConfig(BaseModel):
        host: str = Field(..., min_length=1)
        port: int = Field(default=502, ge=1, le=65535)
        connect_timeout_s: float = Field(default=DEFAULT_CONNECT_TIMEOUT_S, gt=0)
        request_timeout_s: float = Field(default=DEFAULT_REQUEST_TIMEOUT_S, gt=0)
        socket_idle_timeout_s: float = Field(default=DEFAULT_SOCKET_IDLE_TIMEOUT_S, gt=0)
        unit_id: int = Field(default=1, ge=0, le=255)

    class PollRates(BaseModel):
        status_poll_s: float = Field(default=DEFAULT_STATUS_POLL_S, gt=0)
        telemetry_poll_s: float = Field(default=DEFAULT_TELEMETRY_POLL_S, gt=0)
        keepalive_interval_s: float = Field(default=DEFAULT_KEEPALIVE_INTERVAL_S, gt=0)

        keepalive_miss_limit: int = Field(default=DEFAULT_KEEPALIVE_MISS_LIMIT, ge=1)

    class MotionLimits(BaseModel):
        """Soft limits and validation rules (driver-side guards).

        Units are *drive units* (whatever the device is configured for).
        Provide these from commissioning parameters or vendor documentation.
        
        For software position limits in the drive (hardware-enforced):
        - min_position_limit: Minimum position limit (0x607D) - set in drive hardware
        - max_position_limit: Maximum position limit (0x607B) - set in drive hardware
        - If None, software limits are not configured in the drive
        """

        max_abs_position: int | None = Field(default=None)  # None => no position clamp
        max_abs_velocity: int | None = Field(default=None)  # None => no velocity clamp
        max_abs_accel: int | None = Field(default=None)     # None => no accel clamp
        max_abs_decel: int | None = Field(default=None)     # None => no decel clamp
        
        # Software position limits (hardware-enforced in drive)
        min_position_limit: int | None = Field(default=0)  # Min position limit (0x607D), default: 0
        max_position_limit: int | None = Field(default=120000)  # Max position limit (0x607B), default: 120000

        def clamp_position(self, pos: int) -> int:
            if self.max_abs_position is None:
                return pos
            m = int(self.max_abs_position)
            return max(-m, min(m, int(pos)))

        def clamp_velocity(self, vel: int) -> int:
            if self.max_abs_velocity is None:
                return vel
            m = int(self.max_abs_velocity)
            return max(-m, min(m, int(vel)))

        def clamp_accel(self, a: int) -> int:
            if self.max_abs_accel is None:
                return a
            m = int(self.max_abs_accel)
            return max(0, min(m, int(a)))

        def clamp_decel(self, d: int) -> int:
            if self.max_abs_decel is None:
                return d
            m = int(self.max_abs_decel)
            return max(0, min(m, int(d)))

    class JogConfig(BaseModel):
        ttl_ms: int = Field(default=DEFAULT_JOG_TTL_MS, ge=50, le=5000)
        stop_on_ttl_expire: bool = Field(default=True)

    class DriveConfig(BaseModel):
        connection: ConnectionConfig
        retry: RetryPolicy = Field(default_factory=RetryPolicy)
        poll: PollRates = Field(default_factory=PollRates)
        limits: MotionLimits = Field(default_factory=MotionLimits)
        jog: JogConfig = Field(default_factory=JogConfig)

else:
    # Minimal dataclass fallback (no pydantic)
    @dataclass(frozen=True)
    class RetryPolicy:  # type: ignore[no-redef]
        max_attempts: int | None = None
        base_delay_s: float = 0.25
        max_delay_s: float = 5.0
        jitter_s: float = 0.1

        def __post_init__(self):
            if self.max_attempts is not None and self.max_attempts < 1:
                raise ValueError("max_attempts must be >= 1 or None")
            _require_positive("base_delay_s", self.base_delay_s)
            _require_positive("max_delay_s", self.max_delay_s)
            _require_non_negative("jitter_s", self.jitter_s)

    @dataclass(frozen=True)
    class ConnectionConfig:  # type: ignore[no-redef]
        host: str
        port: int = 502
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
        socket_idle_timeout_s: float = DEFAULT_SOCKET_IDLE_TIMEOUT_S
        unit_id: int = 1

        def __post_init__(self):
            if not self.host:
                raise ValueError("host must be non-empty")
            if not (1 <= int(self.port) <= 65535):
                raise ValueError("port must be 1..65535")
            _require_positive("connect_timeout_s", self.connect_timeout_s)
            _require_positive("request_timeout_s", self.request_timeout_s)
            _require_positive("socket_idle_timeout_s", self.socket_idle_timeout_s)
            if not (1 <= int(self.unit_id) <= 255):
                raise ValueError("unit_id must be 1..255")

    @dataclass(frozen=True)
    class PollRates:  # type: ignore[no-redef]
        status_poll_s: float = DEFAULT_STATUS_POLL_S
        telemetry_poll_s: float = DEFAULT_TELEMETRY_POLL_S
        keepalive_interval_s: float = DEFAULT_KEEPALIVE_INTERVAL_S
        keepalive_miss_limit: int = DEFAULT_KEEPALIVE_MISS_LIMIT

        def __post_init__(self):
            _require_positive("status_poll_s", self.status_poll_s)
            _require_positive("telemetry_poll_s", self.telemetry_poll_s)
            _require_positive("keepalive_interval_s", self.keepalive_interval_s)
            if int(self.keepalive_miss_limit) < 1:
                raise ValueError("keepalive_miss_limit must be >= 1")

    @dataclass(frozen=True)
    class MotionLimits:  # type: ignore[no-redef]
        max_abs_position: int | None = None
        max_abs_velocity: int | None = None
        max_abs_accel: int | None = None
        max_abs_decel: int | None = None
        
        # Software position limits (hardware-enforced in drive)
        min_position_limit: int | None = 0  # Min position limit (0x607D), default: 0
        max_position_limit: int | None = 120000  # Max position limit (0x607B), default: 120000

        def clamp_position(self, pos: int) -> int:
            if self.max_abs_position is None:
                return pos
            m = int(self.max_abs_position)
            return max(-m, min(m, int(pos)))

        def clamp_velocity(self, vel: int) -> int:
            if self.max_abs_velocity is None:
                return vel
            m = int(self.max_abs_velocity)
            return max(-m, min(m, int(vel)))

        def clamp_accel(self, a: int) -> int:
            if self.max_abs_accel is None:
                return a
            m = int(self.max_abs_accel)
            return max(0, min(m, int(a)))

        def clamp_decel(self, d: int) -> int:
            if self.max_abs_decel is None:
                return d
            m = int(self.max_abs_decel)
            return max(0, min(m, int(d)))

    @dataclass(frozen=True)
    class JogConfig:  # type: ignore[no-redef]
        ttl_ms: int = DEFAULT_JOG_TTL_MS
        stop_on_ttl_expire: bool = True

        def __post_init__(self):
            if not (50 <= int(self.ttl_ms) <= 5000):
                raise ValueError("ttl_ms must be 50..5000")

    @dataclass(frozen=True)
    class DriveConfig:  # type: ignore[no-redef]
        connection: ConnectionConfig
        retry: RetryPolicy = field(default_factory=RetryPolicy)
        poll: PollRates = field(default_factory=PollRates)
        limits: MotionLimits = field(default_factory=MotionLimits)
        jog: JogConfig = field(default_factory=JogConfig)

