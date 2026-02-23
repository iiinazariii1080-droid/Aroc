"""Tests for middleware modules: rate_limit, version, metrics, logging."""
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Rate limit middleware
# ---------------------------------------------------------------------------

class TestRateLimitMiddleware:
    """Tests for app.middleware.rate_limit."""

    def test_get_limiter_creates_singleton(self):
        import app.middleware.rate_limit as mod
        # Reset singleton
        mod._limiter = None
        lim1 = mod.get_limiter()
        lim2 = mod.get_limiter()
        assert lim1 is lim2

    def test_setup_rate_limiting_attaches_limiter(self):
        from app.middleware.rate_limit import setup_rate_limiting
        app_mock = MagicMock()
        limiter = setup_rate_limiting(app_mock)
        assert app_mock.state.limiter is limiter
        app_mock.add_exception_handler.assert_called_once()


# ---------------------------------------------------------------------------
# Version middleware
# ---------------------------------------------------------------------------

class TestVersionMiddleware:
    """Tests for app.middleware.version."""

    @pytest.mark.asyncio
    async def test_adds_api_version_header(self):
        from app.middleware.version import add_api_version_middleware
        request = MagicMock()
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)
        result = await add_api_version_middleware(request, call_next)
        assert "API-Version" in result.headers
        call_next.assert_awaited_once_with(request)


# ---------------------------------------------------------------------------
# Prometheus metrics middleware
# ---------------------------------------------------------------------------

class TestPrometheusMiddleware:
    """Tests for app.middleware.metrics."""

    def test_normalize_path_static(self):
        from app.middleware.metrics import PrometheusMiddleware
        mw = PrometheusMiddleware.__new__(PrometheusMiddleware)
        assert mw._normalize_path("/api/v1/config/broker") == "/api/v1/config/broker"
        assert mw._normalize_path("/health") == "/health"

    def test_normalize_path_replaces_api_key_id(self):
        from app.middleware.metrics import PrometheusMiddleware
        mw = PrometheusMiddleware.__new__(PrometheusMiddleware)
        assert mw._normalize_path("/api/v1/auth/api-keys/abc123") == "/api/v1/auth/api-keys/{key_id}"

    def test_normalize_path_replaces_task_id(self):
        from app.middleware.metrics import PrometheusMiddleware
        mw = PrometheusMiddleware.__new__(PrometheusMiddleware)
        assert mw._normalize_path("/api/v1/tasks/xyz99") == "/api/v1/tasks/{task_id}"

    def test_normalize_path_replaces_certificate_action(self):
        from app.middleware.metrics import PrometheusMiddleware
        mw = PrometheusMiddleware.__new__(PrometheusMiddleware)
        assert mw._normalize_path("/api/v1/config/certificates/upload") == "/api/v1/config/certificates/{action}"

    def test_normalize_path_preserves_trailing_slash(self):
        from app.middleware.metrics import PrometheusMiddleware
        mw = PrometheusMiddleware.__new__(PrometheusMiddleware)
        # Pattern check: if remaining part is empty or starts with '/', no replacement
        result = mw._normalize_path("/api/v1/auth/api-keys/")
        assert result == "/api/v1/auth/api-keys/"

    def test_setup_prometheus_metrics_adds_middleware(self):
        from fastapi import FastAPI

        from app.middleware.metrics import setup_prometheus_metrics
        app = FastAPI()
        setup_prometheus_metrics(app)
        # The /metrics route should be registered
        routes = [r.path for r in app.routes]
        assert "/metrics" in routes


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

class TestExceptionHandlers:
    """Tests for app.core.exception_handlers."""

    @pytest.mark.asyncio
    async def test_base_api_exception_handler(self):
        from app.core.exception_handlers import base_api_exception_handler
        from app.core.exceptions import ValidationError
        req = MagicMock()
        req.url.path = "/test"
        req.method = "GET"
        exc = ValidationError(detail="bad input")
        resp = await base_api_exception_handler(req, exc)
        assert resp.status_code == 400
        import json
        body = json.loads(resp.body)
        assert body["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_validation_exception_handler(self):
        from app.core.exception_handlers import validation_exception_handler
        req = MagicMock()
        req.url.path = "/test"
        req.method = "POST"
        exc = MagicMock()
        exc.errors.return_value = [{"loc": ["body", "field"], "msg": "required", "type": "missing"}]
        resp = await validation_exception_handler(req, exc)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_general_exception_handler(self):
        from app.core.exception_handlers import general_exception_handler
        req = MagicMock()
        req.url.path = "/test"
        req.method = "GET"
        exc = RuntimeError("boom")
        resp = await general_exception_handler(req, exc)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class TestCustomExceptions:
    """Tests for app.core.exceptions."""

    def test_validation_error(self):
        from app.core.exceptions import ValidationError
        e = ValidationError("bad")
        assert e.status_code == 400
        assert e.error_code == "VALIDATION_ERROR"

    def test_configuration_error(self):
        from app.core.exceptions import ConfigurationError
        e = ConfigurationError("broken config")
        assert e.status_code == 500
        assert e.error_code == "CONFIGURATION_ERROR"

    def test_certificate_error(self):
        from app.core.exceptions import CertificateError
        e = CertificateError("bad cert")
        assert e.status_code == 400
        assert e.error_code == "CERTIFICATE_ERROR"

    def test_database_error(self):
        from app.core.exceptions import DatabaseError
        e = DatabaseError("db fail")
        assert e.status_code == 500
        assert e.error_code == "DATABASE_ERROR"

    def test_base_api_exception_custom_code(self):
        from app.core.exceptions import BaseAPIException
        e = BaseAPIException(status_code=422, detail="x", error_code="CUSTOM")
        assert e.error_code == "CUSTOM"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

class TestCoreDependencies:
    """Tests for app.core.dependencies."""

    def test_get_current_role_from_state(self):
        from app.core.dependencies import get_current_role
        from app.core.security import Role
        req = MagicMock()
        req.state.auth_role = Role.ADMIN
        assert get_current_role(req) == Role.ADMIN

    def test_get_current_role_missing(self):
        from app.core.dependencies import get_current_role
        req = MagicMock(spec=[])
        req.state = MagicMock(spec=[])
        result = get_current_role(req)
        assert result is None
