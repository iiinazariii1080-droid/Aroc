"""Pytest fixtures for app-level tests.

Shared fakes and helpers live in ``tests.fakes`` to avoid conftest namespace
collisions with ``drivers/tests/conftest.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

import main
from tests.fakes import (  # noqa: F401 — re-export for backward compat
    AsyncNoopLock,
    ControllableLock,
    DEFAULT_SETTINGS,
    FakeDrive,
    FakeEventBus,
    FakeSnapshot,
    set_app_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def noop_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable startup/shutdown so TestClient doesn't hit real hardware."""
    async def _noop(_app: Any) -> None:
        return None

    monkeypatch.setattr(main, "startup", _noop)
    monkeypatch.setattr(main, "shutdown", _noop)
