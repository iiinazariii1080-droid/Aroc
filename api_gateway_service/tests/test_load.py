"""
Integration / load tests for the API Gateway.

Validates under concurrent load:
  - No semaphore leaks (every acquire has a matching release)
  - No connection leaks (resp.aclose always called)
  - Timeouts trigger correctly and release resources
  - Circuit breaker opens/closes under failure patterns
  - Concurrent requests don't deadlock or race
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.core.circuit_breaker as cb_mod
from app.core.circuit_breaker import get_breaker, CircuitBreaker
from app.core.config import DEFAULT_SERVICE_CONCURRENCY


# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def _make_mock_response(
    status_code: int = 200,
    body: bytes = b'{"ok":true}',
    headers: dict | None = None,
    *,
    delay: float = 0.0,
    chunk_size: int | None = None,
):
    """
    Create a mock httpx.Response for streaming proxy tests.

    Args:
        delay: seconds to sleep between chunks (simulates slow upstream).
        chunk_size: if set, split body into chunks of this size.
    """
    resp = AsyncMock()
    resp.status_code = status_code
    resp.headers = httpx.Headers(headers or {"content-type": "application/json"})

    if chunk_size and len(body) > chunk_size:
        chunks = [body[i:i + chunk_size] for i in range(0, len(body), chunk_size)]
    else:
        chunks = [body]

    async def _aiter_bytes(chunk_size=65536):
        for c in chunks:
            if delay > 0:
                await asyncio.sleep(delay)
            yield c

    resp.aiter_bytes = _aiter_bytes
    resp.aclose = AsyncMock()
    return resp


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Clear the global circuit breaker registry between tests."""
    cb_mod._breakers.clear()
    yield
    cb_mod._breakers.clear()


# ────────────────────────────────────────────────────────────────
# 1. Semaphore leak tests
# ────────────────────────────────────────────────────────────────

