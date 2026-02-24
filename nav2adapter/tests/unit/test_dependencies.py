"""Tests for app/dependencies.py — DI provider."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from app.dependencies import get_symovo_client


@pytest.mark.asyncio
async def test_shared_client_from_state():
    """When app.state has a shared SymovoAgvClient, yield it directly."""
    from services.symovo_service import SymovoAgvClient
    mock_client = MagicMock(spec=SymovoAgvClient)
    mock_request = MagicMock()
    mock_request.app.state = SimpleNamespace(symovo_client=mock_client)

    gen = get_symovo_client(mock_request)
    client = await gen.__anext__()
    assert client is mock_client

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_per_request_fallback():
    """When no shared client, create one and close it after."""
    mock_request = MagicMock()
    # No symovo_client attribute at all → getattr returns None → isinstance fails → fallback
    mock_request.app.state = SimpleNamespace()

    mock_instance = MagicMock()
    mock_instance.close = AsyncMock()

    original_class = None
    import app.dependencies as deps_mod
    from services.symovo_service import SymovoAgvClient as RealClass
    original_init = RealClass.__init__

    # Temporarily make SymovoAgvClient() return our mock
    with patch.object(deps_mod, "SymovoAgvClient", return_value=mock_instance) as mock_cls:
        # Keep isinstance working by not patching the class itself in dependencies
        pass

    # Simpler approach: just test the else branch by having no attribute
    mock_instance2 = MagicMock(spec=["close"])
    mock_instance2.close = AsyncMock()
    with patch("services.symovo_service.SymovoAgvClient.__init__", return_value=None):
        with patch("services.symovo_service.SymovoAgvClient.close", new_callable=AsyncMock):
            gen = get_symovo_client(mock_request)
            client = await gen.__anext__()
            # It's a real SymovoAgvClient (with mocked init)
            assert client is not None

            # Exhaust generator
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()
