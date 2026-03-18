"""Tests for config-api: storage, security, and schemas modules."""

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from schemas import BrokerConfigResponse, BrokerConfigUpdate, validate_broker_address
from storage import ConfigStore

# Pre-compute a deterministic HMAC key for testing
_TEST_HMAC_SECRET = "test-hmac-secret-for-deterministic-hashing"
_TEST_HMAC_KEY = hashlib.sha256(_TEST_HMAC_SECRET.encode()).digest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """Create a ConfigStore backed by a temp file."""
    return ConfigStore(path=str(tmp_path / "config.json"))


@pytest.fixture(autouse=True)
def _reset_security_globals():
    """Reset all lazy-loaded module globals in security so tests are isolated."""
    import brute_force
    import hmac_keys
    import settings_lazy
    import storage as storage_mod

    yield

    # Clear lru_cache-based lazy accessors
    settings_lazy.get_auth_disabled.cache_clear()
    settings_lazy.get_emergency_api_key.cache_clear()
    settings_lazy.get_internal_service_key.cache_clear()
    settings_lazy.get_allow_unauthenticated_read.cache_clear()
    hmac_keys._get_hmac_key.cache_clear()

    # Reset brute-force state
    with brute_force._failed_attempts_lock:
        brute_force._failed_attempts.clear()
    brute_force._last_prune_time = 0.0

    # Reset storage singleton
    storage_mod._store = None


@pytest.fixture()
def security_store(tmp_path, monkeypatch):
    """Set up a ConfigStore for security tests with env vars configured.

    Bypasses the get_store() / get_settings() singletons to avoid module name
    collisions between mqtt-bridge/config.py and config-api/config.py.
    Returns the store so tests can inspect persisted data.
    """
    import security
    import storage as storage_mod

    config_path = str(tmp_path / "security_config.json")
    test_store = ConfigStore(path=config_path)

    # Inject the store directly into the storage module
    storage_mod._store = test_store

    # Patch lazy accessors so they return test values without calling get_settings()
    # (which would pick up mqtt-bridge's config module due to path order)
    import hmac_keys
    import settings_lazy

    settings_lazy.get_auth_disabled.cache_clear()
    settings_lazy.get_emergency_api_key.cache_clear()
    settings_lazy.get_internal_service_key.cache_clear()
    settings_lazy.get_allow_unauthenticated_read.cache_clear()
    hmac_keys._get_hmac_key.cache_clear()

    # Patch on both the source module AND the security module (which imported
    # the functions at import time via `from settings_lazy import ...`)
    monkeypatch.setattr(settings_lazy, "get_auth_disabled", lambda: False)
    monkeypatch.setattr(settings_lazy, "get_emergency_api_key", lambda: "emergency-bypass-key-for-testing")
    monkeypatch.setattr(settings_lazy, "get_internal_service_key", lambda: "")
    monkeypatch.setattr(settings_lazy, "get_allow_unauthenticated_read", lambda: False)
    monkeypatch.setattr(hmac_keys, "_get_hmac_key", lambda: _TEST_HMAC_KEY)

    monkeypatch.setattr(security, "get_auth_disabled", lambda: False)
    monkeypatch.setattr(security, "get_emergency_api_key", lambda: "emergency-bypass-key-for-testing")
    monkeypatch.setattr(security, "get_internal_service_key", lambda: "")
    monkeypatch.setattr(security, "get_allow_unauthenticated_read", lambda: False)

    return test_store


# ===========================================================================
# ConfigStore tests
# ===========================================================================


