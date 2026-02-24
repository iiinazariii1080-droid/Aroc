"""Tests for app/services/relay_proxy.py and janus_proxy.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import relay_proxy, janus_proxy


class TestRelayProxy:
    @pytest.mark.asyncio
    async def test_start_stop_client(self):
        relay_proxy._client = None
        await relay_proxy.start_client()
        assert relay_proxy._client is not None
        await relay_proxy.stop_client()
        assert relay_proxy._client is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        relay_proxy._client = None
        await relay_proxy.start_client()
        first = relay_proxy._client
        await relay_proxy.start_client()
        assert relay_proxy._client is first
        await relay_proxy.stop_client()

    @pytest.mark.asyncio
    async def test_relay_get_success(self):
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"time": 123}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        relay_proxy._client = mock_client
        try:
            result = await relay_proxy.relay_get("time")
            assert result["time"] == 123
        finally:
            relay_proxy._client = None

    @pytest.mark.asyncio
    async def test_relay_get_creates_client_if_needed(self):
        relay_proxy._client = None
        with patch("app.services.relay_proxy.start_client", new_callable=AsyncMock) as mock_start:
            # After start_client, _client should exist
            async def set_client():
                mock = AsyncMock()
                resp = MagicMock()
                resp.json.return_value = {"ok": True}
                resp.raise_for_status = MagicMock()
                mock.get.return_value = resp
                relay_proxy._client = mock

            mock_start.side_effect = set_client
            result = await relay_proxy.relay_get("test")
            assert result == {"ok": True}
        relay_proxy._client = None


class TestJanusProxy:
    @pytest.mark.asyncio
    async def test_start_stop_client(self):
        janus_proxy._janus_client = None
        await janus_proxy.start_client()
        assert janus_proxy._janus_client is not None
        await janus_proxy.stop_client()
        assert janus_proxy._janus_client is None

    @pytest.mark.asyncio
    async def test_stop_when_none(self):
        janus_proxy._janus_client = None
        await janus_proxy.stop_client()  # should not raise
