"""Integration tests for hub CRUD endpoints."""

from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.asyncio
async def test_hub_config_roundtrip(client):
    """PUT then GET hub config returns saved data."""
    payload = {"base_url": "http://hub.example.com:8000"}
    resp = await client.put("/api/v1/hub/config", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"

    resp = await client.get("/api/v1/hub/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "hub.example.com" in data["config"]["base_url"]


@pytest.mark.asyncio
async def test_hub_robot_identity_roundtrip(client):
    """PUT then GET robot identity."""
    payload = {"robot_id": "test-robot-42", "display_name": "Test Bot"}
    resp = await client.put("/api/v1/hub/robot", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"

    resp = await client.get("/api/v1/hub/robot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["robot"]["robot_id"] == "test-robot-42"
    assert data["robot"]["display_name"] == "Test Bot"


@pytest.mark.asyncio
async def test_hub_credentials_roundtrip(client):
    """PUT credentials, then GET — API key is masked."""
    payload = {"robot_id": "cred-robot", "api_key": "super-secret-key-12345"}
    resp = await client.put("/api/v1/hub/credentials", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"

    resp = await client.get("/api/v1/hub/credentials")
    assert resp.status_code == 200
    creds = resp.json()["credentials"]
    assert creds["robot_id"] == "cred-robot"
    # API key must be masked
    assert "api_key_preview" in creds
    assert "***" in creds["api_key_preview"]
    assert "super-secret-key-12345" != creds.get("api_key_preview")


@pytest.mark.asyncio
async def test_hub_config_validation(client):
    """PUT with invalid base_url is rejected."""
    resp = await client.put("/api/v1/hub/config", json={"base_url": "not-a-url"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_hub_robot_identity_validation(client):
    """Robot ID must match pattern ^[A-Za-z0-9._-]+$."""
    resp = await client.put("/api/v1/hub/robot", json={"robot_id": "invalid robot id!"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_auth_status(client):
    """GET auth status returns disabled or status info."""
    resp = await client.get("/api/v1/hub/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_connection_test_dry_run(client):
    """Connection test with dry_run=true skips network call."""
    # Ensure hub config exists first
    await client.put("/api/v1/hub/config", json={"base_url": "http://hub.example.com:8000"})

    resp = await client.post("/api/v1/hub/connection-test", json={"dry_run": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "skipped"
    assert data["reason"] == "dry_run=true"
