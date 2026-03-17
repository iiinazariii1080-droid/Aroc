"""L5 Security boundary tests — admin auth, CSP, CORS.

Validates that:
- Admin routes require valid X-Admin-Token (L5-SEC-02)
- CSP frame-ancestors allows only authorized origins (L5-IFRAME-03)
- CORS allows exact origins via regex, rejects others (L5-CORS-05)

Markers: security, unit
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings

_ADMIN_TOKEN = "test-secure-token-32chars-long!!"


@pytest.fixture
def app_enforced(tmp_path):
    """Create app with admin enforcement ON and a known token."""
    settings = make_test_settings(tmp_path, admin_token=_ADMIN_TOKEN, admin_enforce=True)
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch("app.core.admin.get_settings", return_value=settings):
        from app.core.app import create_app
        yield create_app()


@pytest.fixture
async def client_enforced(app_enforced):
    transport = ASGITransport(app=app_enforced)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def app_default_token(tmp_path):
    """Create app where CAM_ADMIN_TOKEN is empty (not configured)."""
    settings = make_test_settings(tmp_path, admin_token="", admin_enforce=True)
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch("app.core.admin.get_settings", return_value=settings):
        from app.core.app import create_app
        yield create_app()


@pytest.fixture
async def client_default_token(app_default_token):
    transport = ASGITransport(app=app_default_token)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Admin auth tests ──────────────────────────────────────────────────

@pytest.mark.security
class TestAdminAuth:
    """L5-SEC-02: Admin routes reject without valid token."""

    async def test_admin_route_no_token_returns_403(self, client_enforced):
        """Admin route without X-Admin-Token → 403."""
        resp = await client_enforced.post("/janus/restart")
        assert resp.status_code == 403

    async def test_admin_route_wrong_token_returns_403(self, client_enforced):
        """Admin route with wrong token → 403."""
        resp = await client_enforced.post(
            "/janus/restart",
            headers={"X-Admin-Token": "wrong-token"},
        )
        assert resp.status_code == 403

    async def test_admin_route_correct_token_accepted(self, client_enforced):
        """Admin route with correct token → not 403/401."""
        resp = await client_enforced.post(
            "/janus/restart",
            headers={"X-Admin-Token": _ADMIN_TOKEN},
        )
        # May be 500/502 (Janus not running), but NOT 403
        assert resp.status_code != 403
        assert resp.status_code != 401

    async def test_admin_nat_route_no_token_returns_403(self, client_enforced):
        """GET /janus/nat without token → 403."""
        resp = await client_enforced.get("/janus/nat")
        assert resp.status_code == 403

    async def test_admin_camera_config_no_token_returns_403(self, client_enforced):
        """GET /admin/camera/config without token → 403."""
        resp = await client_enforced.get("/admin/camera/config")
        assert resp.status_code == 403

    async def test_default_token_returns_503(self, client_default_token):
        """When CAM_ADMIN_TOKEN is empty (not set), admin routes → 503."""
        resp = await client_default_token.post(
            "/janus/restart",
            headers={"X-Admin-Token": "anything"},
        )
        assert resp.status_code == 503
        assert "not set" in resp.json()["detail"].lower() or "disabled" in resp.json()["detail"].lower()

    async def test_public_routes_no_admin_required(self, client_enforced):
        """Public routes like /healthz don't require admin token."""
        resp = await client_enforced.get("/healthz")
        assert resp.status_code != 403
        assert resp.status_code != 401


# ── CSP frame-ancestors tests ─────────────────────────────────────────

@pytest.mark.security
class TestCSPFrameAncestors:
    """L5-IFRAME-03: CSP frame-ancestors allows only authorized parents."""

    async def test_csp_header_present(self, client_enforced):
        """Every response has Content-Security-Policy header."""
        resp = await client_enforced.get("/healthz")
        assert "content-security-policy" in resp.headers

    async def test_frame_ancestors_includes_self(self, client_enforced):
        """frame-ancestors includes 'self'."""
        resp = await client_enforced.get("/healthz")
        csp = resp.headers["content-security-policy"]
        assert "'self'" in csp
        assert "frame-ancestors" in csp

    async def test_frame_ancestors_includes_techvisioncloud(self, client_enforced):
        """frame-ancestors includes techvisioncloud.pl wildcard."""
        resp = await client_enforced.get("/healthz")
        csp = resp.headers["content-security-policy"]
        assert "techvisioncloud.pl" in csp

    async def test_frame_ancestors_includes_lan_nodes(self, client_enforced):
        """frame-ancestors includes LAN node origins."""
        resp = await client_enforced.get("/healthz")
        csp = resp.headers["content-security-policy"]
        assert "192.168.1.10" in csp

    async def test_no_x_frame_options_header(self, client_enforced):
        """X-Frame-Options is NOT set (CSP frame-ancestors supersedes it)."""
        resp = await client_enforced.get("/healthz")
        assert "x-frame-options" not in resp.headers

    async def test_security_headers_present(self, client_enforced):
        """X-Content-Type-Options and other security headers present."""
        resp = await client_enforced.get("/healthz")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert "referrer-policy" in resp.headers
        assert "permissions-policy" in resp.headers


# ── CORS tests ────────────────────────────────────────────────────────

@pytest.mark.security
class TestCORS:
    """L5-CORS-05: CORS allows exact origins via regex, rejects wildcards."""

    async def test_cors_allows_localhost(self, client_enforced):
        """CORS allows http://localhost:8900."""
        resp = await client_enforced.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:8900",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8900"

    async def test_cors_allows_lan_node(self, client_enforced):
        """CORS allows http://192.168.1.10:8900."""
        resp = await client_enforced.options(
            "/healthz",
            headers={
                "Origin": "http://192.168.1.10:8900",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://192.168.1.10:8900"

    async def test_cors_allows_techvisioncloud(self, client_enforced):
        """CORS allows https://api.techvisioncloud.pl."""
        resp = await client_enforced.options(
            "/healthz",
            headers={
                "Origin": "https://api.techvisioncloud.pl",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "https://api.techvisioncloud.pl"

    async def test_cors_rejects_unknown_origin(self, client_enforced):
        """CORS does NOT allow an unknown origin."""
        resp = await client_enforced.options(
            "/healthz",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # No allow-origin header or not matching evil.example.com
        allow = resp.headers.get("access-control-allow-origin", "")
        assert "evil.example.com" not in allow

    async def test_cors_rejects_subdomain_injection(self, client_enforced):
        """CORS does NOT allow a crafted subdomain that mimics LAN."""
        resp = await client_enforced.options(
            "/healthz",
            headers={
                "Origin": "http://192.168.1.10.evil.com:8900",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow = resp.headers.get("access-control-allow-origin", "")
        assert "evil.com" not in allow

    async def test_cors_credentials_allowed(self, client_enforced):
        """CORS allows credentials for matching origins."""
        resp = await client_enforced.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:8900",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-credentials") == "true"