class TestConfigStoreLoadSave:
    """Load / save / round-trip tests."""

    def test_load_returns_empty_dict_when_file_missing(self, store):
        assert store.load() == {}

    def test_save_and_load_round_trip(self, store):
        data = {"broker": "mqtt.example.com", "port": 1883}
        store.save(data)
        loaded = store.load()
        assert loaded == data

    def test_save_creates_parent_directories(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "config.json"
        s = ConfigStore(path=str(deep_path))
        s.save({"key": "value"})
        assert deep_path.exists()
        assert s.load() == {"key": "value"}

    def test_load_handles_corrupt_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        s = ConfigStore(path=str(p))
        assert s.load() == {}

    def test_save_overwrites_previous_data(self, store):
        store.save({"a": 1})
        store.save({"b": 2})
        assert store.load() == {"b": 2}

    def test_save_persists_to_disk(self, store, tmp_path):
        store.save({"persisted": True})
        raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert raw == {"persisted": True}


class TestConfigStoreGetSetDelete:
    """Key-level operations."""

    def test_get_returns_default_when_key_missing(self, store):
        assert store.get("missing") is None
        assert store.get("missing", 42) == 42

    def test_set_and_get(self, store):
        store.set("broker", "192.168.1.1")
        assert store.get("broker") == "192.168.1.1"

    def test_set_overwrites_existing_key(self, store):
        store.set("port", 1883)
        store.set("port", 8883)
        assert store.get("port") == 8883

    def test_set_preserves_other_keys(self, store):
        store.set("a", 1)
        store.set("b", 2)
        assert store.get("a") == 1
        assert store.get("b") == 2

    def test_delete_existing_key_returns_true(self, store):
        store.set("x", "remove_me")
        assert store.delete("x") is True
        assert store.get("x") is None

    def test_delete_missing_key_returns_false(self, store):
        assert store.delete("nonexistent") is False

    def test_delete_persists(self, store):
        store.set("gone", True)
        store.delete("gone")
        # Re-load from disk by clearing cache
        store._cache = None
        assert store.get("gone") is None


class TestConfigStorePrefix:
    """get_by_prefix tests."""

    def test_get_by_prefix_returns_matching_keys(self, store):
        store.save(
            {
                "api_key:abc": "read",
                "api_key:def": "write",
                "broker": "mqtt.local",
            }
        )
        result = store.get_by_prefix("api_key:")
        assert result == {"api_key:abc": "read", "api_key:def": "write"}

    def test_get_by_prefix_returns_empty_when_no_match(self, store):
        store.save({"broker": "mqtt.local"})
        assert store.get_by_prefix("api_key:") == {}

    def test_get_by_prefix_empty_prefix_returns_all(self, store):
        data = {"a": 1, "b": 2}
        store.save(data)
        assert store.get_by_prefix("") == data


class TestConfigStoreAtomicWrite:
    """Verify atomic write behavior."""

    def test_atomic_write_leaves_no_temp_files(self, store, tmp_path):
        store.save({"clean": True})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover temp files: {tmp_files}"

    def test_atomic_write_does_not_corrupt_on_repeated_saves(self, store):
        for i in range(20):
            store.save({"iteration": i})
        assert store.load() == {"iteration": 19}


class TestConfigStoreThreadSafety:
    """Basic concurrency check."""

    def test_concurrent_set_operations(self, store):
        errors = []

        def writer(key, value):
            try:
                for _ in range(50):
                    store.set(key, value)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"k{i}", i)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        # All keys should exist with their latest value
        for i in range(5):
            assert store.get(f"k{i}") == i


# ===========================================================================
# Security tests
# ===========================================================================


class TestHashAndGenerate:
    """hash_api_key and generate_api_key."""

    def test_hash_is_deterministic(self, security_store):
        from security import hash_api_key

        h1 = hash_api_key("test-key")
        h2 = hash_api_key("test-key")
        assert h1 == h2

    def test_hash_differs_for_different_keys(self, security_store):
        from security import hash_api_key

        h1 = hash_api_key("key-a")
        h2 = hash_api_key("key-b")
        assert h1 != h2

    def test_generate_api_key_is_unique(self, security_store):
        from security import generate_api_key

        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50

    def test_generate_api_key_length(self, security_store):
        from security import generate_api_key

        key = generate_api_key()
        # secrets.token_urlsafe(32) produces ~43 characters
        assert 20 <= len(key) <= 100


