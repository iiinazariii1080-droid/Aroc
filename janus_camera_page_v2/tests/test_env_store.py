"""Tests for app/services/env_store.py — atomic .env file I/O."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.env_store import read_env, write_env_atomic


@pytest.fixture
def env_dir(tmp_path):
    """Return a temp directory and patch settings to use it."""
    from app.core.settings import Settings

    env_path = tmp_path / "cam-rgb.env"
    lock_path = tmp_path / ".cam-rgb.lock"

    fake_settings = Settings.__new__(Settings)
    object.__setattr__(fake_settings, "env_path", env_path)
    object.__setattr__(fake_settings, "lock_path", lock_path)
    # Copy other fields from default settings
    default = Settings()
    for field in Settings.__dataclass_fields__:
        if field not in ("env_path", "lock_path"):
            object.__setattr__(fake_settings, field, getattr(default, field))

    with patch("app.services.env_store.get_settings", return_value=fake_settings):
        yield env_path


class TestWriteEnvAtomic:
    def test_creates_file(self, env_dir):
        write_env_atomic({"WIDTH": "640", "HEIGHT": "480"})
        assert env_dir.exists()
        content = env_dir.read_text()
        assert "WIDTH=640" in content
        assert "HEIGHT=480" in content

    def test_overwrites_existing(self, env_dir):
        env_dir.write_text("OLD=data\n")
        write_env_atomic({"NEW": "value"})
        content = env_dir.read_text()
        assert "OLD" not in content
        assert "NEW=value" in content


class TestReadEnv:
    def test_reads_key_values(self, env_dir):
        env_dir.write_text("WIDTH=640\nHEIGHT=480\n")
        data = read_env()
        assert data["WIDTH"] == "640"
        assert data["HEIGHT"] == "480"

    def test_empty_when_missing(self, env_dir):
        # env_dir doesn't exist yet (not written)
        data = read_env()
        assert data == {}

    def test_skips_comments_and_blanks(self, env_dir):
        env_dir.write_text("# comment\n\nFPS=30\n")
        data = read_env()
        assert data == {"FPS": "30"}
