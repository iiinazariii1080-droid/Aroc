import os

# Service version string.  Override at deployment time via SERVER_VERSION env var
# (e.g. injected by CI from git tag).  Falls back to the hardcoded release
# constant so the service always reports a non-empty version.
SERVER_VERSION: str = os.getenv("SERVER_VERSION", "1.0.0")

# Driver package version — single authoritative import with fallback.
# Import from here instead of repeating the try/except in every module.
try:
    from drivers.dryve_d1 import __version__ as DRIVER_VERSION
except ImportError:
    DRIVER_VERSION = "unknown"
