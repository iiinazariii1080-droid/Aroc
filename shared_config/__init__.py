"""Shared configuration primitives for all robot services.

Provides:
- ``ServicePorts`` — canonical port numbers for every micro-service.
- ``DeviceDefaults`` — default LAN addresses for physical hardware.
- ``get_service_url(name)`` — resolve a service base URL from env vars.
- ``BaseServiceSettings`` — pydantic-settings base class with common fields.
"""

from shared_config.network import DeviceDefaults, ServicePorts, get_service_url
from shared_config.settings_base import BaseServiceSettings

__all__ = [
    "BaseServiceSettings",
    "DeviceDefaults",
    "ServicePorts",
    "get_service_url",
]
