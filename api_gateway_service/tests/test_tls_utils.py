"""Unit tests for app.core.tls_utils — SSL context creation."""

import ssl
from unittest.mock import patch

from app.core.config import settings
from app.core.tls_utils import ssl_ctx_for


class TestSslCtxFor:
    def test_returns_context_for_wss(self):
        ctx = ssl_ctx_for("wss://secure.host:443/ws")
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_returns_none_for_ws(self):
        assert ssl_ctx_for("ws://plain.host:8080/ws") is None

    def test_returns_none_for_http(self):
        assert ssl_ctx_for("http://some.host") is None

    def test_insecure_mode_disables_verification(self):
        with patch.object(settings, "allow_insecure_tls", True):
            ctx = ssl_ctx_for("wss://insecure.host/ws")
            assert ctx.check_hostname is False
            assert ctx.verify_mode == ssl.CERT_NONE

    def test_secure_mode_by_default(self):
        with patch.object(settings, "allow_insecure_tls", False):
            ctx = ssl_ctx_for("wss://secure.host/ws")
            assert ctx.check_hostname is True
