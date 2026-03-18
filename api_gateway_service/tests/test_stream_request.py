"""Unit tests for app.core.http_proxy_utils.stream_request — retry logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.http_proxy_utils import stream_request


def _mock_response(status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.aclose = AsyncMock()
    return resp


def _mock_client(responses: list):
    """Build a mock AsyncClient that returns responses in sequence."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.build_request = MagicMock(return_value=MagicMock(spec=httpx.Request))
    client.send = AsyncMock(side_effect=responses)
    return client


class TestStreamRequestRetry:
    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        resp = _mock_response(200)
        client = _mock_client([resp])
        result = await stream_request(
            client, "GET", "http://test/ok",
            headers={}, params={}, content=b"",
        )
        assert result.status_code == 200
        assert client.send.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_502_for_safe_method(self):
        bad = _mock_response(502)
        good = _mock_response(200)
        client = _mock_client([bad, good])
        with patch("app.core.http_proxy_utils.asyncio.sleep", new_callable=AsyncMock):
            result = await stream_request(
                client, "GET", "http://test/retry",
                headers={}, params={}, content=b"",
                retry_attempts=1,
            )
        assert result.status_code == 200
        assert client.send.call_count == 2
        bad.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_on_503_504(self):
        for status in (503, 504):
            bad = _mock_response(status)
            good = _mock_response(200)
            client = _mock_client([bad, good])
            with patch("app.core.http_proxy_utils.asyncio.sleep", new_callable=AsyncMock):
                result = await stream_request(
                    client, "GET", "http://test/retry",
                    headers={}, params={}, content=b"",
                    retry_attempts=1,
                )
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_no_retry_for_post(self):
        bad = _mock_response(502)
        client = _mock_client([bad])
        result = await stream_request(
            client, "POST", "http://test/no-retry",
            headers={}, params={}, content=b"body",
            retry_attempts=3,
        )
        assert result.status_code == 502
        assert client.send.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_for_put(self):
        bad = _mock_response(503)
        client = _mock_client([bad])
        result = await stream_request(
            client, "PUT", "http://test/no-retry",
            headers={}, params={}, content=b"data",
            retry_attempts=3,
        )
        assert result.status_code == 503

    @pytest.mark.asyncio
    async def test_retries_on_connect_error(self):
        good = _mock_response(200)
        client = _mock_client([httpx.ConnectError("refused"), good])
        with patch("app.core.http_proxy_utils.asyncio.sleep", new_callable=AsyncMock):
            result = await stream_request(
                client, "GET", "http://test/connfail",
                headers={}, params={}, content=b"",
                retry_attempts=1,
            )
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        client = _mock_client([
            httpx.ConnectTimeout("t1"),
            httpx.ConnectTimeout("t2"),
        ])
        with patch("app.core.http_proxy_utils.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.ConnectTimeout):
                await stream_request(
                    client, "GET", "http://test/fail",
                    headers={}, params={}, content=b"",
                    retry_attempts=1,
                )

    @pytest.mark.asyncio
    async def test_returns_bad_status_after_exhausting_retries(self):
        bad1 = _mock_response(502)
        bad2 = _mock_response(503)
        client = _mock_client([bad1, bad2])
        with patch("app.core.http_proxy_utils.asyncio.sleep", new_callable=AsyncMock):
            result = await stream_request(
                client, "HEAD", "http://test/exhaust",
                headers={}, params={}, content=b"",
                retry_attempts=1,
            )
        assert result.status_code == 503

    @pytest.mark.asyncio
    async def test_head_and_options_are_retryable(self):
        for method in ("HEAD", "OPTIONS"):
            bad = _mock_response(502)
            good = _mock_response(200)
            client = _mock_client([bad, good])
            with patch("app.core.http_proxy_utils.asyncio.sleep", new_callable=AsyncMock):
                result = await stream_request(
                    client, method, "http://test/safe",
                    headers={}, params={}, content=b"",
                    retry_attempts=1,
                )
            assert result.status_code == 200
