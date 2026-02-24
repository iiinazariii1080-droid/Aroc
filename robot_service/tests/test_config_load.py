"""Tests for app/config — _load_config_file edge cases."""

import pytest
import yaml

from app.config import _load_config_file


def test_load_real_yaml(tmp_path):
    """Real YAML file is parsed correctly."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump({"xarm_ip": "10.0.0.1", "debug": True}), encoding="utf-8")
    result = _load_config_file(str(cfg))
    assert result == {"xarm_ip": "10.0.0.1", "debug": True}


def test_load_non_dict_yaml(tmp_path):
    """Non-dict YAML returns empty dict."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- a\n- b\n", encoding="utf-8")
    assert _load_config_file(str(cfg)) == {}


def test_load_invalid_yaml(tmp_path):
    """Corrupt YAML returns empty dict."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("{{{{ bad !@#$", encoding="utf-8")
    assert _load_config_file(str(cfg)) == {}
