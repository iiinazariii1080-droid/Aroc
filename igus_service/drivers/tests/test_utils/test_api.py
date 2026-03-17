"""Test API helpers for controlling drive states during testing.

These functions interact with test-only endpoints on the drive simulator
to set various states (homed, fault, emergency stop) for testing purposes.

WARNING: These endpoints are ONLY available in test/simulation mode
and should NEVER be used with real hardware.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlencode

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class TestAPIError(Exception):
    """Error when calling test API endpoints."""
    __test__ = False  # Tell pytest this is not a test class
    pass


class TestDriveController:
    """Controller for test-only drive state manipulation.
    
    This class provides methods to set drive states (homed, fault, emergency)
    through test API endpoints. These endpoints are ONLY available in test mode.
    
    WARNING: Do NOT use with real hardware!
    """
    __test__ = False  # Tell pytest this is not a test class
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        """Initialize test API controller.
        
        Args:
            base_url: Base URL of the test API server
        """
        self.base_url = base_url.rstrip('/')
        if not HAS_AIOHTTP:
            raise ImportError("aiohttp is required for test API. Install with: pip install aiohttp")
    
    async def set_homed(self, value: bool) -> None:
        """Set homed status.
        
        Args:
            value: True to set homed=1, False to set homed=0
        
        Raises:
            TestAPIError: If the API call fails
        """
        url = f"{self.base_url}/test/homed"
        params = {"value": 1 if value else 0}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, params=params, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise TestAPIError(f"Failed to set homed: HTTP {resp.status}: {text}")
            except aiohttp.ClientError as e:
                raise TestAPIError(f"Failed to set homed: {e}")
    
    async def set_fault(self, value: bool) -> None:
        """Set fault state.
        
        Args:
            value: True to activate fault (fault=1), False to clear fault (fault=0)
        
        Raises:
            TestAPIError: If the API call fails
        """
        url = f"{self.base_url}/test/fault"
        params = {"value": 1 if value else 0}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, params=params, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise TestAPIError(f"Failed to set fault: HTTP {resp.status}: {text}")
            except aiohttp.ClientError as e:
                raise TestAPIError(f"Failed to set fault: {e}")
    
    async def set_emergency(self, value: bool) -> None:
        """Set emergency stop state.
        
        Args:
            value: True to activate emergency stop, False to clear emergency stop
        
        Raises:
            TestAPIError: If the API call fails
        """
        url = f"{self.base_url}/test/emergency"
        params = {"value": 1 if value else 0}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, params=params, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise TestAPIError(f"Failed to set emergency: HTTP {resp.status}: {text}")
            except aiohttp.ClientError as e:
                raise TestAPIError(f"Failed to set emergency: {e}")
    
    async def reset_all_test_states(self) -> None:
        """Reset all test states to default (safe) values.
        
        This is useful in test teardown to ensure clean state.
        """
        try:
            await self.set_fault(False)
            await self.set_emergency(False)
            # Note: We don't reset homed here, as it's typically set by actual homing operation
        except TestAPIError:
            # Ignore errors during cleanup
            pass


def get_test_api_url() -> str:
    """Get test API base URL from environment or use default.
    
    Returns:
        Base URL for test API (default: http://localhost:8001)
    """
    import os
    return os.getenv("TEST_API_URL", "http://localhost:8001")

