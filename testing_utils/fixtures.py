"""Common pytest fixtures available to all services when testing_utils is on PYTHONPATH.

These fixtures are auto-discovered when a service conftest.py does::

    from testing_utils.fixtures import *  # noqa: F401,F403

Or services can import specific fixtures::

    from testing_utils.fixtures import anyio_backend, temp_dir
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def env_override(monkeypatch: pytest.MonkeyPatch):
    """Helper fixture to set env vars for the duration of a test.

    Usage::

        def test_something(env_override):
            env_override("XARM_IP", "127.0.0.1")
            env_override("WS_CHECK_DISABLED", "1")
            # ... imports after env is patched ...
    """

    def _set(key: str, value: str) -> None:
        monkeypatch.setenv(key, value)

    return _set


@pytest.fixture
def no_hardware_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set environment variables to disable all hardware connections.

    Useful for services that check env vars at import time.
    """
    monkeypatch.setenv("XARM_IP", "127.0.0.1")
    monkeypatch.setenv("WS_CHECK_DISABLED", "1")
    monkeypatch.setenv("DRYVE_HOST", "127.0.0.1")
    monkeypatch.setenv("MQTT_BROKER", "127.0.0.1")
    monkeypatch.setenv("DEPTH_BASE_URL", "http://127.0.0.1:9999")
