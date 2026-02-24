"""Tests for app/api/v1/endpoints/auth.py — API key management."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _bypass(bypass_auth):
    """All tests in this module run with auth bypassed."""


@pytest.fixture()
def api():
    with TestClient(app) as c:
        yield c


# ── create_api_key_endpoint ────────────────────────────────────────────


class TestCreateApiKey:
    @patch("app.api.v1.endpoints.auth.create_api_key")
    def test_success(self, mock_create, api):
        mock_create.return_value = ("key-abc123", "id-001")
        resp = api.post(
            "/api/v1/auth/api-keys",
            data={"role": "read", "description": "test key"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["api_key"] == "key-abc123"
        assert body["key_id"] == "id-001"
        assert body["role"] == "read"
        assert "warning" in body
        mock_create.assert_called_once()

    @patch("app.api.v1.endpoints.auth.create_api_key")
    def test_admin_role(self, mock_create, api):
        mock_create.return_value = ("key-xyz", "id-002")
        resp = api.post(
            "/api/v1/auth/api-keys",
            data={"role": "admin"},
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"

    def test_missing_role_field(self, api):
        resp = api.post("/api/v1/auth/api-keys", data={})
        assert resp.status_code == 422

    def test_invalid_role_value(self, api):
        resp = api.post(
            "/api/v1/auth/api-keys",
            data={"role": "superadmin"},
        )
        assert resp.status_code == 422


# ── list_api_keys ──────────────────────────────────────────────────────


class TestListApiKeys:
    @patch("app.api.v1.endpoints.auth.get_storage")
    def test_empty_list(self, mock_get_storage, api):
        storage = MagicMock()
        storage.get_all.return_value = {}
        mock_get_storage.return_value = storage
        resp = api.get("/api/v1/auth/api-keys")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("app.api.v1.endpoints.auth.get_storage")
    def test_populated_list(self, mock_get_storage, api):
        storage = MagicMock()
        storage.get_all.return_value = {
            "api_key_meta:id1": "...",
            "api_key_meta:id2": "...",
            "other_key": "noise",
        }
        storage.get_json.side_effect = lambda key: {
            "api_key_meta:id1": {
                "role": "read",
                "description": "reader",
                "created_at": "2025-01-01T00:00:00Z",
            },
            "api_key_meta:id2": {
                "role": "admin",
                "description": "admin key",
                "created_at": "2025-06-01T00:00:00Z",
                "revoked": True,
                "revoked_at": "2025-07-01T00:00:00Z",
            },
        }.get(key)
        mock_get_storage.return_value = storage

        resp = api.get("/api/v1/auth/api-keys")
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 2
        # Sorted descending by created_at
        assert keys[0]["key_id"] == "id2"
        assert keys[0]["revoked"] is True
        assert keys[1]["key_id"] == "id1"
        assert keys[1]["role"] == "read"


# ── revoke_api_key_endpoint ────────────────────────────────────────────


class TestRevokeApiKey:
    @patch("app.api.v1.endpoints.auth.revoke_api_key")
    def test_success(self, mock_revoke, api):
        mock_revoke.return_value = True
        resp = api.delete("/api/v1/auth/api-keys/id-001")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["revoked"] == "id-001"

    @patch("app.api.v1.endpoints.auth.revoke_api_key")
    def test_not_found(self, mock_revoke, api):
        mock_revoke.return_value = False
        resp = api.delete("/api/v1/auth/api-keys/nonexistent")
        assert resp.status_code == 404
