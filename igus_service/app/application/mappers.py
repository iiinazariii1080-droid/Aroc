"""Driver-to-application type converters.

Lives in the application layer so that neither the driver package nor
app/api_models.py need to import from each other directly.

This module deliberately has NO runtime imports from app.api_models — it maps
driver types to plain strings (the .value of the corresponding API enum) so
that the application layer stays free of presentation-layer dependencies.

Single source of truth invariant
---------------------------------
CiA402 state strings are derived from the driver enum member *names* via
``state.name.lower()``.  This is the single derivation rule:

    DriverCiA402State.OPERATION_ENABLED  →  "operation_enabled"
    DriverCiA402State.FAULT              →  "fault"

The API-layer ``CiA402State`` enum (app/api_models.py) must use matching
``.value`` strings.  Adding a new driver state automatically produces a valid
string without touching this file.  Invariant validated by
``test_mappers_cia402_coverage`` in the test suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drivers.dryve_d1.od.statusword import CiA402State as DriverCiA402State

# Populated lazily on first call to avoid import-time side effects from the
# driver package.  The map is module-level so it is built only once.
_CIA402_STATE_STR_MAP: dict[DriverCiA402State, str] | None = None


def _get_cia402_state_str_map() -> dict[DriverCiA402State, str]:
    global _CIA402_STATE_STR_MAP
    if _CIA402_STATE_STR_MAP is None:
        from drivers.dryve_d1.od.statusword import CiA402State as DriverCiA402State

        # Derive strings from enum member names: OPERATION_ENABLED → "operation_enabled".
        # Single source of truth — no hardcoded string list to keep in sync with api_models.
        # Invariant: app/api_models.CiA402State.value == state.name.lower() for every member.
        _CIA402_STATE_STR_MAP = {state: state.name.lower() for state in DriverCiA402State}
    return _CIA402_STATE_STR_MAP


def driver_cia402_state_to_str(state: DriverCiA402State) -> str:
    """Convert driver CiA402State to its API enum string value.

    Returns the string corresponding to ``CiA402State(value)`` in the
    presentation layer.  Callers can reconstruct the enum with
    ``CiA402State(driver_cia402_state_to_str(state))``.
    """
    return _get_cia402_state_str_map().get(state, "unknown")


_MODE_DISPLAY_MAP: dict[int, str] = {
    1: "PP",
    3: "PV",
    6: "HOMING",
}


def mode_display_to_str(mode_display_raw: int | None) -> str:
    """Convert mode_display integer register value to OperationMode string.

    Returns the ``.value`` of the corresponding ``OperationMode`` enum member,
    so the presentation layer can reconstruct the enum from the string.

    Note: Fallback is ``"UNKNOWN"`` (uppercase) to match OperationMode enum
    values which are uppercase (``"PP"``, ``"PV"``, ``"HOMING"``).
    ``driver_cia402_state_to_str`` uses lowercase ``"unknown"`` because
    CiA402State enum values are lowercase.
    """
    if mode_display_raw is None:
        return "UNKNOWN"
    return _MODE_DISPLAY_MAP.get(int(mode_display_raw) & 0xFF, "UNKNOWN")
