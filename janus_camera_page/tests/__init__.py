"""janus_camera_page test package.

Fixtures are defined in tests/conftest.py (the single source of truth).
"""

from __future__ import annotations

import os
import sys

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)
