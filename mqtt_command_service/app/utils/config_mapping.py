"""Configuration mapping utilities."""
from typing import Any

from app.models.schemas import BrokerConfigUpdate

# Mapping BrokerConfigUpdate fields -> config keys
# Certificate paths are fixed and cannot be changed via API
FIELD_TO_CONFIG_KEY = {
    "broker": "MQTT_BROKER",
    "broker_port": "MQTT_PORT",
    "mqtt_user": "MQTT_USER",
    "mqtt_password": "MQTT_PASS",
    "mqtt_use_tls": "MQTT_USE_TLS",
    # Certificate paths removed - always use fixed paths from constants
    "mqtt_tls_insecure": "MQTT_TLS_INSECURE",
}

# Mapping config keys -> response fields (reverse mapping)
CONFIG_KEY_TO_FIELD = {v: k for k, v in FIELD_TO_CONFIG_KEY.items()}


def prepare_config_updates(update: BrokerConfigUpdate) -> dict[str, str]:
    """
    Prepare configuration updates from BrokerConfigUpdate model.

    Returns:
        Dictionary of config_key -> value for batch update
    """
    updates = {}

    if update.broker is not None:
        updates["MQTT_BROKER"] = update.broker

    if update.broker_port is not None:
        updates["MQTT_PORT"] = str(update.broker_port)

    if update.mqtt_user is not None:
        updates["MQTT_USER"] = update.mqtt_user

    if update.mqtt_password is not None:
        updates["MQTT_PASS"] = update.mqtt_password

    if update.mqtt_use_tls is not None:
        updates["MQTT_USE_TLS"] = str(update.mqtt_use_tls).lower()

    # Certificate paths are fixed - cannot be changed via API
    # Use /api/v1/config/certificates/upload to upload certificates

    if update.mqtt_tls_insecure is not None:
        updates["MQTT_TLS_INSECURE"] = str(update.mqtt_tls_insecure).lower()

    return updates


def apply_config_updates_to_dict(
    config: dict[str, Any],
    updates: dict[str, str]
) -> dict[str, Any]:
    """
    Apply config updates to config dictionary.

    Converts string values to appropriate types.

    Args:
        config: Configuration dictionary to update
        updates: Dictionary of config_key -> value (as strings)

    Returns:
        Updated config dictionary
    """
    import logging
    logger = logging.getLogger(__name__)

    # Type conversion functions
    type_converters = {
        "MQTT_PORT": lambda v: int(v),
        "MQTT_USE_TLS": lambda v: v.lower() in ("true", "1", "yes"),
        "MQTT_TLS_INSECURE": lambda v: v.lower() in ("true", "1", "yes"),
        "MQTT_CA_CERTS": lambda v: v if v else None,
        "MQTT_CERTFILE": lambda v: v if v else None,
        "MQTT_KEYFILE": lambda v: v if v else None,
    }

    # Field name mapping (config_key -> response field name)
    # Use CONFIG_KEY_TO_FIELD where possible, with special cases for direct mappings
    field_mapping = {
        "MQTT_BROKER": "MQTT_BROKER",  # Special case: same name
        "MQTT_PORT": "MQTT_PORT",  # Special case: same name
        **{k: v for k, v in CONFIG_KEY_TO_FIELD.items() if k not in ("MQTT_BROKER", "MQTT_PORT")}
    }

    for config_key, value in updates.items():
        # Get field name
        field_name = field_mapping.get(config_key, config_key)

        # Apply type conversion if needed
        if config_key in type_converters:
            try:
                converted_value: Any = type_converters[config_key](value)
                config[field_name] = converted_value
            except (ValueError, TypeError) as e:
                logger.warning(
                    "Failed to convert %s to appropriate type: %s", config_key, e
                )
                # Skip this update if conversion fails
                continue
        else:
            config[field_name] = value

    return config