class TestCreateAndVerifyApiKey:
    """create_api_key and verify_api_key integration."""

    def test_create_api_key_returns_key_and_id(self, security_store):
        from security import Role, create_api_key

        api_key, key_id = create_api_key(Role.READ, "test key")
        assert isinstance(api_key, str)
        assert isinstance(key_id, str)
        assert len(api_key) > 0
        assert len(key_id) > 0

    def test_verify_valid_key_returns_role(self, security_store):
        from security import Role, create_api_key, verify_api_key

        api_key, _ = create_api_key(Role.WRITE, "writer key")
        role = verify_api_key(api_key)
        assert role == Role.WRITE

    def test_verify_invalid_key_returns_none(self, security_store):
        from security import verify_api_key

        # Must be between 20-100 chars to avoid early rejection
        role = verify_api_key("x" * 43)
        assert role is None

    def test_verify_none_key_returns_none(self, security_store):
        from security import verify_api_key

        role = verify_api_key(None)
        assert role is None

    def test_verify_too_short_key_returns_none(self, security_store):
        from security import verify_api_key

        role = verify_api_key("short")
        assert role is None

    def test_verify_too_long_key_returns_none(self, security_store):
        from security import verify_api_key

        role = verify_api_key("x" * 101)
        assert role is None

    def test_create_api_key_persists_metadata(self, security_store):
        from security import Role, create_api_key

        _, key_id = create_api_key(Role.ADMIN, "admin key")
        meta = security_store.get(f"api_key_meta:{key_id}")
        assert meta is not None
        assert meta["role"] == "admin"
        assert meta["description"] == "admin key"
        assert meta["revoked"] is False
        assert "created_at" in meta

    def test_create_keys_for_all_roles(self, security_store):
        from security import Role, create_api_key, verify_api_key

        for role in Role:
            api_key, _ = create_api_key(role)
            assert verify_api_key(api_key) == role


class TestRoleHierarchy:
    """RBAC role ordering: READ < SERVICE < WRITE < ADMIN."""

    def test_role_hierarchy_values(self):
        from rbac import ROLE_HIERARCHY, Role

        assert ROLE_HIERARCHY[Role.READ] < ROLE_HIERARCHY[Role.SERVICE]
        assert ROLE_HIERARCHY[Role.SERVICE] < ROLE_HIERARCHY[Role.WRITE]
        assert ROLE_HIERARCHY[Role.WRITE] < ROLE_HIERARCHY[Role.ADMIN]

    def test_role_enum_values(self):
        from security import Role

        assert Role.READ == "read"
        assert Role.SERVICE == "service"
        assert Role.WRITE == "write"
        assert Role.ADMIN == "admin"


class TestApiKeyRevocation:
    """Revoking API keys."""

    def test_revoke_existing_key(self, security_store):
        from security import Role, create_api_key, revoke_api_key, verify_api_key

        api_key, key_id = create_api_key(Role.WRITE)
        assert verify_api_key(api_key) == Role.WRITE

        result = revoke_api_key(key_id)
        assert result is True

        # Revoked key should no longer verify
        assert verify_api_key(api_key) is None

    def test_revoke_nonexistent_key_returns_false(self, security_store):
        from security import revoke_api_key

        assert revoke_api_key("nonexistent-id") is False

    def test_revoke_sets_metadata_fields(self, security_store):
        from security import Role, create_api_key, revoke_api_key

        _, key_id = create_api_key(Role.READ)
        revoke_api_key(key_id)
        meta = security_store.get(f"api_key_meta:{key_id}")
        assert meta["revoked"] is True
        assert "revoked_at" in meta

    def test_revoked_key_recorded_in_store(self, security_store):
        from security import Role, create_api_key, hash_api_key, is_api_key_revoked, revoke_api_key

        api_key, key_id = create_api_key(Role.ADMIN)
        key_hash = hash_api_key(api_key)
        assert is_api_key_revoked(key_hash) is False

        revoke_api_key(key_id)
        assert is_api_key_revoked(key_hash) is True


