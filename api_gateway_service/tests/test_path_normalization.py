"""Unit tests for proxy path normalization — the substring bug fix."""

from app.routers.proxy_http import _normalize_upstream_path


class TestNormalizeUpstreamPath:
    def test_empty_path(self):
        assert _normalize_upstream_path("xarm", "") == ""

    def test_simple_path(self):
        assert _normalize_upstream_path("xarm", "status") == "status"

    def test_strips_duplicate_prefix(self):
        assert _normalize_upstream_path("xarm", "api/v1/xarm/status") == "status"

    def test_strips_duplicate_with_leading_slash(self):
        assert _normalize_upstream_path("xarm", "/api/v1/xarm/status") == "status"

    def test_exact_duplicate_no_trailing(self):
        assert _normalize_upstream_path("xarm", "api/v1/xarm") == ""

    def test_does_not_strip_substring_match(self):
        """Regression: 'xarm_legacy' should NOT be treated as 'xarm' prefix."""
        result = _normalize_upstream_path("xarm", "api/v1/xarm_legacy/status")
        assert result == "api/v1/xarm_legacy/status"

    def test_does_not_strip_partial_service_name(self):
        result = _normalize_upstream_path("igus", "api/v1/igus_v2/endpoint")
        assert result == "api/v1/igus_v2/endpoint"

    def test_normal_path_no_prefix(self):
        assert _normalize_upstream_path("robot", "joints/list") == "joints/list"

    def test_deep_nested_path(self):
        result = _normalize_upstream_path("robot", "api/v1/robot/a/b/c")
        assert result == "a/b/c"