class TestSemaphoreLeaks:
    """Verify semaphore is always released — success, error, timeout."""

    @pytest.mark.asyncio
    async def test_semaphore_released_after_successful_request(self, client, app):
        """N parallel requests → all complete → semaphore fully released."""
        mock_resp = _make_mock_response(200, b'{"data":"ok"}')
        n = 15

        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            tasks = [client.get("/api/v1/xarm/status") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in results)

        # Semaphore should be fully released (value == initial value)
        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "xarm")
        # _value reflects available slots; after all released it should be at max
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY, (
            f"Semaphore leak: {DEFAULT_SERVICE_CONCURRENCY - sem._value} unreleased slots"
        )

    @pytest.mark.asyncio
    async def test_semaphore_released_on_connect_error(self, client, app):
        """Connect failure → semaphore released, no leak."""
        n = 10

        with patch(
            "app.routers.proxy_http.stream_request",
            side_effect=httpx.ConnectError("refused"),
        ):
            tasks = [client.get("/api/v1/xarm/status") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        assert all(r.status_code == 502 for r in results)

        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "xarm")
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY, "Semaphore leaked on ConnectError"

    @pytest.mark.asyncio
    async def test_semaphore_released_on_timeout(self, client, app):
        """Read timeout → semaphore released."""
        n = 3  # keep at threshold to avoid CB opening

        with patch(
            "app.routers.proxy_http.stream_request",
            side_effect=httpx.ReadTimeout("timed out"),
        ):
            tasks = [client.get("/api/v1/robot/state") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        # All should be 504 (timeout) — CB opens at exactly 3 but these are concurrent
        assert all(r.status_code in (502, 504) for r in results)

        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "robot")
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY, "Semaphore leaked on ReadTimeout"

    @pytest.mark.asyncio
    async def test_semaphore_released_after_body_streaming(self, client, app):
        """
        Body is streamed lazily. Semaphore must stay held during streaming
        and release AFTER the body is fully consumed.
        """
        # Large body split into many small chunks
        body = b"x" * 4096
        mock_resp = _make_mock_response(200, body, chunk_size=512)

        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            resp = await client.get("/api/v1/xarm/data")
            # Body is consumed by the test client automatically
            assert resp.status_code == 200
            assert len(resp.content) == 4096

        # After full consumption → semaphore released
        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "xarm")
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY, "Semaphore leaked after body streaming"

        # resp.aclose must have been called
        mock_resp.aclose.assert_called()

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure_no_leak(self, client, app):
        """Mix of successes and failures under load — no semaphore leak."""
        call_count = 0

        async def _alternating(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 3 == 0:
                raise httpx.ConnectError("refused")
            return _make_mock_response(200, b'{"ok":true}')

        n = 20
        with patch("app.routers.proxy_http.stream_request", side_effect=_alternating):
            tasks = [client.get("/api/v1/igus/status") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        success = sum(1 for r in results if r.status_code == 200)
        errors = sum(1 for r in results if r.status_code in (502, 504))
        assert success + errors == n

        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "igus")
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY, (
            f"Semaphore leak in mixed scenario: {DEFAULT_SERVICE_CONCURRENCY - sem._value} stuck"
        )


# ────────────────────────────────────────────────────────────────
# 2. Connection leak tests (resp.aclose)
# ────────────────────────────────────────────────────────────────

class TestConnectionLeaks:
    """Every upstream response must be closed, even on errors."""

    @pytest.mark.asyncio
    async def test_aclose_called_on_success(self, client):
        """Successful streaming → aclose called in _guarded_body finally."""
        mock_resp = _make_mock_response(200, b'{"ok":true}')

        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            resp = await client.get("/api/v1/xarm/status")
            assert resp.status_code == 200
            _ = resp.content  # consume body

        mock_resp.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_aclose_called_on_every_concurrent_request(self, client):
        """N concurrent requests → N aclose calls."""
        responses = []
        n = 10

        async def _make_unique(*args, **kwargs):
            r = _make_mock_response(200, b'{"n":1}')
            responses.append(r)
            return r

        with patch("app.routers.proxy_http.stream_request", side_effect=_make_unique):
            tasks = [client.get("/api/v1/robot/state") for _ in range(n)]
            await asyncio.gather(*tasks)

        assert len(responses) == n
        for i, r in enumerate(responses):
            r.aclose.assert_called_once(), f"Response {i} was never closed"


# ────────────────────────────────────────────────────────────────
# 3. Circuit breaker under load
# ────────────────────────────────────────────────────────────────

class TestCircuitBreakerLoad:
    """Circuit breaker opens under failures, rejects fast, recovers."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold_failures(self, client):
        """3 consecutive failures → circuit opens → next requests rejected without network."""
        call_count = 0

        async def _always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("down")

        with patch("app.routers.proxy_http.stream_request", side_effect=_always_fail):
            # CB_FAILURE_THRESHOLD defaults to 3
            for i in range(5):
                resp = await client.get("/api/v1/xarm/status")
                assert resp.status_code == 502

        # Only 3 actual network calls should have been made;
        # requests 4 and 5 should be rejected by circuit breaker
        assert call_count == 3, f"Expected 3 actual calls, got {call_count}"

        cb = get_breaker("xarm")
        assert cb.describe()["state"] == "open"

    @pytest.mark.asyncio
    async def test_circuit_open_returns_fast(self, client):
        """When circuit is open, requests return instantly (no network wait)."""
        # Force circuit open
        cb = get_breaker("symovo")
        for _ in range(3):
            cb.record_failure()

        assert cb.describe()["state"] == "open"

        import time
        start = time.monotonic()

        with patch("app.routers.proxy_http.stream_request") as sr:
            results = await asyncio.gather(
                *[client.get("/api/v1/symovo/status") for _ in range(20)]
            )
            sr.assert_not_called()

        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"Circuit-open rejections took {elapsed:.2f}s — should be instant"
        assert all(r.status_code == 502 for r in results)

    @pytest.mark.asyncio
    async def test_circuit_recovers_after_success(self, client):
        """OPEN → HALF_OPEN → probe succeeds → CLOSED."""
        cb = get_breaker("robot")
        for _ in range(3):
            cb.record_failure()
        assert cb.describe()["state"] == "open"

        # Simulate recovery timeout passing
        cb._last_failure_time = 0  # force expiry

        mock_resp = _make_mock_response(200, b'{"ok":true}')
        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            resp = await client.get("/api/v1/robot/status")

        assert resp.status_code == 200
        assert cb.describe()["state"] == "closed"

    @pytest.mark.asyncio
    async def test_independent_circuits_per_service(self, client):
        """Failure in one service doesn't affect another."""
        # Kill xarm circuit
        cb_xarm = get_breaker("xarm")
        for _ in range(3):
            cb_xarm.record_failure()

        mock_resp = _make_mock_response(200, b'{"ok":true}')
        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            # xarm should be rejected
            r1 = await client.get("/api/v1/xarm/status")
            # igus should work fine
            r2 = await client.get("/api/v1/igus/status")

        assert r1.status_code == 502
        assert r2.status_code == 200


# ────────────────────────────────────────────────────────────────
# 4. Timeout behavior
# ────────────────────────────────────────────────────────────────

class TestTimeouts:
    """Verify timeout paths release resources correctly."""

    @pytest.mark.asyncio
    async def test_body_timeout_releases_semaphore(self, client, app):
        """
        Upstream sends body very slowly → PROXY_BODY_TIMEOUT_S triggers →
        semaphore released, response closed.
        """
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.headers = httpx.Headers({"content-type": "text/plain"})
        mock_resp.aclose = AsyncMock()

        async def _very_slow_body(chunk_size=65536):
            yield b"start"
            # Sleep longer than body timeout
            await asyncio.sleep(999)
            yield b"never_reached"

        mock_resp.aiter_bytes = _very_slow_body

        with (
            patch("app.routers.proxy_http.stream_request", return_value=mock_resp),
            patch("app.routers.proxy_http.PROXY_BODY_TIMEOUT_S", 0.3),
        ):
            # The body timeout fires mid-stream which propagates as
            # an ExceptionGroup through Starlette's task group.
            # This is expected — in production the client connection
            # is simply dropped.
            try:
                resp = await client.get("/api/v1/xarm/data")
            except Exception:
                pass  # Expected: TimeoutError / ExceptionGroup

        # Let finally blocks in _guarded_body complete
        await asyncio.sleep(0.5)

        # Key assertion: semaphore is released even after body timeout
        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "xarm")
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY, "Semaphore leaked on body timeout"

        # Connection must be closed
        mock_resp.aclose.assert_called()

    @pytest.mark.asyncio
    async def test_connect_timeout_releases_semaphore(self, client, app):
        """
        Upstream takes too long to respond → connect timeout →
        semaphore released.
        """
        async def _slow_connect(*args, **kwargs):
            await asyncio.sleep(999)

        with (
            patch("app.routers.proxy_http.stream_request", side_effect=_slow_connect),
            patch("app.routers.proxy_http.PROXY_CONNECT_TIMEOUT_S", 0.3),
        ):
            resp = await client.get("/api/v1/robot/data")

        assert resp.status_code == 504  # TimeoutError → 504

        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "robot")
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY, "Semaphore leaked on connect timeout"


