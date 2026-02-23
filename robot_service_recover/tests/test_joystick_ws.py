import time

import os
import sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import main as app_main  # noqa: E402


def _ws_frame(seq: int = 1, axes_count: int = 4, buttons_count: int = 19):
    return {
        "seq": seq,
        "ts": time.time(),
        "axes": [0.0] * axes_count,
        "buttons": [0] * buttons_count,
        "ttl": 150,
    }


def test_websocket_joystick_frame_success():
    with TestClient(app_main.app) as client:
        with client.websocket_connect("/joystick/connect") as ws:
            payload = _ws_frame(seq=42)
            ws.send_json(payload)
            response = ws.receive_json()
            assert response["seq"] == 42
            assert response["success"] is True
            assert response.get("error") in (None, "queue_full")


def test_websocket_joystick_frame_validation_error():
    with TestClient(app_main.app) as client:
        with client.websocket_connect("/joystick/connect") as ws:
            payload = _ws_frame(seq=7, axes_count=2)  # invalid axes length
            ws.send_json(payload)
            response = ws.receive_json()
            assert response["seq"] == 7
            assert response["success"] is False
            assert "validation_error" in response["error"]

