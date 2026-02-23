#!/usr/bin/env python3
"""
Full test suite for verifying all architecture requirements.

This script runs:
1. Unit tests for all new services
2. Integration tests
3. Requirements compliance checks
"""
import os
import subprocess
import sys
from pathlib import Path


# Output colors
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    """Print formatted header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text.center(70)}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}\n")

def print_success(text):
    """Print success message."""
    print(f"{Colors.GREEN}[OK] {text}{Colors.RESET}")

def print_error(text):
    """Print error message."""
    print(f"{Colors.RED}[FAIL] {text}{Colors.RESET}")

def print_warning(text):
    """Print warning message."""
    print(f"{Colors.YELLOW}[WARN] {text}{Colors.RESET}")

def print_info(text):
    """Print info message."""
    print(f"{Colors.BLUE}i {text}{Colors.RESET}")

def run_command(cmd, description):
    """Run command and return success status."""
    print_info(f"Running: {description}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode == 0:
            print_success(f"{description} - PASSED")
            if result.stdout:
                # Show last few lines of output
                lines = result.stdout.strip().split('\n')
                if len(lines) > 5:
                    print("  ...")
                    for line in lines[-5:]:
                        print(f"  {line}")
                else:
                    for line in lines:
                        print(f"  {line}")
            return True
        else:
            print_error(f"{description} - FAILED")
            if result.stderr:
                print("  Error output:")
                for line in result.stderr.strip().split('\n')[-10:]:
                    print(f"  {line}")
            if result.stdout:
                print("  Output:")
                for line in result.stdout.strip().split('\n')[-10:]:
                    print(f"  {line}")
            return False
    except subprocess.TimeoutExpired:
        print_error(f"{description} - TIMEOUT (exceeded 5 minutes)")
        return False
    except Exception as e:
        print_error(f"{description} - EXCEPTION: {e}")
        return False

def main():
    """Run full test suite."""
    print_header("MQTT Command Service - Full Test Suite")

    # Change to project root
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    results = []

    # 1. Unit Tests
    print_header("1. Unit Tests")

    results.append((
        "ConfigService Tests",
        run_command(
            "python -m pytest tests/test_config_service.py -v",
            "ConfigService unit tests"
        )
    ))

    results.append((
        "MQTT Client Service Tests",
        run_command(
            "python -m pytest tests/test_mqtt_client_service.py -v",
            "UnifiedMQTTClient unit tests"
        )
    ))

    results.append((
        "Lifecycle Manager Tests",
        run_command(
            "python -m pytest tests/test_lifecycle.py -v",
            "LifecycleManager unit tests"
        )
    ))

    # 2. Integration Tests
    print_header("2. Integration Tests")

    results.append((
        "Config-MQTT Integration Tests",
        run_command(
            "python -m pytest tests/integration_test_config_mqtt.py -v",
            "ConfigService and MQTTClient integration tests"
        )
    ))

    # 3. Code Quality Checks
    print_header("3. Code Quality Checks")

    # Check for linter errors
    try:
        import ast

        test_files = [
            "app/services/config_service.py",
            "app/services/mqtt_client_service.py",
            "app/core/lifecycle.py",
        ]

        syntax_errors = []
        for file_path in test_files:
            full_path = project_root / file_path
            if full_path.exists():
                try:
                    with open(full_path, encoding='utf-8') as f:
                        ast.parse(f.read())
                except SyntaxError as e:
                    syntax_errors.append(f"{file_path}: {e}")

        if syntax_errors:
            print_error("Syntax errors found:")
            for error in syntax_errors:
                print(f"  {error}")
            results.append(("Syntax Check", False))
        else:
            print_success("No syntax errors found")
            results.append(("Syntax Check", True))
    except Exception as e:
        print_warning(f"Could not check syntax: {e}")
        results.append(("Syntax Check", None))

    # 4. Summary
    print_header("Test Summary")

    total = len(results)
    passed = sum(1 for _, result in results if result is True)
    failed = sum(1 for _, result in results if result is False)
    skipped = sum(1 for _, result in results if result is None)

    print(f"\nTotal tests: {total}")
    print_success(f"Passed: {passed}")
    if failed > 0:
        print_error(f"Failed: {failed}")
    if skipped > 0:
        print_warning(f"Skipped: {skipped}")

    print("\nDetailed results:")
    for name, result in results:
        if result is True:
            print_success(f"  {name}")
        elif result is False:
            print_error(f"  {name}")
        else:
            print_warning(f"  {name} (skipped)")

    # Final status
    print_header("Final Status")

    if failed == 0:
        print_success("All tests PASSED!")
        return 0
    else:
        print_error(f"{failed} test suite(s) FAILED!")
        return 1

if __name__ == "__main__":
    sys.exit(main())

