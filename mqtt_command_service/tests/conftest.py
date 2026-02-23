"""
Pytest configuration and shared fixtures.
"""
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Set test environment variables
os.environ.setdefault("CONFIG_DB_PATH", ":memory:")
os.environ.setdefault("API_ENABLED", "false")

