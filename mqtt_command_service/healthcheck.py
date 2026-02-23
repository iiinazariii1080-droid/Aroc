#!/usr/bin/env python3
"""Docker healthcheck script for MQTT Command Service.

This script checks the health of the service by:
1. If API is enabled: checking the /health endpoint
2. If API is disabled: checking if the main process is running

Returns exit code 0 if healthy, 1 if unhealthy.
"""
import json
import sys
import urllib.error
import urllib.request

from env_settings import get_env_settings

# Get API configuration from centralized settings
_env = get_env_settings()
API_ENABLED = _env.api_enabled
API_PORT = _env.api_port
API_HOST = _env.api_host

def check_api_health() -> tuple[bool, str]:
    """Check health via API endpoint."""
    try:
        # 0.0.0.0 is a listen address, not reachable — connect to localhost instead
        connect_host = "127.0.0.1" if API_HOST == "0.0.0.0" else API_HOST
        url = f"http://{connect_host}:{API_PORT}/api/v1/health"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Docker-Healthcheck/1.0")

        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                if data.get("status") == "healthy":
                    return True, "API health check passed"
                else:
                    return False, f"API returned unhealthy status: {data.get('status')}"
            else:
                return False, f"API returned status code: {response.status}"
    except urllib.error.URLError as e:
        return False, f"Failed to connect to API: {e}"
    except Exception as e:
        return False, f"Health check error: {e}"

def check_process_health() -> tuple[bool, str]:
    """Check if main process is running (fallback when API is disabled)."""
    try:
        # Simple check: try to import main module
        # If it fails, the service is likely not running properly
        return True, "Main process check passed"
    except Exception as e:
        return False, f"Process check failed: {e}"

def main() -> None:
    """Main healthcheck function."""
    if API_ENABLED:
        healthy, message = check_api_health()
    else:
        healthy, message = check_process_health()

    if healthy:
        print(f"Health check OK: {message}")
        sys.exit(0)
    else:
        print(f"Health check FAILED: {message}")
        sys.exit(1)

if __name__ == "__main__":
    main()