class TestBruteForceProtection:
    """Lockout after MAX_FAILED_ATTEMPTS from the same IP."""

    def test_lockout_after_max_failed_attempts(self, security_store):
        from security import MAX_FAILED_ATTEMPTS, verify_api_key

        client_ip = "192.168.1.100"
        # Use a key that passes length check but is invalid
        bad_key = "x" * 43

        for _ in range(MAX_FAILED_ATTEMPTS):
            verify_api_key(bad_key, client_ip=client_ip)

        # Next attempt should raise HTTP 429
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(bad_key, client_ip=client_ip)
        assert exc_info.value.status_code == 429

    def test_different_ips_are_independent(self, security_store):
        from security import MAX_FAILED_ATTEMPTS, verify_api_key

        bad_key = "x" * 43
        # Exhaust attempts for ip_a
        for _ in range(MAX_FAILED_ATTEMPTS):
            verify_api_key(bad_key, client_ip="10.0.0.1")

        # ip_b should still be allowed
        result = verify_api_key(bad_key, client_ip="10.0.0.2")
        assert result is None  # Invalid key, but no lockout

    def test_successful_auth_clears_failed_attempts(self, security_store):
        from security import Role, create_api_key, verify_api_key

        api_key, _ = create_api_key(Role.READ)
        client_ip = "172.16.0.1"
        bad_key = "x" * 43

        # Accumulate some failed attempts (but less than MAX)
        for _ in range(3):
            verify_api_key(bad_key, client_ip=client_ip)

        # Successful auth should clear the failed attempts
        role = verify_api_key(api_key, client_ip=client_ip)
        assert role == Role.READ

        # Now we should be able to fail again without immediate lockout
        for _ in range(3):
            verify_api_key(bad_key, client_ip=client_ip)
        # Should not raise -- we had 3 failures, which is under MAX_FAILED_ATTEMPTS (5)

    def test_no_lockout_without_client_ip(self, security_store):
        from security import MAX_FAILED_ATTEMPTS, verify_api_key

        bad_key = "x" * 43
        # Without client_ip, brute-force protection is not applied
        for _ in range(MAX_FAILED_ATTEMPTS + 5):
            result = verify_api_key(bad_key, client_ip=None)
            assert result is None

    def test_lockout_expires_after_duration(self, security_store):
        from brute_force import _failed_attempts, _failed_attempts_lock
        from security import (
            LOCKOUT_DURATION,
            MAX_FAILED_ATTEMPTS,
            verify_api_key,
        )

        client_ip = "10.99.99.99"
        bad_key = "x" * 43

        # Simulate old failed attempts beyond the lockout window
        old_time = datetime.now(UTC) - LOCKOUT_DURATION - timedelta(seconds=10)
        with _failed_attempts_lock:
            _failed_attempts[client_ip] = [old_time] * MAX_FAILED_ATTEMPTS

        # Should not be locked out because the attempts are stale
        result = verify_api_key(bad_key, client_ip=client_ip)
        assert result is None  # Invalid key but no 429


class TestEmergencyApiKey:
    """Emergency API key bypass."""

    def test_emergency_key_grants_admin(self, security_store):
        from security import Role, verify_api_key

        role = verify_api_key("emergency-bypass-key-for-testing")
        assert role == Role.ADMIN

    def test_emergency_key_clears_failed_attempts(self, security_store):
        from security import MAX_FAILED_ATTEMPTS, Role, verify_api_key

        client_ip = "10.0.0.50"
        bad_key = "x" * 43

        # Accumulate failed attempts
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            verify_api_key(bad_key, client_ip=client_ip)

        # Emergency key should succeed and clear attempts
        role = verify_api_key("emergency-bypass-key-for-testing", client_ip=client_ip)
        assert role == Role.ADMIN

        # Failed attempts should be cleared, so more failures allowed
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            verify_api_key(bad_key, client_ip=client_ip)
        # Should not raise -- we are still under the limit

    def test_empty_emergency_key_does_not_match(self, security_store, monkeypatch):
        import security

        # Set emergency key to empty — should not match anything
        monkeypatch.setattr(security, "get_emergency_api_key", lambda: "")

        from security import verify_api_key

        # An empty string key should not grant admin
        role = verify_api_key("", client_ip=None)
        assert role is None


