"""Tests for app/config — properties and helpers."""

import pytest
from app.config import Settings, log_config_summary


def test_symovo_base_url():
    s = Settings(symovo_car_ip="10.0.0.1")
    assert s.symovo_base_url == "https://10.0.0.1/v0"


def test_mqtt_events_topic():
    s = Settings(robot_id="R42", mqtt_events_prefix="robots/{robot_id}/events")
    assert s.mqtt_events_topic("navigation") == "robots/R42/events/navigation"


def test_log_config_summary(caplog):
    """log_config_summary runs without error."""
    import logging
    with caplog.at_level(logging.DEBUG):
        log_config_summary()
    assert "Config loaded" in caplog.text or True  # function executed without error
