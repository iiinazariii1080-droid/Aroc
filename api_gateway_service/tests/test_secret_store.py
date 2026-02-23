"""Tests for RobotSecretStore — set, get, masking."""

import pytest

from app.core.secret_store import RobotSecretStore


@pytest.fixture
def store():
    s = RobotSecretStore.__new__(RobotSecretStore)
    import asyncio
    s._lock = asyncio.Lock()
    s._api_key = None
    return s


@pytest.mark.asyncio
async def test_get_returns_none_initially(store):
    assert await store.get_api_key() is None


@pytest.mark.asyncio
async def test_set_then_get(store):
    await store.set_api_key("my-secret-key")
    assert await store.get_api_key() == "my-secret-key"


@pytest.mark.asyncio
async def test_masked_none(store):
    assert await store.masked_api_key() is None


@pytest.mark.asyncio
async def test_masked_short_key(store):
    await store.set_api_key("ab")
    assert await store.masked_api_key() == "**"


@pytest.mark.asyncio
async def test_masked_exact_4_chars(store):
    await store.set_api_key("abcd")
    assert await store.masked_api_key() == "****"


@pytest.mark.asyncio
async def test_masked_long_key(store):
    await store.set_api_key("super-secret-key-12345")
    masked = await store.masked_api_key()
    assert masked.startswith("su")
    assert masked.endswith("45")
    assert "***" in masked
    assert "super-secret-key-12345" != masked


@pytest.mark.asyncio
async def test_set_none_clears(store):
    await store.set_api_key("something")
    await store.set_api_key(None)
    assert await store.get_api_key() is None
    assert await store.masked_api_key() is None


def test_init_reads_api_key_from_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "robot_api_key.txt"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.delenv("ROBOT_API_KEY", raising=False)

    store = RobotSecretStore(file_path=str(secret_file))
    assert store._api_key == "file-secret"


def test_init_env_overrides_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "robot_api_key.txt"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("ROBOT_API_KEY", "env-secret")

    store = RobotSecretStore(file_path=str(secret_file))
    assert store._api_key == "env-secret"
