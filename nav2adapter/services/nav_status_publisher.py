"""
Shared navigation status publish logic.

Single source of truth for publishing NavigationStatus to MQTT and state store.
Used by both CommandHandler and StatusPublisher to avoid duplication (НАР-2).
"""
import logging
from typing import Optional

from domain.models import NavigationStatus
from services.state_store import state_store

_LOGGER = logging.getLogger(__name__)


async def publish_navigation_status(
    status: NavigationStatus,
    mqtt_adapter: Optional[object],
    *,
    update_store: bool = True,
) -> None:
    """Publish navigation status to MQTT and optionally persist to state store.

    Args:
        status: The NavigationStatus to publish.
        mqtt_adapter: MqttAdapter instance (or None if MQTT is disabled).
        update_store: Whether to update the in-memory state store (default True).
    """
    _LOGGER.debug(
        "publish_navigation_status: status=%s goal_id=%s progress=%s",
        getattr(status, "status", None),
        getattr(status, "goal_id", None),
        getattr(status, "progress_percent", None),
    )
    if update_store:
        await state_store.set_last_navigation_status(status)
    if mqtt_adapter:
        await mqtt_adapter.publish_navigation_status(status.model_dump())
