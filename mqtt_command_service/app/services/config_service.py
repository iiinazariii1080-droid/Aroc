"""
Unified configuration service with versioning and change notifications.

This module provides a single source of truth for MQTT broker configuration
with automatic versioning and event-based notifications for workers.
"""
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from config import BridgeConfig
from config import load_bridge_config as _load_bridge_config
from config_storage import get_storage

logger = logging.getLogger(__name__)


class ConfigRevision:
    """Represents a configuration revision with versioning."""

    def __init__(self, revision: int, updated_at: datetime, config: BridgeConfig):
        self.revision = revision
        self.updated_at = updated_at
        self.config = config

    def __repr__(self) -> str:
        return f"ConfigRevision(revision={self.revision}, updated_at={self.updated_at.isoformat()})"


class ConfigChangeEvent(Enum):
    """Types of configuration change events."""
    BROKER_CHANGED = "broker_changed"
    PORT_CHANGED = "port_changed"
    CREDENTIALS_CHANGED = "credentials_changed"
    TLS_CHANGED = "tls_changed"
    CERTIFICATES_CHANGED = "certificates_changed"
    FULL_RELOAD = "full_reload"


@dataclass
class ConfigChangeNotification:
    """Notification about configuration change."""
    event: ConfigChangeEvent
    revision: int
    updated_at: datetime
    old_config: BridgeConfig | None
    new_config: BridgeConfig
    changed_fields: set[str]


