"""Unified subprocess execution helper.

All subprocess.run calls in the application MUST go through this module so
that the architectural test (TestX7_SubprocessSinglePoint) can verify the
invariant.  Timeouts must always be specified explicitly — there is no default
to prevent callers from accidentally blocking forever.
"""
from __future__ import annotations

import subprocess


def run_cmd(cmd: list[str], *, timeout: int, error_prefix: str = "") -> str:
    """Run a command and return stdout; raise RuntimeError on non-zero exit.

    Args:
        cmd: Command and arguments list.
        timeout: Maximum execution time in seconds (required, no default).
        error_prefix: Optional human-readable context prepended to the error
            message (e.g. ``"Failed to restart janus.service"``).

    Returns:
        stdout as a string (may be empty).

    Raises:
        RuntimeError: If the command exits with a non-zero return code.
        subprocess.TimeoutExpired: If the command exceeds *timeout* seconds.
    """
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        prefix = f"{error_prefix}: " if error_prefix else ""
        raise RuntimeError(
            f"{prefix}{' '.join(cmd)} exit={result.returncode}: {detail}"
        )
    return result.stdout
