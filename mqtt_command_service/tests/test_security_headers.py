"""Tests for app/middleware/security_headers.py."""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.security_headers import add_security_headers_middleware

_app = FastAPI()


@_app.middleware("http")
async def _mw(request: Request, call_next):
    return await add_security_headers_middleware(request, call_next)


@_app.get("/api/v1/data")
async def _data():
    return {"v": 1}


@_app.get("/docs")
async def _docs():
    return {"docs": True}


@pytest.fixture()
def api():
    with TestClient(_app) as c:
        yield c


EXPECTED_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


class TestSecurityHeaders:
    def test_standard_headers_present(self, api):
        resp = api.get("/api/v1/data")
        for header, value in EXPECTED_HEADERS.items():
            assert resp.headers.get(header) == value, f"Missing or wrong: {header}"

    def test_strict_csp_on_api(self, api):
        resp = api.get("/api/v1/data")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'none'" in csp

    def test_relaxed_csp_on_docs(self, api):
        resp = api.get("/docs")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "cdn.jsdelivr.net" in csp

    def test_cache_control_set(self, api):
        resp = api.get("/api/v1/data")
        assert "no-store" in resp.headers.get("Cache-Control", "")
        assert resp.headers.get("Pragma") == "no-cache"
