from __future__ import annotations

import os


def _as_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def runtime_profile() -> str:
    profile = os.getenv("DRYVE_PROFILE", "production").strip().lower()
    return profile if profile else "production"


def is_simulator_profile() -> bool:
    return runtime_profile() in ("simulator", "dev")


def default_tid_mismatch_tolerance() -> bool:
    return is_simulator_profile()


def default_unit_id_wildcard_tolerance() -> bool:
    return is_simulator_profile()


def allow_tid_mismatch() -> bool:
    raw = os.getenv("DRYVE_ALLOW_TID_MISMATCH")
    if raw is None:
        return default_tid_mismatch_tolerance()
    return _as_bool(raw)


def allow_unit_id_wildcard() -> bool:
    raw = os.getenv("DRYVE_ALLOW_UNIT_ID_WILDCARD")
    if raw is None:
        return default_unit_id_wildcard_tolerance()
    return _as_bool(raw)