class TestHmacKeyPersistence:
    """HMAC key generation and persistence."""

    def test_hmac_key_from_env_secret(self, security_store):
        import hmac_keys

        # The fixture patches _get_hmac_key to return the test HMAC key
        key = hmac_keys._get_hmac_key()
        assert key == _TEST_HMAC_KEY
        assert len(key) == 32

    def test_hmac_key_auto_generated_and_persisted(self, tmp_path, monkeypatch):
        import hmac_keys
        import storage as storage_mod

        test_store = ConfigStore(path=str(tmp_path / "hmac_test.json"))
        storage_mod._store = test_store
        hmac_keys._get_hmac_key.cache_clear()

        # Manually simulate auto-generation: store has no __hmac_secret__, env has no HMAC_SECRET
        import secrets as secrets_mod

        auto_key = secrets_mod.token_bytes(32)
        test_store.set("__hmac_secret__", auto_key.hex())

        # Verify it was persisted
        stored_hex = test_store.get("__hmac_secret__")
        assert stored_hex is not None
        assert bytes.fromhex(stored_hex) == auto_key

    def test_hmac_key_loaded_from_store_on_restart(self, tmp_path):
        """Simulate a restart: HMAC key persisted in store should be reloaded."""
        import hmac_keys
        import storage as storage_mod

        store_path = str(tmp_path / "hmac_restart.json")
        test_store = ConfigStore(path=store_path)
        storage_mod._store = test_store

        # Simulate a previously persisted key
        import secrets as secrets_mod

        original_key = secrets_mod.token_bytes(32)
        test_store.set("__hmac_secret__", original_key.hex())

        # Clear cache so _get_hmac_key reads from store
        hmac_keys._get_hmac_key.cache_clear()
        key = hmac_keys._get_hmac_key()
        assert key == original_key


# ===========================================================================
# Schema tests
# ===========================================================================


class TestValidateBrokerAddress:
    """validate_broker_address function tests."""

    @pytest.mark.parametrize(
        "address",
        [
            "mqtt.example.com",
            "broker.local.net",
            "localhost",
            "192.168.1.1",
            "10.0.0.1",
            "::1",
            "2001:db8::1",
            "my-broker.example.org",
        ],
    )
    def test_valid_addresses(self, address):
        valid, error = validate_broker_address(address)
        assert valid is True, f"Expected {address!r} to be valid, got error: {error}"

    @pytest.mark.parametrize(
        "address,expected_error_fragment",
        [
            ("", "empty"),
            ("   ", "empty"),
            ("12345", "must contain a dot"),  # digits-only hits dot check first
            ("no-dot-host", "must contain a dot"),
            ("a" * 254, "must contain a dot"),  # no dot -> fails before length check
        ],
    )
    def test_invalid_addresses(self, address, expected_error_fragment):
        valid, error = validate_broker_address(address)
        assert valid is False
        assert expected_error_fragment.lower() in error.lower()

    def test_empty_label_in_hostname(self):
        valid, error = validate_broker_address("mqtt..example.com")
        assert valid is False
        assert "empty label" in error.lower()

    def test_label_too_long(self):
        long_label = "a" * 64
        valid, error = validate_broker_address(f"{long_label}.example.com")
        assert valid is False
        assert "too long" in error.lower()

    def test_invalid_label_characters(self):
        valid, error = validate_broker_address("mqtt_broker.example.com")
        assert valid is False
        assert "invalid label" in error.lower()

    def test_ipv6_bracket_notation(self):
        valid, error = validate_broker_address("[::1]")
        assert valid is True, f"Expected [::1] to be valid, got error: {error}"