# ────────────────────────────────────────────────────────────────
# 5. Concurrency stress
# ────────────────────────────────────────────────────────────────

class TestConcurrencyStress:
    """High concurrency scenarios — verify no deadlocks or corruption."""

    @pytest.mark.asyncio
    async def test_50_concurrent_requests(self, client, app):
        """50 parallel requests to same service — no deadlock, all complete."""
        mock_resp = _make_mock_response(200, b'{"ok":true}')
        n = 50

        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            tasks = [client.get("/api/v1/xarm/status") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in results)
        assert len(results) == n

        from app.core.http_client import get_service_semaphore
        sem = get_service_semaphore(app, "xarm")
        assert sem._value == DEFAULT_SERVICE_CONCURRENCY

    @pytest.mark.asyncio
    async def test_concurrent_requests_across_services(self, client, app):
        """Parallel requests to different services — independent semaphores."""
        mock_resp = _make_mock_response(200, b'{"ok":true}')
        services = ["xarm", "igus", "robot", "symovo"]
        n_per_service = 10

        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            tasks = []
            for svc in services:
                for _ in range(n_per_service):
                    tasks.append(client.get(f"/api/v1/{svc}/status"))
            results = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in results)
        assert len(results) == len(services) * n_per_service

        from app.core.http_client import get_service_semaphore
        for svc in services:
            sem = get_service_semaphore(app, svc)
            assert sem._value == DEFAULT_SERVICE_CONCURRENCY, f"Leak in {svc}"

    @pytest.mark.asyncio
    async def test_semaphore_contention_under_limit(self, client, app):
        """
        Set semaphore to 2, fire 10 requests with slow upstream →
        all complete sequentially without deadlock.
        """
        from app.core.http_client import get_service_semaphore

        # Restrict to 2 concurrent slots for igus
        app.state.service_semaphores["igus"] = asyncio.Semaphore(2)

        completion_order = []

        async def _slow_request(*args, **kwargs):
            completion_order.append("start")
            await asyncio.sleep(0.05)
            return _make_mock_response(200, b'{"ok":true}')

        n = 6
        with patch("app.routers.proxy_http.stream_request", side_effect=_slow_request):
            tasks = [client.get("/api/v1/igus/status") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in results)
        assert len(results) == n

        sem = get_service_semaphore(app, "igus")
        assert sem._value == 2, "Semaphore not fully released"


