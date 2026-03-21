"""Centralized config imports — single fallback site for shared_config.network.

All modules that need DEVICES or PORTS should import from here:
    from app.config import DEVICES, PORTS

This avoids duplicating the try/except ImportError fallback across
multiple files (previously in settings.py and nat_config.py).
"""
try:
    from shared_config.network import DEVICES, PORTS  # monorepo context
except ImportError:  # pragma: no cover — standalone / CI outside monorepo
    from app.config.network_defaults import DEVICES, PORTS

__all__ = ["DEVICES", "PORTS"]
