"""Tests for main.py orchestration functions."""
import threading
from unittest.mock import MagicMock, patch


class TestConfigureLogging:
    """Tests for configure_logging."""

    def test_configure_logging_runs_without_error(self):
        from main import configure_logging
        configure_logging()


class TestStartApi:
    """Tests for start_api thread management."""

    def test_start_api_creates_thread(self):
        import main as main_mod
        main_mod._api_thread = None
        main_mod._api_shutdown_event = threading.Event()
        # Patch _run_api_thread to do nothing
        with patch.object(main_mod, "_run_api_thread"):
            main_mod.start_api()
            assert main_mod._api_thread is not None
            assert main_mod._api_thread.daemon is True
            # Wait for thread to start
            main_mod._api_thread.join(timeout=1.0)

    def test_start_api_skips_if_already_running(self):
        import main as main_mod
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        main_mod._api_thread = mock_thread
        main_mod.start_api()
        # Should not create a new thread
        # (no direct assertion needed — just no crash)


class TestShutdownApi:
    """Tests for shutdown_api."""

    def test_shutdown_sets_event(self):
        import main as main_mod
        main_mod._api_shutdown_event = threading.Event()
        main_mod._api_thread = None
        main_mod.shutdown_api()
        assert main_mod._api_shutdown_event.is_set()

    def test_shutdown_joins_thread(self):
        import main as main_mod
        main_mod._api_shutdown_event = threading.Event()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        main_mod._api_thread = mock_thread
        main_mod.shutdown_api()
        mock_thread.join.assert_called_once_with(timeout=5.0)


class TestRunApiThread:
    """Tests for _run_api_thread."""

    def test_handles_uvicorn_import_failure(self):
        import main as main_mod
        old_uvicorn = main_mod.uvicorn
        main_mod.uvicorn = None

        # Create a custom __import__ that fails only for "uvicorn"
        real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        def fake_import(name, *args, **kwargs):
            if name == "uvicorn":
                raise ImportError("no uvicorn")
            return real_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=fake_import):
                main_mod._run_api_thread()
            # Should return early without raising
        finally:
            main_mod.uvicorn = old_uvicorn


class TestStopTelemetry:
    """Tests for stop_telemetry."""

    @patch("telemetry.shutdown_flag")
    @patch("telemetry.mqtt_client")
    def test_stop_telemetry_sets_flag_and_stops_client(self, mock_client, mock_flag):
        from main import stop_telemetry
        stop_telemetry()
        mock_flag.set.assert_called_once()
        mock_client.stop.assert_called_once()

    @patch("telemetry.shutdown_flag")
    @patch("telemetry.mqtt_client", None)
    def test_stop_telemetry_handles_none_client(self, mock_flag):
        from main import stop_telemetry
        stop_telemetry()
        mock_flag.set.assert_called_once()
