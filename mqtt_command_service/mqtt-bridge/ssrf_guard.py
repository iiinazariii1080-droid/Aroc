"""SSRF protection: validate URL targets against forbidden IP ranges."""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Private/internal IP ranges that should never be targeted via SSRF
_FORBIDDEN_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fd00::/8"),
    ipaddress.ip_network("fe80::/10"),
]


def validate_url_target(
    url: str,
    allowed_hosts: frozenset[str] = frozenset(),
) -> tuple[bool, str | None, str | None]:
    """Validate that a URL does not target a forbidden internal IP range.

    Args:
        url: The URL to validate.
        allowed_hosts: Hostnames explicitly allowed (e.g., configured service hosts).

    Returns:
        (True, None, resolved_ip) if safe (resolved_ip is the first safe IP,
            or None for allowed hosts that skip DNS resolution),
        (False, reason, None) if blocked.

    The caller should use *resolved_ip* to pin the DNS result and prevent
    TOCTOU rebinding between validation and the actual HTTP request.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, "no hostname in URL", None

        # Skip validation for explicitly allowed hosts — these are from
        # the service config and trusted. No DNS pinning needed.
        if hostname in allowed_hosts:
            return True, None, None

        # Resolve hostname to IP addresses
        try:
            addr_infos = socket.getaddrinfo(hostname, parsed.port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as e:
            return False, f"DNS resolution failed: {e}", None

        first_safe_ip: str | None = None
        for _family, _type, _proto, _canonname, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            for network in _FORBIDDEN_NETWORKS:
                if addr in network:
                    return False, f"resolved to forbidden IP {ip_str} (in {network})", None

            if first_safe_ip is None:
                first_safe_ip = ip_str

        return True, None, first_safe_ip

    except Exception as e:
        return False, f"URL validation error: {e}", None
