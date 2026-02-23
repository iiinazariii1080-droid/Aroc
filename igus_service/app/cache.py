"""State cache adapter for accessing driver telemetry snapshots.

The DryveD1 driver maintains an internal TelemetryPoller (M1 requirement) and exposes
the latest snapshot via a public method. This module wraps that in a stable dict API
consumed by the HTTP layer.
"""

from __future__ import annotations

from typing import Any


class StateCache:
    """Read-only view over driver telemetry snapshots."""

    def __init__(self, drive: Any) -> None:
        self._drive = drive

    def _get_snapshot(self):
        if self._drive is None:
            return None
        # Preferred public API (added in our driver fork)
        if hasattr(self._drive, "telemetry_latest"):
            return self._drive.telemetry_latest()
        # Fallback for older versions (avoid breaking the service)
        poller = getattr(self._drive, "_telemetry_poller", None)
        return getattr(poller, "latest", None) if poller is not None else None

    def get_cached_status(self) -> dict[str, Any] | None:
        snap = self._get_snapshot()
        if snap is None:
            return None
        return {
            "statusword": snap.statusword,
            "cia402_state": snap.cia402_state,
            "position": snap.position,
            "velocity": snap.velocity,
            "mode_display": snap.mode_display,
            "decoded_status": snap.decoded_status,
            "ts_monotonic_s": snap.ts_monotonic_s,
        }

    def get_poll_info(self) -> dict[str, Any]:
        if self._drive is None:
            return {"is_running": False, "interval_s": None}
        if hasattr(self._drive, "telemetry_poll_info"):
            result: dict[str, Any] = self._drive.telemetry_poll_info()
            return result
        poller = getattr(self._drive, "_telemetry_poller", None)
        if poller is None:
            return {"is_running": False, "interval_s": None}
        cfg = getattr(poller, "_cfg", None)
        return {
            "is_running": bool(getattr(poller, "is_running", False)),
            "interval_s": float(getattr(cfg, "interval_s", 0.0)) if cfg is not None else None,
        }
