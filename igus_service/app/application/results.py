"""Plain-dataclass results for the application layer.

These are the return types for DriveUseCases methods.  Using plain dataclasses
(not Pydantic models) keeps the application layer independent of the HTTP
presentation layer (app/api_models.py).

String fields correspond to the ``.value`` of the matching API enum so that
callers in the presentation layer can construct the enum from the string without
storing an enum dependency here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultDetailsResult:
    """Fault diagnostic details (presentation-agnostic mirror of FaultDetails)."""
    error_code: str | None = None
    error_register: str | None = None
    history: list[str] | None = None


@dataclass(frozen=True)
class FaultInfoResult:
    """Fault state (presentation-agnostic mirror of FaultInfo)."""
    active: bool = False
    details: FaultDetailsResult | None = None


@dataclass(frozen=True)
class DriveStatusResult:
    """Application-layer drive status result (presentation-agnostic).

    String fields correspond to the ``.value`` of the relevant API enum:
      - ``online``:       DriveOnlineState.value  (``"online"`` | ``"offline"`` | ``"degraded"``)
      - ``cia402_state``: CiA402State.value
      - ``mode_display``: OperationMode.value | ``None``

    ``is_moving`` and ``is_homed`` are read atomically within
    ``get_drive_status()`` to eliminate the TOCTOU issue that arises when
    the legacy ``/status`` route makes separate post-call reads.

    ``is_moving`` defaults to ``True`` (fail-safe): any code that constructs
    this result without an explicit ``is_moving`` argument is conservatively
    treated as "axis may be in motion".  This prevents unsafe follow-on
    commands when the motion state is unknown.
    """
    online: str
    connected: bool
    last_poll_ts: int | None
    poll_period_ms: float | None
    cia402_state: str
    mode_display: str | None
    statusword: int
    status_bits: dict[str, bool]
    remote: bool | None
    enabled: bool | None
    position: int | None
    velocity: int | None
    fault: FaultInfoResult
    is_moving: bool = True   # fail-safe default: unknown state → assume moving
    is_homed: bool = False
