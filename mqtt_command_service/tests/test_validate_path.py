"""Tests for path validation."""

import pytest
from path_validator import validate_path as _validate_path


class TestValidPaths:
    def test_simple_path(self):
        assert _validate_path("/tasks/navigate") == "/tasks/navigate"

    def test_root(self):
        assert _validate_path("/") == "/"

    def test_nested_path(self):
        assert _validate_path("/api/v1/robot/status") == "/api/v1/robot/status"

    def test_path_without_leading_slash(self):
        result = _validate_path("tasks/navigate")
        assert result is not None
        assert result.startswith("/")

    def test_normalizes_double_slashes(self):
        result = _validate_path("/api//v1///status")
        assert result is not None
        assert "//" not in result

    def test_normalizes_dot_segments(self):
        result = _validate_path("/api/./v1/status")
        assert result == "/api/v1/status"


class TestRejectedPaths:
    def test_null_byte(self):
        assert _validate_path("/path\x00injection") is None

    def test_backslash(self):
        assert _validate_path("/path\\traversal") is None

    def test_at_sign(self):
        assert _validate_path("/path@host") is None

    def test_carriage_return(self):
        assert _validate_path("/path\rinjection") is None

    def test_newline(self):
        assert _validate_path("/path\ninjection") is None

    def test_parent_directory_traversal_rejected(self):
        # Paths that traverse beyond root are rejected to prevent
        # unintended backend endpoint access
        assert _validate_path("/../../etc/passwd") is None

    def test_parent_traversal_at_root_rejected(self):
        assert _validate_path("/../secret") is None

    def test_double_dot_only(self):
        assert _validate_path("..") is None


class TestEdgeCases:
    def test_empty_string(self):
        result = _validate_path("")
        # posixpath.normpath("") returns ".", which gets "/" prepended -> "/."
        # This should not cause traversal
        assert result is not None
        assert not result.startswith("/..")

    def test_single_dot(self):
        result = _validate_path(".")
        assert result is not None
        assert not result.startswith("/..")

    def test_path_with_query_string_chars(self):
        # Query strings are rejected as they shouldn't appear in path segments
        result = _validate_path("/api?query=1")
        assert result is None

    def test_path_with_fragment(self):
        # Fragment chars are rejected as they shouldn't appear in path segments
        result = _validate_path("/api#fragment")
        assert result is None


class TestBlockedPathSegments:
    """Paths whose first segment resembles an OS directory are rejected (SSRF mitigation)."""

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "/proc/self/environ",
            "/sys/class/net",
            "/dev/null",
            "/var/log/syslog",
            "/tmp/sensitive",
            "/root/.ssh/id_rsa",
            "/home/user/.bashrc",
            "/usr/local/bin/python",
        ],
    )
    def test_os_paths_rejected(self, path):
        assert _validate_path(path) is None

    def test_case_insensitive_blocking(self):
        assert _validate_path("/ETC/passwd") is None
        assert _validate_path("/Proc/self") is None

    def test_api_paths_still_allowed(self):
        assert _validate_path("/tasks/navigate") == "/tasks/navigate"
        assert _validate_path("/api/v1/status") == "/api/v1/status"
        assert _validate_path("/move") == "/move"
        assert _validate_path("/status") == "/status"


