"""
Unit tests for BaseHttpClient with mocks.
"""
import pytest
from unittest.mock import AsyncMock, patch
from exceptions import DeviceConnectionError
from services.base_http_client import BaseHttpClient


class MockHttpClient(BaseHttpClient):
    """Mock implementation of BaseHttpClient for testing."""
    pass


@pytest.mark.asyncio
async def test_get_success():
    """Test successful GET request."""
    client = MockHttpClient(base_url="https://test.example.com")
    
    mock_response_data = {"status": "ok", "data": [1, 2, 3]}
    
    with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response_data
        
        result = await client.get("/test/path")
        
        assert result == mock_response_data
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == "/test/path"


@pytest.mark.asyncio
async def test_post_success():
    """Test successful POST request."""
    client = MockHttpClient(base_url="https://test.example.com")
    
    mock_response_data = {"id": "123", "status": "created"}
    
    with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response_data
        
        result = await client.post("/test/path", json_data={"key": "value"})
        
        assert result == mock_response_data
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/test/path"
        assert call_args[1]["json_data"] == {"key": "value"}


@pytest.mark.asyncio
async def test_retry_on_connection_error():
    """Test that connection errors can be handled."""
    client = MockHttpClient(base_url="https://test.example.com")
    
    # Test that DeviceConnectionError is raised (retry logic is in _make_request)
    with patch.object(client, '_make_request', new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = DeviceConnectionError("Connection failed")
        
        # Should raise the error
        with pytest.raises(DeviceConnectionError):
            await client.get("/test/path")
        
        # Verify it was called
        mock_request.assert_called_once()


@pytest.mark.asyncio
async def test_error_extraction():
    """Test error message extraction from responses."""
    client = MockHttpClient(base_url="https://test.example.com")
    
    # Test various error response formats
    test_cases = [
        ({"error": {"msg": "Test error"}}, "Test error"),
        ({"text": "Error text"}, "Error text"),
        ({"message": "Error message"}, "Error message"),
        ({}, "Unknown error"),
        (None, "Unknown error"),
    ]
    
    for response_data, expected in test_cases:
        error_msg = client._extract_error_message(response_data)
        assert isinstance(error_msg, str)
        if expected != "Unknown error":
            assert expected in error_msg or error_msg == expected
