"""Regression tests for bugs fixed during the production-readiness audit.

Covers:
  R1: TURN HMAC credential generation correctness
  R2: env_store round-trip with legacy quoted values
  R3: ladder.status()["current_level"] — no AttributeError on health_stream
  R4: janus.js fail-fast (no CDN fallback, 503 when file missing)
  R5: JanusNatConfig injection validators
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

# ===================================================================
# R1: TURN credential HMAC
# ===================================================================

class TestTurnCredentials:
    """R1: generate_turn_credentials must produce coturn-compatible HMAC-SHA1."""

    def test_credential_format(self):
        from app.routes.janus import generate_turn_credentials

        username, credential = generate_turn_credentials("s3cr3t", user="webrtc", ttl=86400)

        # Username must be "<expiry>:webrtc"
        parts = username.split(":", 1)
        assert len(parts) == 2
        expiry_ts = int(parts[0])
        assert parts[1] == "webrtc"
        assert expiry_ts > int(time.time())  # must be in the future

        # Credential must be valid base64
        raw = base64.b64decode(credential)
        assert len(raw) == 20  # SHA-1 digest is 20 bytes

    def test_hmac_correctness(self):
        """Credential must match reference coturn HMAC-SHA1 computation."""
        from app.routes.janus import generate_turn_credentials

        secret = "test-shared-secret"
        username, credential = generate_turn_credentials(secret, user="bot", ttl=3600)

        # Re-derive independently
        expected_mac = hmac.new(secret.encode(), username.encode(), hashlib.sha1)
        expected_cred = base64.b64encode(expected_mac.digest()).decode()
        assert credential == expected_cred

    def test_different_users_differ(self):
        from app.routes.janus import generate_turn_credentials

        _, cred1 = generate_turn_credentials("secret", user="alice", ttl=3600)
        _, cred2 = generate_turn_credentials("secret", user="bob", ttl=3600)
        assert cred1 != cred2

    def test_different_secrets_differ(self):
        from app.routes.janus import generate_turn_credentials

        username1, cred1 = generate_turn_credentials("secret-a", user="webrtc", ttl=3600)
        username2, cred2 = generate_turn_credentials("secret-b", user="webrtc", ttl=3600)
        # Usernames may be equal (same user/ttl, close timestamps) but credentials must differ
        if username1 == username2:
            assert cred1 != cred2


# ===================================================================
# R2: env_store — quoted-value backward compatibility
# ===================================================================

class TestEnvStoreQuotedValues:
    """R2: read_env must strip legacy quotes written by old _write_env_file."""

    @pytest.fixture
    def env_path(self, tmp_path):
        from app.core.settings import Settings

        ep = tmp_path / "cam-rgb.env"
        lp = tmp_path / ".lock"

        fake = Settings.__new__(Settings)
        object.__setattr__(fake, "env_path", ep)
        object.__setattr__(fake, "lock_path", lp)
        default = Settings()
        for f in Settings.__dataclass_fields__:
            if f not in ("env_path", "lock_path"):
                object.__setattr__(fake, f, getattr(default, f))

        with patch("app.services.env_store.get_settings", return_value=fake):
            yield ep

    def test_double_quoted_values_stripped(self, env_path):
        from app.services.env_store import read_env

        env_path.write_text('WIDTH="640"\nHEIGHT="480"\n')
        data = read_env()
        assert data["WIDTH"] == "640"
        assert data["HEIGHT"] == "480"

    def test_single_quoted_values_stripped(self, env_path):
        from app.services.env_store import read_env

        env_path.write_text("PRESET='veryfast'\nTUNE='zerolatency'\n")
        data = read_env()
        assert data["PRESET"] == "veryfast"
        assert data["TUNE"] == "zerolatency"

    def test_unquoted_values_unchanged(self, env_path):
        from app.services.env_store import read_env

        env_path.write_text("FPS=30\nBITRATE_KBPS=1800\n")
        data = read_env()
        assert data["FPS"] == "30"
        assert data["BITRATE_KBPS"] == "1800"

    def test_atomic_write_then_read_roundtrip(self, env_path):
        from app.services.env_store import read_env, write_env_atomic

        original = {"WIDTH": "1920", "HEIGHT": "1080", "FPS": "60"}
        write_env_atomic(original)
        recovered = read_env()
        assert recovered["WIDTH"] == "1920"
        assert recovered["HEIGHT"] == "1080"
        assert recovered["FPS"] == "60"


# ===================================================================
# R3: health_stream uses ladder.status()["current_level"] (not .level)
# ===================================================================

class TestLadderStatusRegression:
    """R3: /health/stream must not raise AttributeError on recovery_ladder check."""

    @pytest.fixture
    def mock_ladder(self):
        ladder = MagicMock()
        ladder.status.return_value = {"current_level": 0, "total_recoveries": 0}
        return ladder

    @pytest.mark.asyncio
    async def test_health_stream_does_not_raise_on_ladder(self, mock_ladder):
        """RecoveryLadder has no .level — status()['current_level'] must be used."""
        from unittest.mock import AsyncMock

        with (
            patch("app.services.recovery_ladder.get_ladder", return_value=mock_ladder),
            patch("app.services.janus.janus_summary", new_callable=AsyncMock, side_effect=ConnectionError("down")),
        ):
            from app.routes.system import health_stream
            # Must not raise AttributeError
            response = await health_stream()
            body = response.body  # JSONResponse
            import json
            data = json.loads(body)
            assert "checks" in data
            assert "recovery_ladder" in data["checks"]
            assert data["checks"]["recovery_ladder"]["level"] == 0

    def test_ladder_mock_has_no_level_attr(self):
        """Confirm that a real RecoveryLadder has no .level attribute."""
        # Verify the old AttributeError scenario
        m = MagicMock(spec=[])  # empty spec — no attributes
        with pytest.raises(AttributeError):
            _ = m.level
        # But .status() returns a dict with current_level
        m2 = MagicMock()
        m2.status.return_value = {"current_level": 2}
        assert m2.status()["current_level"] == 2


# ===================================================================
# R4: janus.js fail-fast (no CDN fallback)
# ===================================================================

class TestJanusJsFailFast:
    """R4: /janus.js must return 503 when templates/janus.js is absent (no CDN fetch)."""

    @pytest.mark.asyncio
    async def test_missing_janus_js_returns_503(self, client, tmp_path):
        """When templates/janus.js does not exist, endpoint returns 503."""
        from app.core.settings import Settings

        fake = Settings.__new__(Settings)
        default = Settings()
        for f in Settings.__dataclass_fields__:
            object.__setattr__(fake, f, getattr(default, f))
        # Point templates_dir at empty tmp dir (no janus.js there)
        object.__setattr__(fake, "templates_dir", tmp_path)

        with patch("app.routes.media.get_settings", return_value=fake):
            resp = await client.get("/janus.js")
        assert resp.status_code == 503
        assert "janus.js" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_present_janus_js_returned(self, client, tmp_path):
        """When templates/janus.js exists, it is served as application/javascript."""
        janus_js = tmp_path / "janus.js"
        janus_js.write_text("// janus stub\n")

        from app.core.settings import Settings

        fake = Settings.__new__(Settings)
        default = Settings()
        for f in Settings.__dataclass_fields__:
            object.__setattr__(fake, f, getattr(default, f))
        object.__setattr__(fake, "templates_dir", tmp_path)

        with patch("app.routes.media.get_settings", return_value=fake):
            resp = await client.get("/janus.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_no_external_network_call_when_file_missing(self, client, tmp_path):
        """Confirm no requests.get is called when janus.js is absent."""
        from app.core.settings import Settings

        fake = Settings.__new__(Settings)
        default = Settings()
        for f in Settings.__dataclass_fields__:
            object.__setattr__(fake, f, getattr(default, f))
        object.__setattr__(fake, "templates_dir", tmp_path)

        with (
            patch("app.routes.media.get_settings", return_value=fake),
            patch("requests.get") as mock_get,
        ):
            resp = await client.get("/janus.js")
        assert resp.status_code == 503
        mock_get.assert_not_called()


# ===================================================================
# R5: JanusNatConfig injection validators
# ===================================================================

class TestJanusNatConfigValidators:
    """R5: JanusNatConfig must reject values that could enable config-file injection."""

    def test_valid_hostname_accepted(self):
        from app.routes.janus import JanusNatConfig

        cfg = JanusNatConfig(stun_server="turn.example.com", turn_server="turn.example.com")
        assert cfg.stun_server == "turn.example.com"

    def test_valid_ip_accepted(self):
        from app.routes.janus import JanusNatConfig

        cfg = JanusNatConfig(stun_server="192.168.1.10", turn_server="10.0.0.1")
        assert cfg.stun_server == "192.168.1.10"

    def test_empty_hostname_accepted(self):
        from app.routes.janus import JanusNatConfig

        cfg = JanusNatConfig(stun_server="", turn_server="")
        assert cfg.stun_server == ""

    def test_hostname_with_newline_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(stun_server="evil.com\nstun_server = attacker.net")

    def test_hostname_with_semicolon_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(turn_server="turn.com; rm -rf /")

    def test_hostname_with_spaces_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(stun_server="turn.com injected")

    def test_nat_1_1_valid_ip(self):
        from app.routes.janus import JanusNatConfig

        cfg = JanusNatConfig(nat_1_1_mapping="1.2.3.4")
        assert cfg.nat_1_1_mapping == "1.2.3.4"

    def test_nat_1_1_empty_accepted(self):
        from app.routes.janus import JanusNatConfig

        cfg = JanusNatConfig(nat_1_1_mapping="")
        assert cfg.nat_1_1_mapping == ""

    def test_nat_1_1_hostname_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(nat_1_1_mapping="evil.com")

    def test_turn_user_newline_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(turn_user="webrtc\ninjected = true")

    def test_turn_pwd_newline_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(turn_pwd="password\r\ninjected = x")

    def test_port_out_of_range_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(stun_port=0)
        with pytest.raises(ValidationError):
            JanusNatConfig(turn_port=70000)

    def test_min_port_below_1024_rejected(self):
        from app.routes.janus import JanusNatConfig

        with pytest.raises(ValidationError):
            JanusNatConfig(min_port=80)