# ────────────────────────────────────────────────────────────────
# 6. Response correctness under load
# ────────────────────────────────────────────────────────────────

class TestResponseCorrectnessUnderLoad:
    """Responses aren't mixed up or corrupted under concurrent load."""

    @pytest.mark.asyncio
    async def test_response_bodies_not_mixed(self, client):
        """Each request gets its own response body, not another request's."""
        counter = 0

        async def _unique_response(*args, **kwargs):
            nonlocal counter
            counter += 1
            body = f'{{"id":{counter}}}'.encode()
            return _make_mock_response(200, body)

        n = 20
        with patch("app.routers.proxy_http.stream_request", side_effect=_unique_response):
            tasks = [client.get("/api/v1/robot/data") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        ids = [r.json()["id"] for r in results]
        assert len(set(ids)) == n, f"Got {len(set(ids))} unique IDs for {n} requests — responses mixed!"

    @pytest.mark.asyncio
    async def test_request_id_unique_per_request(self, client):
        """Each concurrent request gets a unique X-Request-ID."""
        mock_resp = _make_mock_response(200, b'{"ok":true}')
        n = 30

        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            tasks = [client.get("/api/v1/xarm/status") for _ in range(n)]
            results = await asyncio.gather(*tasks)

        request_ids = [r.headers["x-request-id"] for r in results]
        assert len(set(request_ids)) == n, (
            f"Only {len(set(request_ids))} unique request IDs for {n} requests"
        )


# ────────────────────────────────────────────────────────────────
# 7. Health endpoints under load
# ────────────────────────────────────────────────────────────────

class TestHealthUnderLoad:
    """Health endpoints remain responsive during proxy load."""

    @pytest.mark.asyncio
    async def test_health_responds_during_proxy_load(self, client):
        """
        Fire proxy requests + health checks concurrently.
        Health must respond regardless of proxy state.
        """
        mock_resp = _make_mock_response(200, b'{"ok":true}')

        async def _proxy_request():
            with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
                return await client.get("/api/v1/xarm/status")

        async def _health_request():
            return await client.get("/livez")

        tasks = []
        for _ in range(10):
            tasks.append(_proxy_request())
            tasks.append(_health_request())

        results = await asyncio.gather(*tasks)
        health_results = results[1::2]  # every other is health

        assert all(r.status_code == 200 for r in health_results), (
            "Health endpoint failed during proxy load"
        )
