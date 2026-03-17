from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_LOADED = False


def _parse_env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()

        # strip quotes
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]

        if key:
            out[key] = val
    return out


def load_env_file(
    *,
    candidates: Iterable[str] | None = None,
    allow_example_fallback: bool = True,
    force: bool = False,
) -> None:
    """Load environment variables from a local env file.

    Policy:
    - Prefer explicit ENV_FILE
    - Then try .env / .env.local
    - If no env file is found AND no DRYVE_/IGUS_ vars exist, optionally fall back to .env.example
      (dev convenience; logged as WARNING).

    This function is idempotent per-process.  Pass ``force=True`` to reload in
    test scenarios that need to exercise different env-file configurations.
    """
    global _LOADED
    if _LOADED and not force:
        return
    _LOADED = False  # reset so we re-run cleanly on forced reload

    if candidates is None:
        candidates = []

    # If user already exported any connection vars, do not override.
    has_conn_env = any(
        k in os.environ
        for k in (
            "DRYVE_HOST",
            "DRYVE_PORT",
            "DRYVE_UNIT_ID",
            "IGUS_MOTOR_IP",
            "IGUS_MOTOR_PORT",
        )
    )

    search_order: list[str] = []
    env_file = os.getenv("ENV_FILE")
    if env_file:
        search_order.append(env_file)

    search_order.extend(list(candidates))
    search_order.extend([".env", ".env.local"])

    loaded_from: str | None = None
    for rel in search_order:
        p = Path(rel).expanduser()
        if not p.is_absolute():
            # resolve relative to current working directory
            p = (Path.cwd() / p).resolve()
        if p.exists() and p.is_file():
            try:
                data = _parse_env_lines(p.read_text(encoding="utf-8"))
                # only set if not already set (env wins)
                for k, v in data.items():
                    os.environ.setdefault(k, v)
                loaded_from = str(p)
            except Exception:
                _LOGGER.exception("Failed to load env file: %s", p)
            break

    # Dev fallback: if no file found and no conn env set, use .env.example if present
    if loaded_from is None and allow_example_fallback and not has_conn_env:
        p = (Path.cwd() / ".env.example").resolve()
        if p.exists() and p.is_file():
            try:
                data = _parse_env_lines(p.read_text(encoding="utf-8"))
                for k, v in data.items():
                    os.environ.setdefault(k, v)
                loaded_from = str(p)
                _LOGGER.warning("No .env found; loaded defaults from .env.example (%s).", p)
            except Exception:
                _LOGGER.exception("Failed to load .env.example: %s", p)

    if loaded_from:
        _LOGGER.info("Environment loaded from %s", loaded_from)

    _LOADED = True


def reset_for_testing() -> None:
    """Reset the loaded flag so the next load_env_file() call re-runs.

    Call this in test teardown or fixture finalizers when tests need to
    exercise different env-file configurations in isolation.
    """
    global _LOADED
    _LOADED = False
