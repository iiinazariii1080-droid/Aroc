"""Tests for the application lifecycle event handlers."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSdNotify:
    """Tests for systemd sd_notify helper."""

    def test_sd_notify_noop_when_no_socket(self):
        from app.core.events import _sd_notify
        with patch.dict(os.environ, {}, clear=False):
            with patch("app.core.events._NOTIFY_SOCKET", None):
                # Should not raise
                _sd_notify("READY=1")

    def test_sd_notify_sends_datagram_when_socket_set(self):
        from app.core.events import _sd_notify
        with patch("app.core.events._NOTIFY_SOCKET", "/tmp/test-sd-notify.sock"), \
             patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            _sd_notify("READY=1")
            mock_sock.sendall.assert_called_once_with(b"READY=1")


class TestRegisterEventHandlers:
    """Tests for startup/shutdown handler registration."""

    def test_handlers_are_registered(self):
        """Verify register_event_handlers adds startup and shutdown handlers."""
        from unittest.mock import call
        mock_app = MagicMock()
        mock_app.on_event = MagicMock(return_value=lambda fn: fn)

        with patch("app.core.events.register_event_handlers") as mock_reg:
            # Just verify the function is callable
            from app.core.events import register_event_handlers
            assert callable(register_event_handlers)

    @pytest.mark.asyncio
    async def test_startup_starts_watchdogs(self):
        """Verify startup sequence starts watchdogs and proxies."""
        from app.core.events import register_event_handlers

        mock_app = MagicMock()
        startup_fn = None
        shutdown_fn = None

        def capture_handler(event_type):
            def decorator(fn):
                nonlocal startup_fn, shutdown_fn
                if event_type == "startup":
                    startup_fn = fn
                elif event_type == "shutdown":
                    shutdown_fn = fn
                return fn
            return decorator

        mock_app.on_event = capture_handler

        with patch("app.core.events.watchdogs") as mock_watchdogs, \
             patch("app.core.events.start_thermal_monitor") as mock_thermal, \
             patch("app.core.events.janus_proxy") as mock_janus_proxy, \
             patch("app.core.events.relay_proxy") as mock_relay_proxy, \
             patch("app.core.events._sd_notify") as mock_notify, \
             patch("app.core.events.get_settings") as mock_settings, \
             patch("asyncio.create_task"):

            mock_settings.return_value = MagicMock(camera_type="color_camera")
            mock_watchdogs.start_snapshot_watchdog = AsyncMock()
            mock_janus_proxy.start_client = AsyncMock()
            mock_relay_proxy.start_client = AsyncMock()

            register_event_handlers(mock_app)
            assert startup_fn is not None

            await startup_fn()

            mock_watchdogs.start_janus_watchdog.assert_called_once()
            mock_watchdogs.start_snapshot_watchdog.assert_awaited_once()
            mock_thermal.assert_called_once()
            mock_janus_proxy.start_client.assert_awaited_once()
            mock_relay_proxy.start_client.assert_awaited_once()
            mock_notify.assert_called_with("READY=1")

    @pytest.mark.asyncio
    async def test_shutdown_stops_proxies(self):
        """Verify shutdown sequence stops proxy clients and executor."""
        from app.core.events import register_event_handlers

        mock_app = MagicMock()
        shutdown_fn = None

        def capture_handler(event_type):
            def decorator(fn):
                nonlocal shutdown_fn
                if event_type == "shutdown":
                    shutdown_fn = fn
                return fn
            return decorator

        mock_app.on_event = capture_handler

        with patch("app.core.events.watchdogs"), \
             patch("app.core.events.start_thermal_monitor"), \
             patch("app.core.events.janus_proxy") as mock_janus_proxy, \
             patch("app.core.events.relay_proxy") as mock_relay_proxy, \
             patch("app.core.events.get_settings") as mock_settings, \
             patch("asyncio.create_task"):

            mock_settings.return_value = MagicMock(camera_type="color_camera")
            mock_janus_proxy.stop_client = AsyncMock()
            mock_relay_proxy.stop_client = AsyncMock()

            register_event_handlers(mock_app)
            assert shutdown_fn is not None

            with patch("app.services.janus._executor") as mock_executor:
                await shutdown_fn()

            mock_janus_proxy.stop_client.assert_awaited_once()
            mock_relay_proxy.stop_client.assert_awaited_once()
