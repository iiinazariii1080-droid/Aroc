"""Unified navigation status publishing.

All code that needs to store a ``NavigationStatus`` should call
:func:`publish_nav_status` instead of inlining the pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.models import NavigationStatus

if TYPE_CHECKING:
    from services.state_store import StateStore


async def publish_nav_status(
    status: NavigationStatus,
    state_store: StateStore,
    *,
    update_store: bool = True,
) -> None:
    """Store navigation status in state store.

    Args:
        status: The navigation status to publish.
        state_store: In-memory state store for caching latest status.
        update_store: Whether to persist in state_store (default ``True``).
    """
    if update_store:
        await state_store.set_last_navigation_status(status)