class ConfigService:
    """
    Unified configuration service with caching, versioning, and change notifications.

    This is the single source of truth for broker configuration.
    All workers (bridge, telemetry) should subscribe to changes and reload config
    when notified.
    """

    def __init__(self, poll_interval: float = 5.0):
        """
        Initialize config service.

        Args:
            poll_interval: How often to check for config changes (seconds)
        """
        self._lock = threading.RLock()
        self._storage = get_storage()
        self._poll_interval = poll_interval

        # Current cached config
        self._current_revision: ConfigRevision | None = None
        self._last_check_time = 0.0

        # Subscribers: list of callbacks that will be called on config change
        self._subscribers: list[Callable[[ConfigChangeNotification], None]] = []
        self._subscribers_lock = threading.Lock()

        # Load initial config
        self._current_revision = self._load_config()

    def _load_config(self) -> ConfigRevision:
        """Load current config from storage and create revision."""
        with self._lock:
            try:
                config = _load_bridge_config()
                # Get revision from storage (counter stored in DB)
                revision_key = "__config_revision__"
                revision_str = self._storage.get(revision_key, "0")
                try:
                    revision = int(revision_str or "0")
                except (ValueError, TypeError):
                    revision = 0

                # Get latest updated_at from config_history via SQL MAX
                broker_keys = [
                    "MQTT_BROKER", "MQTT_PORT", "MQTT_USER", "MQTT_PASS",
                    "MQTT_USE_TLS", "MQTT_CA_CERTS", "MQTT_CERTFILE", "MQTT_KEYFILE", "MQTT_TLS_INSECURE"
                ]

                latest_updated_at = datetime.now(UTC)
                try:
                    latest_str = self._storage.get_latest_update_time(broker_keys)
                    if latest_str:
                        latest_updated_at = datetime.fromisoformat(
                            latest_str.replace('Z', '+00:00')
                        )
                except Exception:
                    pass

                return ConfigRevision(revision, latest_updated_at, config)
            except Exception as e:
                logger.error("Failed to load config: %s", e, exc_info=True)
                # Return cached config if available
                if self._current_revision:
                    return self._current_revision
                raise

    def get_config(self, force_reload: bool = False) -> BridgeConfig:
        """
        Get current broker configuration.

        Args:
            force_reload: If True, reload from storage even if cache is fresh

        Returns:
            Current BridgeConfig
        """
        with self._lock:
            now = time.time()

            # Check if we need to reload
            if force_reload or self._current_revision is None or (now - self._last_check_time) >= self._poll_interval:
                new_revision = self._load_config()

                # Check if config actually changed
                if self._current_revision is None or self._current_revision.revision != new_revision.revision:
                    old_revision = self._current_revision
                    self._current_revision = new_revision
                    self._last_check_time = now

                    # Notify subscribers if config changed
                    if old_revision is not None:
                        self._notify_subscribers(old_revision, new_revision)
                else:
                    # Config didn't change, just update check time
                    self._last_check_time = now

            return self._current_revision.config

    def get_revision(self) -> int:
        """Get current config revision number."""
        with self._lock:
            if self._current_revision is None:
                self._load_config()
            return self._current_revision.revision if self._current_revision else 0

    def update_config(
        self,
        updates: dict[str, str],
        updated_by: str = "api",
        reason: str | None = None
    ) -> bool:
        """
        Atomically update configuration.

        Args:
            updates: Dictionary of config key -> value mappings
            updated_by: Who made the change
            reason: Reason for change

        Returns:
            True if successful, False otherwise
        """
        with self._lock:
            # Include revision counter in the same atomic batch
            revision_key = "__config_revision__"
            current_revision = self.get_revision()
            new_revision_num = current_revision + 1

            # Merge revision update into the same transaction
            merged_updates = dict(updates)
            merged_updates[revision_key] = str(new_revision_num)

            success = self._storage.batch_update(merged_updates, updated_by=updated_by, reason=reason)

            if success:
                # Force reload to get new config
                old_revision = self._current_revision
                new_revision = self._load_config()
                # Ensure new revision number is set
                new_revision.revision = new_revision_num
                self._current_revision = new_revision
                self._last_check_time = time.time()

                # Notify subscribers
                if old_revision is not None:
                    self._notify_subscribers(old_revision, new_revision)

            return success

    def subscribe(self, callback: Callable[[ConfigChangeNotification], None]) -> None:
        """
        Subscribe to configuration change notifications.

        Args:
            callback: Function that will be called when config changes.
                     Receives ConfigChangeNotification as argument.
        """
        with self._subscribers_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
                logger.debug("Subscriber added, total: %d", len(self._subscribers))

    def unsubscribe(self, callback: Callable[[ConfigChangeNotification], None]) -> None:
        """
        Unsubscribe from configuration change notifications.

        Args:
            callback: Callback function to remove
        """
        with self._subscribers_lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
                logger.debug("Subscriber removed, total: %d", len(self._subscribers))

    def _notify_subscribers(self, old_revision: ConfigRevision, new_revision: ConfigRevision) -> None:
        """Notify all subscribers about config change."""
        # Determine what changed
        old_config = old_revision.config
        new_config = new_revision.config

        changed_fields = set()
        events = []

        if old_config.broker != new_config.broker:
            changed_fields.add("broker")
            events.append(ConfigChangeEvent.BROKER_CHANGED)

        if old_config.broker_port != new_config.broker_port:
            changed_fields.add("broker_port")
            events.append(ConfigChangeEvent.PORT_CHANGED)

        if (old_config.mqtt_user != new_config.mqtt_user or
            old_config.mqtt_password != new_config.mqtt_password):
            changed_fields.add("mqtt_user")
            changed_fields.add("mqtt_password")
            events.append(ConfigChangeEvent.CREDENTIALS_CHANGED)

        if old_config.mqtt_use_tls != new_config.mqtt_use_tls:
            changed_fields.add("mqtt_use_tls")
            events.append(ConfigChangeEvent.TLS_CHANGED)

        if (old_config.mqtt_ca_certs != new_config.mqtt_ca_certs or
            old_config.mqtt_certfile != new_config.mqtt_certfile or
            old_config.mqtt_keyfile != new_config.mqtt_keyfile or
            old_config.mqtt_tls_insecure != new_config.mqtt_tls_insecure):
            changed_fields.update(["mqtt_ca_certs", "mqtt_certfile", "mqtt_keyfile", "mqtt_tls_insecure"])
            events.append(ConfigChangeEvent.CERTIFICATES_CHANGED)

        # If many fields changed, use FULL_RELOAD
        if len(changed_fields) > 3:
            events = [ConfigChangeEvent.FULL_RELOAD]

        # Create notification
        notification = ConfigChangeNotification(
            event=events[0] if events else ConfigChangeEvent.FULL_RELOAD,
            revision=new_revision.revision,
            updated_at=new_revision.updated_at,
            old_config=old_config,
            new_config=new_config,
            changed_fields=changed_fields
        )

        # Notify all subscribers
        with self._subscribers_lock:
            subscribers = list(self._subscribers)  # Copy to avoid lock during callbacks

        for callback in subscribers:
            try:
                callback(notification)
            except Exception as e:
                logger.error("Error in config change subscriber callback: %s", e, exc_info=True)

        logger.info(
            "Config changed: revision %s -> %s, events=%s, fields=%s",
            old_revision.revision, new_revision.revision,
            [e.value for e in events], changed_fields
        )


# Global singleton instance
_config_service: ConfigService | None = None
_config_service_lock = threading.Lock()


def get_config_service(poll_interval: float = 5.0) -> ConfigService:
    """Get global config service instance (singleton)."""
    global _config_service
    if _config_service is None:
        with _config_service_lock:
            if _config_service is None:
                _config_service = ConfigService(poll_interval=poll_interval)
    return _config_service

