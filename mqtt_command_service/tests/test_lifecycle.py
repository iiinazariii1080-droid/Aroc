"""
Unit tests for LifecycleManager.
"""
import threading
import time

from app.core.lifecycle import LifecycleManager


class TestLifecycleManager:
    """Test cases for LifecycleManager."""

    def test_initialization(self):
        """Test LifecycleManager initialization."""
        lifecycle = LifecycleManager(shutdown_timeout=5.0)

        assert lifecycle._shutdown_timeout == 5.0
        assert not lifecycle.is_shutting_down

    def test_register_component(self):
        """Test component registration."""
        lifecycle = LifecycleManager()

        startup_called = []
        shutdown_called = []

        def startup():
            startup_called.append(True)

        def shutdown():
            shutdown_called.append(True)

        lifecycle.register_component(
            name="test_component",
            startup=startup,
            shutdown=shutdown
        )

        # Startup should be called
        assert len(startup_called) == 1

        # Shutdown not called yet
        assert len(shutdown_called) == 0

    def test_shutdown_calls_all_components(self):
        """Test that shutdown calls all component shutdown callbacks."""
        lifecycle = LifecycleManager()

        shutdown_called = []

        def shutdown1():
            shutdown_called.append("component1")

        def shutdown2():
            shutdown_called.append("component2")

        lifecycle.register_component(
            name="component1",
            startup=lambda: None,
            shutdown=shutdown1
        )

        lifecycle.register_component(
            name="component2",
            startup=lambda: None,
            shutdown=shutdown2
        )

        # Shutdown
        lifecycle.shutdown()

        # Both should be called (in reverse order)
        assert len(shutdown_called) == 2
        assert "component1" in shutdown_called
        assert "component2" in shutdown_called

    def test_shutdown_sets_flag(self):
        """Test that shutdown sets the shutdown flag."""
        lifecycle = LifecycleManager()

        assert not lifecycle.is_shutting_down

        lifecycle.shutdown()

        assert lifecycle.is_shutting_down

    def test_multiple_shutdown_calls(self):
        """Test that multiple shutdown calls are safe."""
        lifecycle = LifecycleManager()

        shutdown_called = []

        def shutdown():
            shutdown_called.append(True)

        lifecycle.register_component(
            name="test",
            startup=lambda: None,
            shutdown=shutdown
        )

        # Call shutdown multiple times
        lifecycle.shutdown()
        lifecycle.shutdown()
        lifecycle.shutdown()

        # Should only call shutdown once per component
        assert len(shutdown_called) == 1

    def test_wait_for_shutdown(self):
        """Test wait_for_shutdown method."""
        lifecycle = LifecycleManager()

        # Start thread that will shutdown after delay
        def delayed_shutdown():
            time.sleep(0.1)
            lifecycle.shutdown()

        thread = threading.Thread(target=delayed_shutdown)
        thread.start()

        # Wait for shutdown (should return when shutdown is called)
        start_time = time.time()
        lifecycle.wait_for_shutdown()
        elapsed = time.time() - start_time

        # Should have waited at least 0.1 seconds
        assert elapsed >= 0.1
        assert lifecycle.is_shutting_down

        thread.join()

