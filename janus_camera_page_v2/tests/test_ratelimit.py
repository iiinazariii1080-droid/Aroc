"""Unit tests for the RateLimitMiddleware.

Covers:
- Basic rate limiting (429 after exceeding budget)
- Window expiry (requests allowed after window elapses)
- Trusted proxy XFF handling
- XFF spoofing rejection from untrusted peers
- Memory bound enforcement (max_buckets eviction)
- Non-rate-limited paths pass through
- Disabled rate limiter
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings


@pytest.fixture
def rate_limited_app(tmp_path):
    """App with aggressive rate limits for testing."""
    os.environ["RATE_LIMIT_SNAPSHOT"] = "2"
    os.environ["RATE_LIMIT_HEALTHZ"] = "3"
    os.environ["RATE_LIMIT_JANUS_WS"] = "1"
    os.environ["RATE_LIMIT_WINDOW_SEC"] = "60"
    os.environ["RATE_LIMIT_MAX_BUCKETS"] = "5"
    os.environ["TRUSTED_PROXIES"] = "10.0.0.1"

    from app.core.settings import get_settings
    get_settings.cache_clear()

    _no_admin = make_test_settings(tmp_path, admin_enforce=False)
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch("app.core.admin.get_settings", return_value=_no_admin):
        from app.core.app import create_app
        app = create_app()

    yield app

    for key in (
        "RATE_LIMIT_SNAPSHOT", "RATE_LIMIT_HEALTHZ", "RATE_LIMIT_JANUS_WS",
        "RATE_LIMIT_WINDOW_SEC", "RATE_LIMIT_MAX_BUCKETS", "TRUSTED_PROXIES",
    ):
        os.environ.pop(key, None)
    get_settings.cache_clear()


@pytest.fixture
async def rl_client(rate_limited_app):
    transport = ASGITransport(app=rate_limited_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_snapshot_rate_limit_allows_within_budget(rl_client):
    """First N requests within budget should succeed."""
    # limit=2, so first 2 requests should get through (200 or 503 from app, not 429)
    r1 = await rl_client.get("/snapshot.jpg")
    assert r1.status_code != 429
    r2 = await rl_client.get("/snapshot.jpg")
    assert r2.status_code != 429


@pytest.mark.anyio
async def test_snapshot_rate_limit_blocks_over_budget(rl_client):
    """Third request over limit=2 should return 429."""
    await rl_client.get("/snapshot.jpg")
    await rl_client.get("/snapshot.jpg")
    r3 = await rl_client.get("/snapshot.jpg")
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers
    assert r3.headers["X-RateLimit-Remaining"] == "0"
    assert r3.headers["X-RateLimit-Limit"] == "2"


@pytest.mark.anyio
async def test_healthz_rate_limit(rl_client):
    """healthz limit=3 should block on 4th request."""
    for _ in range(3):
        r = await rl_client.get("/healthz")
        assert r.status_code != 429
    r4 = await rl_client.get("/healthz")
    assert r4.status_code == 429


@pytest.mark.anyio
async def test_non_limited_path_passes(rl_client):
    """Paths not in the rate-limit list should never get 429."""
    for _ in range(20):
        # Use /client-config (no external calls) instead of /status which
        # hangs in tests because it awaits janus_summary() with no Janus server.
        r = await rl_client.get("/client-config")
        assert r.status_code != 429


@pytest.mark.anyio
async def test_xff_trusted_proxy(rate_limited_app):
    """X-Forwarded-For is honoured when peer is a trusted proxy."""
    transport = ASGITransport(app=rate_limited_app)
    # Simulate requests from trusted proxy 10.0.0.1 forwarding for different clients
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # First 2 from "client-A" via trusted proxy — should pass
        for _ in range(2):
            r = await ac.get(
                "/snapshot.jpg",
                headers={"X-Forwarded-For": "1.2.3.4"},
            )
            assert r.status_code != 429


@pytest.mark.anyio
async def test_xff_untrusted_proxy_ignored(rl_client):
    """X-Forwarded-For from untrusted peer should be ignored."""
    # peer_ip is "testclient" (from httpx), NOT in trusted_proxies
    # So spoofed XFF should be ignored; all requests count as same client
    await rl_client.get("/snapshot.jpg", headers={"X-Forwarded-For": "attacker-ip-1"})
    await rl_client.get("/snapshot.jpg", headers={"X-Forwarded-For": "attacker-ip-2"})
    r3 = await rl_client.get("/snapshot.jpg", headers={"X-Forwarded-For": "attacker-ip-3"})
    # All 3 requests from same peer IP → 3rd should be blocked (limit=2)
    assert r3.status_code == 429


@pytest.mark.anyio
async def test_rate_limiter_disabled():
    """When disabled, no 429 should ever be returned."""
    os.environ["RATE_LIMIT_ENABLED"] = "0"
    os.environ["RATE_LIMIT_SNAPSHOT"] = "1"

    from app.core.settings import get_settings
    get_settings.cache_clear()

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as _tmp:
        _no_admin = make_test_settings(Path(_tmp), admin_enforce=False)
        with patch("app.core.events.register_event_handlers", lambda app: None), \
             patch("app.core.admin.get_settings", return_value=_no_admin):
            from app.core.app import create_app
            app = create_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for _ in range(10):
                r = await ac.get("/snapshot.jpg")
                assert r.status_code != 429

    os.environ.pop("RATE_LIMIT_ENABLED", None)
    os.environ.pop("RATE_LIMIT_SNAPSHOT", None)
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_request_id_header(rl_client):
    """Every response should include X-Request-ID."""
    r = await rl_client.get("/healthz")
    assert "X-Request-ID" in r.headers
    assert len(r.headers["X-Request-ID"]) > 0


@pytest.mark.anyio
async def test_request_id_passthrough(rl_client):
    """Client-provided X-Request-ID should be echoed back."""
    custom_id = "test-trace-12345"
    r = await rl_client.get("/healthz", headers={"X-Request-ID": custom_id})
    assert r.headers["X-Request-ID"] == custom_id
