"""Tests for janus_camera_page — settings, health, basic routes."""

import os
import sys
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class TestSettings:
    """Test app.core.settings.Settings defaults."""

    def test_default_app_title(self, settings):
        assert settings.app_title == "cam-control"

    def test_default_janus_url(self, settings):
        assert "janus" in settings.janus_url.lower() or "8088" in settings.janus_url

    def test_cors_origin_regex_is_valid(self, settings):
        import re
        pat = re.compile(settings.cors_origin_regex)
        assert pat.fullmatch("http://192.168.1.10:8900")

    def test_camera_device_default(self, settings):
        assert settings.camera_device  # non-empty string

    def test_janus_mount_id_positive(self, settings):
        assert settings.janus_mount_id > 0

    def test_watchdog_defaults(self, settings):
        assert settings.watchdog_interval_sec > 0
        assert settings.watchdog_stale_ms > 0

    def test_janus_ws_backends_nonempty(self, settings):
        assert isinstance(settings.janus_ws_backends, dict)
        assert len(settings.janus_ws_backends) >= 1


class TestHealthEndpoint:
    """Test the /healthz endpoint."""

    @pytest.mark.asyncio
    async def test_healthz_returns_structured_response(self, client):
        r = await client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        # Deep healthz now checks Janus+stream; in test env Janus is down
        # so ok may be False, but structure must be correct
        assert "ok" in body
        assert "mode" in body
        assert "janus_reachable" in body
        assert "stream_active" in body
        assert "details" in body
        assert isinstance(body["ok"], bool)


class TestStaticMount:
    """Test that static files are served."""

    @pytest.mark.asyncio
    async def test_static_css_or_js(self, client):
        """Static mount should be accessible (even if file doesn't exist → 404)."""
        r = await client.get("/static/nonexistent.js")
        # File doesn't exist → static mount returns 404
        assert r.status_code == 404