class TestAllowedPrefixes:
    """Path prefix matching with segment boundary enforcement."""

    def test_prefix_blocks_false_positive(self):
        """'/move' must NOT match '/movement_override'."""
        assert _validate_path("/movement_override", allowed_prefixes=("/move",)) is None

    def test_prefix_exact_match(self):
        """'/move' matches '/move' exactly."""
        assert _validate_path("/move", allowed_prefixes=("/move",)) == "/move"

    def test_prefix_sub_path(self):
        """'/move' matches '/move/forward' (sub-path)."""
        assert _validate_path("/move/forward", allowed_prefixes=("/move",)) == "/move/forward"

    def test_prefix_trailing_slash(self):
        """'/tasks/' matches '/tasks/navigate' (trailing-slash prefix)."""
        assert _validate_path("/tasks/navigate", allowed_prefixes=("/tasks/",)) == "/tasks/navigate"

    def test_prefix_trailing_slash_exact(self):
        """'/tasks/' matches '/tasks' exactly (path without trailing slash)."""
        assert _validate_path("/tasks", allowed_prefixes=("/tasks/",)) == "/tasks"

    def test_no_prefix_match_rejected(self):
        """Path not matching any prefix is rejected."""
        assert _validate_path("/admin/config", allowed_prefixes=("/move", "/status")) is None

    def test_multiple_prefixes_any_match(self):
        """Path matching any one prefix passes."""
        assert _validate_path("/status/health", allowed_prefixes=("/move", "/status")) == "/status/health"

    def test_empty_prefixes_allows_all(self):
        """Empty allowed_prefixes tuple allows all paths."""
        assert _validate_path("/anything/goes") == "/anything/goes"


class TestComputeAllowedHosts:
    """SSRF protection: defense-in-depth filtering of dangerous hosts."""

    def test_cloud_metadata_ip_rejected(self):
        """Service with 169.254.169.254 (AWS metadata) is NOT added to allowed_hosts."""
        from path_validator import compute_allowed_hosts
        from shared.config_types import ServiceConfig

        evil = ServiceConfig(
            name="evil", base_url="http://169.254.169.254",
            watch_tasks=False, allowed_path_prefixes=(),
        )
        hosts = compute_allowed_hosts({"evil": evil})
        assert "169.254.169.254" not in hosts

    def test_localhost_ip_rejected(self):
        """Service with 127.0.0.1 is NOT added to allowed_hosts."""
        from path_validator import compute_allowed_hosts
        from shared.config_types import ServiceConfig

        svc = ServiceConfig(
            name="local", base_url="http://127.0.0.1:9999",
            watch_tasks=False, allowed_path_prefixes=(),
        )
        hosts = compute_allowed_hosts({"local": svc})
        assert "127.0.0.1" not in hosts

    def test_private_10_network_allowed(self):
        """Private 10.x.x.x IPs ARE allowed — operator-configured LAN services are trusted."""
        from path_validator import compute_allowed_hosts
        from shared.config_types import ServiceConfig

        svc = ServiceConfig(
            name="internal", base_url="http://10.0.0.5:8080",
            watch_tasks=False, allowed_path_prefixes=(),
        )
        hosts = compute_allowed_hosts({"internal": svc})
        assert "10.0.0.5" in hosts

    def test_private_192_168_network_allowed(self):
        """Private 192.168.x.x IPs ARE allowed — typical robot LAN addresses."""
        from path_validator import compute_allowed_hosts
        from shared.config_types import ServiceConfig

        svc = ServiceConfig(
            name="robot", base_url="http://192.168.1.55:8110",
            watch_tasks=True, allowed_path_prefixes=("/tasks/",),
        )
        hosts = compute_allowed_hosts({"robot": svc})
        assert "192.168.1.55" in hosts

    def test_named_host_allowed(self):
        """Named hosts (e.g., 'robot') are allowed — DNS checked at request time."""
        from path_validator import compute_allowed_hosts
        from shared.config_types import ServiceConfig

        svc = ServiceConfig(
            name="robot", base_url="http://robot:8110",
            watch_tasks=True, allowed_path_prefixes=("/tasks/",),
        )
        hosts = compute_allowed_hosts({"robot": svc})
        assert "robot" in hosts

    def test_public_ip_allowed(self):
        """Public IP addresses are allowed."""
        from path_validator import compute_allowed_hosts
        from shared.config_types import ServiceConfig

        svc = ServiceConfig(
            name="external", base_url="http://203.0.113.1:8080",
            watch_tasks=False, allowed_path_prefixes=(),
        )
        hosts = compute_allowed_hosts({"external": svc})
        assert "203.0.113.1" in hosts
