"""DEF-06: Template rendering tests — Jinja2 migration validation.

Verifies that:
- HTML templates render without raw __CAM_TYPE__ placeholders
- Jinja2 autoescape prevents XSS via template variables
- Template variables are correctly substituted
- Both color_view and depth_view templates render correctly
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from jinja2 import Environment, FileSystemLoader, select_autoescape


# ── Direct Jinja2 env for unit tests (no FastAPI dependency) ──────────

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "templates"
)
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "htm"]),
)


# ── FastAPI integration fixture (color_camera, default) ──────────────

_TEST_TOKEN = "test-token-templates-32chars!!"


@pytest.fixture
def app():
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch.dict(os.environ, {"CAM_ADMIN_TOKEN": _TEST_TOKEN}):
        import app.core.admin as _admin
        _admin.ADMIN_TOKEN = _TEST_TOKEN
        from app.core.app import create_app
        return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Integration tests: color_view via FastAPI ────────────────────────

class TestTemplateRendering:
    """DEF-06: Templates must use Jinja2, not string.replace()."""

    async def test_no_raw_placeholders(self, client):
        """Rendered HTML must not contain raw __CAM_TYPE__ placeholders."""
        resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        body = resp.text
        assert "__CAM_TYPE__" not in body, "Raw placeholder found in rendered template"

    async def test_cam_type_rendered(self, client):
        """HTML must contain the actual camera_type value."""
        resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        body = resp.text
        assert "color_camera" in body, "camera_type not rendered in template"

    async def test_api_prefix_rendered(self, client):
        """data-api-prefix must contain the actual camera_type."""
        resp = await client.get("/color_view.html")
        body = resp.text
        assert 'data-api-prefix="/api/v1/color_camera"' in body

    async def test_stream_id_rendered(self, client):
        """data-prefer-stream-id must be rendered (not the Jinja2 tag)."""
        resp = await client.get("/color_view.html")
        body = resp.text
        assert 'data-prefer-stream-id="1305"' in body
        assert "{{ stream_id }}" not in body

    async def test_joystick_mode_rendered(self, client):
        """Joystick mode must be rendered based on camera type."""
        resp = await client.get("/color_view.html")
        body = resp.text
        assert 'data-joystick-mode="always"' in body
        assert "{{ joystick_mode }}" not in body


# ── Unit tests: depth_view via direct Jinja2 (avoids module-reload fragility) ──

class TestDepthViewRendering:
    """depth_view.html must render correctly with Jinja2."""

    def _render_depth(self):
        tmpl = _jinja_env.get_template("depth_view.html")
        return tmpl.render(cam_type="depth_camera")

    def test_no_raw_placeholders(self):
        body = self._render_depth()
        assert "__CAM_TYPE__" not in body, "Raw placeholder found in depth template"
        assert "{{ cam_type }}" not in body, "Raw Jinja2 tag in depth template"

    def test_cam_type_rendered(self):
        body = self._render_depth()
        assert "depth_camera" in body

    def test_api_prefix_rendered(self):
        body = self._render_depth()
        assert '/api/v1/depth_camera' in body

    def test_stream_id_1306(self):
        body = self._render_depth()
        assert 'data-prefer-stream-id="1306"' in body

    def test_joystick_mode_off(self):
        body = self._render_depth()
        assert 'data-joystick-mode="off"' in body

    def test_depth_endpoint_rendered(self):
        body = self._render_depth()
        assert "data-depth-endpoint" in body

    def test_gripper_calibration_params(self):
        body = self._render_depth()
        for attr in ("data-gripper-fx", "data-gripper-fy", "data-gripper-cx", "data-gripper-cy"):
            assert attr in body, f"Missing depth calibration attribute: {attr}"


# ── XSS and conditional block tests ──────────────────────────────────

class TestJinja2XSS:
    """Jinja2 autoescape must prevent XSS via template variables."""

    def test_xss_in_cam_type_escaped(self):
        tmpl = _jinja_env.get_template("color_view.html")
        html = tmpl.render(
            cam_type='<script>alert(1)</script>',
            stream_id=1305,
            stream_name="test",
            joystick_mode="always",
            depth_features_script=False,
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_depth_features_block_hidden_when_false(self):
        tmpl = _jinja_env.get_template("color_view.html")
        html = tmpl.render(
            cam_type="color_camera",
            stream_id=1305,
            stream_name="RGB",
            joystick_mode="always",
            depth_features_script=False,
        )
        assert "{% if" not in html, "Raw Jinja2 block tags in output"
        assert "depth_features.js" not in html

    def test_depth_features_block_shown_when_true(self):
        tmpl = _jinja_env.get_template("color_view.html")
        html = tmpl.render(
            cam_type="color_camera",
            stream_id=1305,
            stream_name="RGB",
            joystick_mode="always",
            depth_features_script=True,
        )
        assert "depth_features.js" in html


# ── Route-level integration tests ─────────────────────────────────────

class TestColorViewReturnsHtml:
    """GET /color_view.html returns 200 with HTML content."""

    async def test_color_view_returns_html(self, client):
        resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "<html" in resp.text.lower() or "<!doctype" in resp.text.lower()


class TestJanusJsServesContent:
    """GET /janus.js returns JS content (local file or CDN fallback)."""

    async def test_janus_js_serves_content(self, client):
        resp = await client.get("/janus.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "")


class TestPlayerScriptReturnsJs:
    """GET /player/<script>.js returns JS files from player directory."""

    async def test_player_config_js(self, client):
        """config.js in the player directory is served with JS content type."""
        resp = await client.get("/player/config.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "")

    async def test_player_nonexistent_returns_404(self, client):
        """Request for a non-existent player script returns 404."""
        resp = await client.get("/player/nonexistent_file_xyz.js")
        assert resp.status_code == 404


class TestPathTraversalBlocked:
    """Path traversal attempts are blocked in player script routes."""

    async def test_path_traversal_blocked(self, client):
        """GET /player/../../etc/passwd is rejected (404)."""
        resp = await client.get("/player/../../etc/passwd")
        assert resp.status_code == 404

    async def test_path_traversal_dotdot_in_middle(self, client):
        """GET /player/adapters/../../etc/passwd is rejected."""
        resp = await client.get("/player/adapters/../../etc/passwd")
        assert resp.status_code == 404

    async def test_path_traversal_absolute_path_blocked(self, client):
        """Absolute path in player route is rejected."""
        resp = await client.get("/player//etc/passwd")
        assert resp.status_code == 404

    async def test_static_via_api_traversal_blocked(self, client):
        """Path traversal in /api/v1/<cam>/static/ is rejected."""
        resp = await client.get("/api/v1/color_camera/static/../../../etc/passwd")
        assert resp.status_code == 404
