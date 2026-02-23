#!/usr/bin/env python3
"""Create first API key for the system."""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.security import Role, create_api_key

if __name__ == "__main__":
    api_key, key_id = create_api_key(Role.ADMIN, "Initial admin key")
    print("API Key created!")
    print(f"Key ID: {key_id}")
    print(f"API Key: {api_key}")
    print("\n!!! SAVE THIS KEY NOW! It will not be shown again.")

