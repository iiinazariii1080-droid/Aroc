"""
Legacy API module for backward compatibility.

Exposes FastAPI app and helper shims expected by older tests.
"""
from app.main import app
from config import test_mqtt_connection  # type: ignore

__all__ = ["app", "test_mqtt_connection"]
