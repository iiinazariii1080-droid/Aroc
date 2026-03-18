"""Path validation and HTTP URL construction.

Security-focused path normalization and SSRF protection for HTTP requests.
"""

import ipaddress
import logging
import posixpath
from collections.abc import Mapping
from urllib.parse import unquote, urlparse

from shared.config_types import ServiceConfig
from ssrf_guard import _FORBIDDEN_NETWORKS, validate_url_target

logger = logging.getLogger(__name__)

# OS directory prefixes that should never appear as the first segment
# of an API path — blocks SSRF to filesystem paths.
_BLOCKED_PATH_PREFIXES = frozenset(
    {
        "etc",
        "proc",
        "sys",
        "dev",
        "var",
        "tmp",
        "root",
        "home",
        "usr",
    }
)


def _matches_prefix(path: str, prefix: str) -> bool:
    """Check path matches prefix with segment boundary enforcement.

    '/move' matches '/move' and '/move/forward' but NOT '/movement_override'.
    Prefixes ending with '/' use plain startswith (the slash is the boundary).
    """
    if prefix.endswith("/"):
        return path.startswith(prefix) or path == prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def validate_path(path: str, allowed_prefixes: tuple[str, ...] = ()) -> str | None:
    """Validate and normalize a request path.

    Security layers:
    1. Reject dangerous characters (null bytes, backslashes, query/fragment, etc.)
    2. URL-decode to catch %2f-style evasion, then re-check
    3. Reject path traversal ('..') segments
    4. Reject OS filesystem paths (SSRF mitigation)
    5. Normalize with posixpath.normpath
    6. Allowlist check: if the service defines allowed_path_prefixes,
       the normalized path must start with one of them
    """
    if any(c in path for c in ("\x00", "\\", "@", "\r", "\n", ";", "?", "#")):
        return None

    # URL-decode to prevent %2f / double-encoding bypass
    decoded = unquote(unquote(path))  # double-decode to catch double-encoding

    # Re-check dangerous chars after decoding
    if any(c in decoded for c in ("\x00", "\\", "@", "\r", "\n", ";", "?", "#")):
        return None

    # Reject any path containing '..' segments — legitimate API paths
    # never use traversal, and allowing it enables SSRF via path manipulation
    if ".." in decoded.split("/"):
        return None

    normalized = posixpath.normpath(decoded)
    if not normalized.startswith("/"):
        normalized = "/" + normalized

    # Reject OS filesystem paths — the first non-empty segment must not
    # be a well-known OS directory (case-insensitive).
    segments = [s for s in normalized.split("/") if s]
    if segments and segments[0].lower() in _BLOCKED_PATH_PREFIXES:
        return None

    # Allowlist: if service defines permitted prefixes, enforce them
    if allowed_prefixes:
        if not any(_matches_prefix(normalized, prefix) for prefix in allowed_prefixes):
            logger.warning(
                "[bridge] Path %r rejected: not in allowed prefixes %s",
                normalized,
                allowed_prefixes,
            )
            return None

    return normalized


def _is_forbidden_ip(hostname: str) -> bool:
    """Check if hostname is a literal IP address in a forbidden network."""
    try:
        addr = ipaddress.ip_address(hostname)
        for network in _FORBIDDEN_NETWORKS:
            if addr in network:
                return True
    except ValueError:
        pass  # Not an IP literal (e.g., "robot") — allowed
    return False


_NEVER_ALLOWED_HOSTS = frozenset({
    "169.254.169.254",  # AWS/GCP instance metadata
    "metadata.google.internal",
    "127.0.0.1",  # Loopback — services should use real LAN IPs
    "::1",
    "localhost",
})


def compute_allowed_hosts(services: Mapping[str, ServiceConfig]) -> frozenset[str]:
    """Pre-compute the set of allowed hostnames from service configs.

    Configured service hosts are trusted (including private IPs like
    192.168.x.x).  Cloud metadata endpoints and loopback addresses are
    excluded as a defense-in-depth measure.
    """
    safe_hosts: set[str] = set()
    for cfg in services.values():
        if not cfg.base_url:
            continue
        hostname = urlparse(cfg.base_url).hostname
        if hostname and hostname not in _NEVER_ALLOWED_HOSTS:
            safe_hosts.add(hostname)
    return frozenset(safe_hosts)


def build_http_url(
    service: str,
    path: str,
    services: Mapping[str, ServiceConfig],
    allowed_hosts: frozenset[str] | None = None,
) -> str | None:
    """Build and validate an HTTP URL for a service request.

    Returns None if service is unknown or URL fails SSRF validation.
    Pass pre-computed *allowed_hosts* to avoid recomputing on every call.

    When the target hostname is not in *allowed_hosts* and DNS resolution
    is performed, the resolved IP is pinned into the returned URL to
    prevent TOCTOU DNS rebinding between validation and the HTTP request.
    """
    service_cfg = services.get(service)
    if not service_cfg:
        return None
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{service_cfg.base_url}{normalized_path}"
    # SSRF protection: validate resolved IP is not in forbidden ranges.
    # Service hosts from config are auto-allowed.
    if allowed_hosts is None:
        allowed_hosts = compute_allowed_hosts(services)
    safe, reason, resolved_ip = validate_url_target(url, allowed_hosts=allowed_hosts)
    if not safe:
        logger.warning("[bridge] SSRF blocked: %s — %s", url, reason)
        return None
    # Pin the resolved IP into the URL to prevent DNS rebinding.
    # For allowed hosts (resolved_ip=None), keep the original hostname.
    if resolved_ip:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname and hostname != resolved_ip:
            url = url.replace(f"//{hostname}", f"//{resolved_ip}", 1)
    return url