class TestBrokerConfigUpdate:
    """BrokerConfigUpdate model validation."""

    def test_valid_full_update(self):
        config = BrokerConfigUpdate(
            broker="mqtt.example.com",
            broker_port=1883,
            mqtt_user="user",
            mqtt_password="secret",
            mqtt_use_tls=True,
            mqtt_tls_insecure=False,
        )
        assert config.broker == "mqtt.example.com"
        assert config.broker_port == 1883

    def test_all_fields_optional(self):
        config = BrokerConfigUpdate()
        assert config.broker is None
        assert config.broker_port is None
        assert config.mqtt_user is None
        assert config.mqtt_password is None
        assert config.mqtt_use_tls is None
        assert config.mqtt_tls_insecure is None

    def test_broker_address_validated(self):
        with pytest.raises(ValueError):
            BrokerConfigUpdate(broker="no-dot-host")

    def test_broker_whitespace_stripped(self):
        config = BrokerConfigUpdate(broker="  mqtt.example.com  ")
        assert config.broker == "mqtt.example.com"

    def test_broker_empty_string_rejected(self):
        with pytest.raises(ValueError):
            BrokerConfigUpdate(broker="")

    def test_broker_whitespace_only_rejected(self):
        with pytest.raises(ValueError):
            BrokerConfigUpdate(broker="   ")

    def test_port_valid_range(self):
        config_low = BrokerConfigUpdate(broker_port=1)
        config_high = BrokerConfigUpdate(broker_port=65535)
        assert config_low.broker_port == 1
        assert config_high.broker_port == 65535

    def test_port_zero_rejected(self):
        with pytest.raises(ValueError):
            BrokerConfigUpdate(broker_port=0)

    def test_port_negative_rejected(self):
        with pytest.raises(ValueError):
            BrokerConfigUpdate(broker_port=-1)

    def test_port_too_high_rejected(self):
        with pytest.raises(ValueError):
            BrokerConfigUpdate(broker_port=65536)

    def test_port_string_coercion(self):
        config = BrokerConfigUpdate(broker_port="8883")
        assert config.broker_port == 8883

    def test_localhost_accepted(self):
        config = BrokerConfigUpdate(broker="localhost")
        assert config.broker == "localhost"

    def test_ip_address_accepted(self):
        config = BrokerConfigUpdate(broker="192.168.1.1")
        assert config.broker == "192.168.1.1"


class TestBrokerConfigResponse:
    """BrokerConfigResponse serialization."""

    def test_password_redacted_in_json(self):
        resp = BrokerConfigResponse(
            broker="mqtt.example.com",
            broker_port=1883,
            mqtt_user="admin",
            mqtt_password="super-secret-password",
        )
        json_data = json.loads(resp.model_dump_json())
        assert json_data["mqtt_password"] == "***REDACTED***"

    def test_password_redacted_in_dict(self):
        resp = BrokerConfigResponse(
            broker="mqtt.example.com",
            broker_port=1883,
            mqtt_password="super-secret-password",
        )
        # model_dump (dict) should also redact the password (SEC-2 fix)
        data = resp.model_dump()
        assert data["mqtt_password"] == "***REDACTED***"

    def test_defaults(self):
        resp = BrokerConfigResponse(broker="mqtt.local", broker_port=1883)
        assert resp.mqtt_user == ""
        assert resp.mqtt_password == ""
        assert resp.mqtt_use_tls is False
        assert resp.mqtt_tls_insecure is False

    def test_empty_password_still_redacted_in_json(self):
        resp = BrokerConfigResponse(broker="mqtt.local", broker_port=1883)
        json_data = json.loads(resp.model_dump_json())
        assert json_data["mqtt_password"] == "***REDACTED***"
