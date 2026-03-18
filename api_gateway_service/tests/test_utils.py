"""Unit tests for app.core.utils — pure functions, no I/O."""

import pytest

from app.core.http_proxy_utils import (
    join_url,
    filter_response_headers,
    forward_request_headers,
)
from app.core.openapi_utils import (
    strip_prefix,
    rename_component_refs,
    merge_component_sections,
)


# ── join_url ──────────────────────────────────────────────

class TestJoinUrl:
    def test_simple(self):
        assert join_url("http://host:8000", "api", "v1") == "http://host:8000/api/v1"

    def test_trailing_slash_stripped(self):
        assert join_url("http://host:8000/", "api") == "http://host:8000/api"

    def test_leading_slash_stripped(self):
        assert join_url("http://host:8000", "/api/v1") == "http://host:8000/api/v1"

    def test_empty_parts_skipped(self):
        assert join_url("http://host", "", "path") == "http://host/path"

    def test_no_parts(self):
        assert join_url("http://host:8000/") == "http://host:8000"

    def test_double_slash_collapsed(self):
        assert join_url("http://host/", "/api") == "http://host/api"


# ── filter_response_headers ──────────────────────────────

class TestFilterResponseHeaders:
    def test_removes_hop_by_hop(self):
        import httpx
        headers = httpx.Headers({
            "content-type": "application/json",
            "transfer-encoding": "chunked",
            "connection": "keep-alive",
            "x-custom": "value",
        })
        filtered = filter_response_headers(headers)
        assert "content-type" in filtered
        assert "x-custom" in filtered
        assert "transfer-encoding" not in filtered
        assert "connection" not in filtered

    def test_removes_content_length(self):
        import httpx
        headers = httpx.Headers({"content-length": "42", "x-ok": "yes"})
        filtered = filter_response_headers(headers)
        assert "content-length" not in filtered
        assert "x-ok" in filtered


# ── forward_request_headers ──────────────────────────────

class TestForwardRequestHeaders:
    def test_strips_host_and_content_length(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {
            "host": "gateway:8201",
            "content-length": "100",
            "accept-encoding": "gzip",
            "authorization": "Bearer token",
            "x-custom": "val",
        }
        request.scope = {"state": {"request_id": "test-rid-123"}}
        fwd = forward_request_headers(request)
        assert "host" not in fwd
        assert "content-length" not in fwd
        assert "accept-encoding" not in fwd
        assert fwd["authorization"] == "Bearer token"
        assert fwd["x-custom"] == "val"
        assert fwd["x-request-id"] == "test-rid-123"

    def test_propagates_request_id_from_scope(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {"x-foo": "bar"}
        request.scope = {"state": {"request_id": "abc-123"}}
        fwd = forward_request_headers(request)
        assert fwd["x-request-id"] == "abc-123"

    def test_no_request_id_when_scope_empty(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.headers = {"x-foo": "bar"}
        request.scope = {}
        fwd = forward_request_headers(request)
        assert "x-request-id" not in fwd


# ── strip_prefix ─────────────────────────────────────────

class TestStripPrefix:
    def test_exact_match(self):
        assert strip_prefix("/api/v1", "/api/v1") == "/"

    def test_with_trailing_path(self):
        assert strip_prefix("/api/v1/users", "/api/v1") == "/users"

    def test_no_match(self):
        assert strip_prefix("/other/path", "/api/v1") == "/other/path"

    def test_empty_prefix(self):
        assert strip_prefix("/some/path", "") == "/some/path"


# ── rename_component_refs ────────────────────────────────

class TestRenameComponentRefs:
    def test_renames_ref(self):
        obj = {"$ref": "#/components/schemas/MyModel"}
        result = rename_component_refs(obj, "svc_")
        assert result["$ref"] == "#/components/schemas/svc_MyModel"

    def test_nested_dict(self):
        obj = {"properties": {"item": {"$ref": "#/components/schemas/Item"}}}
        result = rename_component_refs(obj, "x_")
        assert result["properties"]["item"]["$ref"] == "#/components/schemas/x_Item"

    def test_list(self):
        obj = [{"$ref": "#/components/schemas/A"}, {"$ref": "#/components/schemas/B"}]
        result = rename_component_refs(obj, "p_")
        assert result[0]["$ref"] == "#/components/schemas/p_A"
        assert result[1]["$ref"] == "#/components/schemas/p_B"

    def test_non_component_ref_unchanged(self):
        obj = {"$ref": "#/other/thing"}
        result = rename_component_refs(obj, "p_")
        assert result["$ref"] == "#/other/thing"

    def test_scalar_passthrough(self):
        assert rename_component_refs(42, "p_") == 42
        assert rename_component_refs("hello", "p_") == "hello"


# ── merge_component_sections ─────────────────────────────

class TestMergeComponentSections:
    def test_merge_new_section(self):
        target = {}
        source = {"schemas": {"Model": {"type": "object"}}}
        merge_component_sections(target, source)
        assert target["schemas"]["Model"]["type"] == "object"

    def test_merge_existing_section(self):
        target = {"schemas": {"A": {"type": "string"}}}
        source = {"schemas": {"B": {"type": "integer"}}}
        merge_component_sections(target, source)
        assert "A" in target["schemas"]
        assert "B" in target["schemas"]

    def test_overwrites_on_conflict(self):
        target = {"schemas": {"X": {"type": "string"}}}
        source = {"schemas": {"X": {"type": "integer"}}}
        merge_component_sections(target, source)
        assert target["schemas"]["X"]["type"] == "integer"


# ── atomic_write ────────────────────────────────────────

class TestAtomicWrite:
    def test_roundtrip(self, tmp_path):
        from app.core.utils import atomic_write
        p = tmp_path / "state.json"
        atomic_write(p, '{"key": "value"}')
        assert p.read_text(encoding="utf-8") == '{"key": "value"}'

    def test_no_tmp_leftover(self, tmp_path):
        from app.core.utils import atomic_write
        p = tmp_path / "data.txt"
        atomic_write(p, "hello")
        assert not p.with_suffix(".tmp").exists()

    def test_creates_parent_dirs(self, tmp_path):
        from app.core.utils import atomic_write
        p = tmp_path / "sub" / "dir" / "file.txt"
        atomic_write(p, "nested")
        assert p.read_text(encoding="utf-8") == "nested"

    def test_overwrite(self, tmp_path):
        from app.core.utils import atomic_write
        p = tmp_path / "over.txt"
        atomic_write(p, "first")
        atomic_write(p, "second")
        assert p.read_text(encoding="utf-8") == "second"
