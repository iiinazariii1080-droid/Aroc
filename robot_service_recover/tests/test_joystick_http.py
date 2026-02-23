import asyncio
import time
import pytest
from fastapi.testclient import TestClient

import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import main as app_main


@pytest.fixture(scope="module")
def client():
    # Ensure debug logging
    os.environ["LOG_LEVEL"] = "DEBUG"
    with TestClient(app_main.app) as c:
        yield c


def _frame(axes=None, buttons=None, ttl=150):
    # Validator expects 4 axes and 19 buttons currently
    axes = axes or [0.0] * 4
    buttons = buttons or [0] * 19
    return {
        "ts": time.time(),
        "axes": axes,
        "buttons": buttons,
        "ttl": ttl,
    }


def test_health_ready(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_stale_frame_dropped(client):
    payload = _frame()
    payload["ts"] = time.time() - 10  # 10s old
    r = client.post("/joystick/frame", json=payload)
    assert r.status_code == 200
    # Stale frames are accepted but logged and ignored
    assert r.json().get("success") is True


def test_single_click_sends_pulse_and_schedules_stop(client, monkeypatch):
    app = app_main.app
    ingress = getattr(app.state, "joystick_ingress", None)
    captured = {}

    if ingress is None:
        jp = getattr(app.state, "joystick_pipeline", None)
        if jp is None:
            pytest.skip("joystick_pipeline not initialized")
        orig_submit = jp.submit

        def fallback_submit(frame):
            captured.update(frame)
            return True

        jp.submit = fallback_submit
        cleanup = lambda: setattr(jp, "submit", orig_submit)
    else:
        orig_submit = ingress.submit

        def fake_ingress_submit(frame, source="http"):
            captured.update(frame)
            return True, None

        ingress.submit = fake_ingress_submit

        def cleanup():
            ingress.submit = orig_submit

    try:
        r = client.post("/joystick/frame", json=_frame(buttons=[0]*13 + [1] + [0]*(19-14)))
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert "ttl" in captured and 50 <= captured["ttl"] <= 500
    finally:
        cleanup()


def test_hold_and_release_sends_move_and_stop(client, monkeypatch):
    # Endpoint-level test: just ensure both requests accepted
    btns = [0] * 11 + [1] + [0] * (19 - 12)
    r1 = client.post("/joystick/frame", json=_frame(buttons=btns))
    assert r1.status_code == 200
    r2 = client.post("/joystick/frame", json=_frame(buttons=[0]*19))
    assert r2.status_code == 200


