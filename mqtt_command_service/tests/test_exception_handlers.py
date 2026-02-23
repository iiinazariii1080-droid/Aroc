"""Tests for exception handlers (app/core/exception_handlers.py)."""
import json
from unittest.mock import MagicMock

import pytest

from app.core.exception_handlers import (
    base_api_exception_handler,
    general_exception_handler,
    validation_exception_handler,
)
from app.core.exceptions import BaseAPIException


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.url.path = "/test"
    req.method = "GET"
    return req


@pytest.mark.asyncio
async def test_base_api_exception_handler(mock_request):
    exc = BaseAPIException(status_code=404, detail="Not found", error_code="NOT_FOUND")
    resp = await base_api_exception_handler(mock_request, exc)
    assert resp.status_code == 404
    body = json.loads(resp.body)
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "Not found"


@pytest.mark.asyncio
async def test_general_exception_handler_hides_details(mock_request):
    exc = RuntimeError("secret internal error")
    resp = await general_exception_handler(mock_request, exc)
    assert resp.status_code == 500
    body = json.loads(resp.body)
    assert "secret" not in body["error"]["message"]
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"


@pytest.mark.asyncio
async def test_validation_exception_handler(mock_request):
    from fastapi.exceptions import RequestValidationError

    exc = RequestValidationError(
        errors=[
            {"loc": ("body", "name"), "msg": "field required", "type": "missing"}
        ]
    )
    resp = await validation_exception_handler(mock_request, exc)
    assert resp.status_code == 422
    body = json.loads(resp.body)
    assert "detail" in body
    assert len(body["detail"]) == 1
    assert body["detail"][0]["field"] == "body.name"
