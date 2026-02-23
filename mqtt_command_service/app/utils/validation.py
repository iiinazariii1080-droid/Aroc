"""Validation utilities."""
import ipaddress
import re

# RFC 1123 constants
MAX_HOSTNAME_LENGTH = 253
MAX_LABEL_LENGTH = 63
HOSTNAME_LABEL_PATTERN = re.compile(
    r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$'
)


def validate_hostname(hostname: str) -> tuple[bool, str | None]:
    """
    Validate hostname according to RFC 1123.

    Args:
        hostname: Hostname to validate

    Returns:
        (is_valid, error_message) tuple
        - is_valid: True if valid, False otherwise
        - error_message: Error message if invalid, None if valid
    """
    if not hostname:
        return False, "Hostname cannot be empty"

    # Check total length
    if len(hostname) > MAX_HOSTNAME_LENGTH:
        return False, (
            f"Hostname too long: {len(hostname)} characters "
            f"(max {MAX_HOSTNAME_LENGTH})"
        )

    # Split into labels
    labels = hostname.split('.')

    # Check each label
    for _i, label in enumerate(labels):
        # Check label length
        if len(label) > MAX_LABEL_LENGTH:
            return False, (
                f"Label '{label}' too long: {len(label)} characters "
                f"(max {MAX_LABEL_LENGTH})"
            )

        # Empty labels are not allowed (except for root domain, but we don't support that)
        if not label:
            return False, "Empty label in hostname"

        # Check label format
        if not HOSTNAME_LABEL_PATTERN.match(label):
            return False, (
                f"Invalid label format: '{label}'. "
                "Labels must start and end with alphanumeric characters, "
                "and can contain hyphens in the middle."
            )

    return True, None


def validate_broker_address(address: str) -> tuple[bool, str | None]:
    """
    Validate broker address (IP or hostname).

    Supports:
    - IPv4 addresses
    - IPv6 addresses (with or without brackets)
    - Hostnames (RFC 1123)

    Args:
        address: Address to validate

    Returns:
        (is_valid, error_message) tuple
        - is_valid: True if valid, False otherwise
        - error_message: Error message if invalid, None if valid
    """
    if not address:
        return False, "Broker address cannot be empty"

    address = address.strip()
    if not address:
        return False, "Broker address cannot be empty"

    # Try IPv4/IPv6 first (without brackets)
    try:
        ipaddress.ip_address(address)
        return True, None
    except ValueError:
        pass

    # Try IPv6 with brackets (e.g., [2001:db8::1])
    if address.startswith('[') and address.endswith(']'):
        try:
            ipv6 = address[1:-1]
            ipaddress.IPv6Address(ipv6)
            return True, None
        except ValueError:
            pass

    # Validate as hostname
    # Additional guard: disallow single-label hostnames without a dot,
    # except for well-known "localhost". This prevents values like "1"
    # or other obviously invalid broker names from passing validation.
    if "." not in address and address.lower() != "localhost":
        return False, "Broker hostname must contain a dot or be 'localhost'"
    # Disallow single-label that is only digits (would be invalid host and not an IP)
    if address.isdigit():
        return False, "Broker hostname cannot be only digits"
    return validate_hostname(address)

