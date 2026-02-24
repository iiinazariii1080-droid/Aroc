"""Tests for app/middleware/logging.py — request logging middleware."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.testclient import TestClient

from app.middleware.logging import log_requests_middleware

# ── Tiny app wired with the middleware under test ──────────────────────

_test_app = FastAPI()


@_test_app.middleware("http")
async def _mw(request: Request, call_next):
    return await log_requests_middleware(request, call_next)


@_test_app.get("/health")
async def _health():
    return {"ok": True}


@_test_app.get("/api/v1/config/broker")
async def _broker():
    return {"broker": "ok"}


@_test_app.get("/api/v1/private")
async def _private():
    return {"secret": True}


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def api():
    with TestClient(_test_app) as c:
        yield c


# ── Public endpoints skip auth ────────────────────────────────────────


class TestPublicEndpoints:
    @patch("app.middleware.logging.is_public_endpoint", return_value=True)
    def test_public_routes_skip_auth(self, mock_pub, api):
        resp = api.get("/health")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers

    @patch("app.middleware.logging.is_public_endpoint", return_value=True)
    def test_custom_correlation_id_forwarded(self, mock_pub, api):
        resp = api.get("/health", headers={"X-Request-ID": "my-custom-id"})
        assert resp.headers["X-Request-ID"] == "my-custom-id"


# ── Auth-disabled mode ────────────────────────────────────────────────


class TestAuthDisabled:
    @patch("app.middleware.logging.AUTH_DISABLED", True)
    @patch("app.middleware.logging.is_public_endpoint", return_value=False)
    @patch("app.middleware.logging.is_optional_auth_endpoint", return_value=False)
    def test_auth_disabled_grants_admin(self, mock_opt, mock_pub, api):
        resp = api.get("/api/v1/private")
        assert resp.status_code == 200


# ── Optional auth endpoints ───────────────────────────────────────────


class TestOptionalAuth:
    @patch("app.middleware.logging.AUTH_DISABLED", False)
    @patch("app.middleware.logging.is_public_endpoint", return_value=False)
    @patch("app.middleware.logging.is_optional_auth_endpoint", return_value=True)
    def test_no_key_defaults_to_admin(self, mock_opt, mock_pub, api):
        resp = api.get("/api/v1/config/broker")
        assert resp.status_code == 200

    @patch("app.middleware.logging.AUTH_DISABLED", False)
    @patch("app.middleware.logging.is_public_endpoint", return_value=False)
    @patch("app.middleware.logging.is_optional_auth_endpoint", return_value=True)
    @patch("app.middleware.logging.verify_api_key", return_value=None)
    def test_invalid_key_returns_401(self, mock_ver, mock_opt, mock_pub, api):
        resp = api.get(
            "/api/v1/config/broker",
            headers={"X-API-Key": "bad"},
        )
        assert resp.status_code == 401


# ── Strict auth (brute-force) ────────────────────────────────────────


class TestStrictAuth:
    @patch("app.middleware.logging.AUTH_DISABLED", False)
    @patch("app.middleware.logging.is_public_endpoint", return_value=False)
    @patch("app.middleware.logging.is_optional_auth_endpoint", return_value=False)
    @patch(
        "app.middleware.logging.verify_api_key",
        side_effect=HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts",
        ),
    )
    def test_brute_force_returns_429(self, mock_ver, mock_opt, mock_pub, api):
        resp = api.get(
            "/api/v1/private",
            headers={"X-API-Key": "brute"},
        )
        assert resp.status_code == 429
        assert "Too many failed attempts" in resp.json()["detail"]

    @patch("app.middleware.logging.AUTH_DISABLED", False)
    @patch("app.middleware.logging.is_public_endpoint", return_value=False)
    @patch("app.middleware.logging.is_optional_auth_endpoint", return_value=False)
    @patch("app.middleware.logging.verify_api_key")
    def test_valid_key_passes(self, mock_ver, mock_opt, mock_pub, api):
        from app.core.security import Role

        mock_ver.return_value = Role.ADMIN
        resp = api.get(
            "/api/v1/private",
            headers={"X-API-Key": "good"},
        )
        assert resp.status_code == 200
