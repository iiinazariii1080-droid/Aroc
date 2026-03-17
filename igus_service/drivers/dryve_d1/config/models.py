"""Configuration models for the dryve D1 driver."""

from __future__ import annotations

from pydantic import BaseModel, Field

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


class RetryPolicy(BaseModel):
    max_attempts: int | None = Field(default=None, ge=1)
    base_delay_s: float = Field(default=0.25, gt=0)
    max_delay_s: float = Field(default=5.0, gt=0)
    jitter_s: float = Field(default=0.1, ge=0)

    def to_transport_policy(self) -> "TransportRetryPolicy":
        """Convert to the transport-layer ``RetryPolicy`` dataclass.

        ``jitter_s`` (absolute seconds) is mapped to ``jitter_fraction``
        (relative to ``base_delay_s``), capped at 0.90.
        """
        from ..transport.retry import RetryPolicy as TransportRetryPolicy

        jitter_fraction = min(0.90, self.jitter_s / max(self.base_delay_s, 1e-6))
        return TransportRetryPolicy(
            max_attempts=int(self.max_attempts) if self.max_attempts is not None else 3,
            base_delay_s=self.base_delay_s,
            backoff_factor=2.0,
            max_delay_s=self.max_delay_s,
            jitter_fraction=jitter_fraction,
        )


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
    """Soft limits and validation rules used by the DryveD1 facade.

    Units are *drive units* (whatever the device is configured for).
    Provide these from commissioning parameters or vendor documentation.

    For software position limits in the drive (hardware-enforced):
    - min_position_limit: Minimum position limit — written to ODIndex.MIN_POSITION_LIMIT (0x607B)
    - max_position_limit: Maximum position limit — written to ODIndex.MAX_POSITION_LIMIT (0x607D)
    - If None, software limits are not configured in the drive
    """

    max_abs_position: int | None = Field(default=None)  # None => no position clamp
    max_abs_velocity: int | None = Field(default=None)  # None => no velocity clamp
    max_abs_accel: int | None = Field(default=None)     # None => no accel clamp
    max_abs_decel: int | None = Field(default=None)     # None => no decel clamp

    # Software position limits (hardware-enforced in drive).
    # Canonical register mapping: see od.indices.ODIndex.
    min_position_limit: int | None = Field(default=0)       # ODIndex.MIN_POSITION_LIMIT (0x607B)
    max_position_limit: int | None = Field(default=120000)  # ODIndex.MAX_POSITION_LIMIT (0x607D)

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
