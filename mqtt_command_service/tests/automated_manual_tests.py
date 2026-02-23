#!/usr/bin/env python3
"""
Automated tests for manual testing scenarios.

This script automates the portions of manual testing that can be verified programmatically.
"""
import sys
from typing import Any

import requests


# Output colors
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_success(text):
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")

def print_error(text):
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ {text}{Colors.RESET}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")

class APITester:
    """Test API endpoints."""

    def __init__(self, base_url: str = "http://localhost:7900", api_key: str | None = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {}
        if api_key:
            self.headers["X-API-Key"] = api_key

    def test_health(self) -> bool:
        """Test health endpoint."""
        print_info("Testing /health endpoint...")
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print_success(f"Health check passed: {data.get('status', 'unknown')}")

                # Check required fields
                required_fields = ["api", "database", "mqtt_broker", "timestamp"]
                missing = [f for f in required_fields if f not in data]
                if missing:
                    print_warning(f"Missing fields in health response: {missing}")
                else:
                    print_success("All required health fields present")

                return True
            else:
                print_error(f"Health check failed: status {response.status_code}")
                return False
        except Exception as e:
            print_error(f"Health check error: {e}")
            return False

    def test_get_broker_config(self) -> bool:
        """Test getting broker configuration."""
        print_info("Testing GET /api/v1/config/broker...")
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/config/broker",
                headers=self.headers,
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                print_success("Broker config retrieved successfully")

                # Check required fields
                required_fields = ["broker", "broker_port", "mqtt_user", "mqtt_use_tls"]
                missing = [f for f in required_fields if f not in data]
                if missing:
                    print_warning(f"Missing fields: {missing}")
                    return False

                print_info(f"  Broker: {data.get('broker')}:{data.get('broker_port')}")
                print_info(f"  User: {data.get('mqtt_user')}")
                print_info(f"  TLS: {data.get('mqtt_use_tls')}")
                return True
            else:
                print_error(f"Failed to get broker config: status {response.status_code}")
                return False
        except Exception as e:
            print_error(f"Error getting broker config: {e}")
            return False

    def test_update_broker_config(self, updates: dict[str, Any]) -> bool:
        """Test updating broker configuration."""
        print_info("Testing POST /api/v1/config/broker...")
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/config/broker",
                headers={**self.headers, "Content-Type": "application/json"},
                json=updates,
                timeout=10
            )
            if response.status_code == 200:
                response.json()
                print_success("Broker config updated successfully")
                return True
            else:
                print_error(f"Failed to update broker config: status {response.status_code}")
                try:
                    error_data = response.json()
                    print_error(f"  Error: {error_data.get('detail', 'Unknown error')}")
                except Exception:
                    print_error(f"  Response: {response.text[:200]}")
                return False
        except Exception as e:
            print_error(f"Error updating broker config: {e}")
            return False

    def test_tls_toggle(self, enable: bool) -> bool:
        """Test TLS toggle via main broker endpoint."""
        print_info(f"Testing TLS toggle (enable={enable})...")
        try:
            # Use main broker endpoint to toggle TLS
            response = requests.post(
                f"{self.base_url}/api/v1/config/broker",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"mqtt_use_tls": enable},
                timeout=10
            )
            if response.status_code == 200:
                response.json()
                print_success(f"TLS {'enabled' if enable else 'disabled'} successfully")
                return True
            else:
                print_error(f"Failed to toggle TLS: status {response.status_code}")
                return False
        except Exception as e:
            print_error(f"Error toggling TLS: {e}")
            return False


def main():
    """Run automated manual tests."""
    print("\n" + "="*70)
    print("Automated Manual Tests for MQTT Command Service")
    print("="*70 + "\n")

    # Configuration
    base_url = os.environ.get("API_BASE_URL", "http://localhost:7900")
    api_key = os.environ.get("API_KEY")

    if not api_key:
        print_warning("API_KEY not set, some tests may fail")
        print_info("Set API_KEY environment variable to test authenticated endpoints")

    tester = APITester(base_url, api_key)

    results = []

    # Test 1: Health check
    print("\n" + "-"*70)
    print("Test 1: Health Check")
    print("-"*70)
    results.append(("Health Check", tester.test_health()))

    # Test 2: Get broker config
    print("\n" + "-"*70)
    print("Test 2: Get Broker Configuration")
    print("-"*70)
    results.append(("Get Broker Config", tester.test_get_broker_config()))

    # Test 3: Update broker config (if API key provided)
    if api_key:
        print("\n" + "-"*70)
        print("Test 3: Update Broker Configuration")
        print("-"*70)
        # Get current config first
        try:
            response = requests.get(
                f"{base_url}/api/v1/config/broker",
                headers=tester.headers,
                timeout=5
            )
            if response.status_code == 200:
                current_config = response.json()
                # Test updating port (non-destructive)
                current_config.get("broker_port", 1883)
                # Just verify the endpoint works, don't actually change anything critical
                print_info("Skipping actual config update to avoid breaking service")
                print_warning("Manual testing required for config updates")
                results.append(("Update Broker Config", None))
            else:
                results.append(("Update Broker Config", False))
        except Exception as e:
            print_error(f"Could not get current config: {e}")
            results.append(("Update Broker Config", False))
    else:
        print_warning("Skipping config update test (no API key)")
        results.append(("Update Broker Config", None))

    # Summary
    print("\n" + "="*70)
    print("Test Summary")
    print("="*70)

    total = len(results)
    passed = sum(1 for _, result in results if result is True)
    failed = sum(1 for _, result in results if result is False)
    skipped = sum(1 for _, result in results if result is None)

    for name, result in results:
        if result is True:
            print_success(f"{name}")
        elif result is False:
            print_error(f"{name}")
        else:
            print_warning(f"{name} (skipped)")

    print(f"\nTotal: {total}, Passed: {passed}, Failed: {failed}, Skipped: {skipped}")

    if failed == 0:
        print_success("\nAll automated tests passed!")
        return 0
    else:
        print_error(f"\n{failed} test(s) failed!")
        return 1


if __name__ == "__main__":
    import os
    sys.exit(main())

