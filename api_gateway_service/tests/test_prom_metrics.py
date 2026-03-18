"""Tests for Prometheus /metrics endpoint."""

from unittest.mock import patch

import pytest

from app.core.service_metrics import record_proxy_result, record_auth_result


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_contains_proxy_counter(client):
    record_proxy_result("xarm", 200)
    resp = await client.get("/metrics")
    body = resp.text
    assert "gateway_proxy_requests_total" in body


@pytest.mark.asyncio
async def test_metrics_contains_auth_counter(client):
    record_auth_result(True)
    resp = await client.get("/metrics")
    body = resp.text
    assert "gateway_auth_attempts_total" in body


@pytest.mark.asyncio
async def test_metrics_contains_duration_histogram(client):
    from app.core.service_metrics import record_proxy_duration
    record_proxy_duration("igus", 0.05)
    resp = await client.get("/metrics")
    body = resp.text
    assert "gateway_proxy_duration_seconds" in body


@pytest.mark.asyncio
async def test_metrics_contains_circuit_breaker_gauge(client):
    from app.core.circuit_breaker import get_breaker
    cb = await get_breaker("test_svc")
    await cb.record_failure()
    resp = await client.get("/metrics")
    body = resp.text
    assert "gateway_circuit_breaker_state" in body
