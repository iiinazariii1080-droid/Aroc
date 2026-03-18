"""Unit tests for HeartbeatPublisher.

Covers: start/stop lifecycle, backoff on failures, recovery, shutdown exit.
No time.sleep() — all sync via threading.Event.
"""

import threading
from unittest.mock import MagicMock

from heartbeat_publisher import HeartbeatPublisher


class TestStartStop:
    def test_start_with_zero_interval_no_thread(self):
        shutdown = threading.Event()
        pub = HeartbeatPublisher(
            interval=0,
            shutdown_event=shutdown,
            publish_system_status=MagicMock(),
            publish_connection_status=MagicMock(),
        )
        pub.start()
        assert pub._thread is None

    def test_start_with_negative_interval_no_thread(self):
        shutdown = threading.Event()
        pub = HeartbeatPublisher(
            interval=-1,
            shutdown_event=shutdown,
            publish_system_status=MagicMock(),
            publish_connection_status=MagicMock(),
        )
        pub.start()
        assert pub._thread is None

    def test_start_creates_daemon_thread(self):
        shutdown = threading.Event()
        sys_fn = MagicMock()
        conn_fn = MagicMock()
        pub = HeartbeatPublisher(
            interval=1.0,
            shutdown_event=shutdown,
            publish_system_status=sys_fn,
            publish_connection_status=conn_fn,
        )
        pub.start()
        assert pub._thread is not None
        assert pub._thread.is_alive()
        assert pub._thread.daemon is True

        shutdown.set()
        pub.stop()
        assert pub._thread is None

    def test_double_start_no_second_thread(self):
        shutdown = threading.Event()
        pub = HeartbeatPublisher(
            interval=1.0,
            shutdown_event=shutdown,
            publish_system_status=MagicMock(),
            publish_connection_status=MagicMock(),
        )
        pub.start()
        first_thread = pub._thread
        pub.start()
        assert pub._thread is first_thread

        shutdown.set()
        pub.stop()

    def test_stop_joins_and_clears_thread(self):
        shutdown = threading.Event()
        pub = HeartbeatPublisher(
            interval=1.0,
            shutdown_event=shutdown,
            publish_system_status=MagicMock(),
            publish_connection_status=MagicMock(),
        )
        pub.start()
        assert pub._thread is not None
        shutdown.set()
        pub.stop()
        assert pub._thread is None


class TestPublishBehavior:
    def test_publishes_both_status_types(self):
        shutdown = threading.Event()
        called = threading.Event()
        sys_fn = MagicMock(side_effect=lambda: called.set())
        conn_fn = MagicMock()
        pub = HeartbeatPublisher(
            interval=1.0,
            shutdown_event=shutdown,
            publish_system_status=sys_fn,
            publish_connection_status=conn_fn,
        )
        pub.start()
        called.wait(timeout=5)
        shutdown.set()
        pub.stop()

        sys_fn.assert_called()
        conn_fn.assert_called()

    def test_shutdown_exits_loop_immediately(self):
        shutdown = threading.Event()
        call_count = 0

        def counting_publish():
            nonlocal call_count
            call_count += 1
            # After first publish, signal shutdown
            shutdown.set()

        pub = HeartbeatPublisher(
            interval=60.0,  # Long interval — would hang without shutdown
            shutdown_event=shutdown,
            publish_system_status=counting_publish,
            publish_connection_status=MagicMock(),
        )
        pub.start()
        pub.stop()

        # Should have published at least once then exited
        assert call_count >= 1


class TestBackoff:
    def test_failure_does_not_crash_loop(self):
        shutdown = threading.Event()
        call_count = 0
        done = threading.Event()

        def failing_then_ok():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("publish failed")
            # After 2 failures, we recovered — signal done
            done.set()

        pub = HeartbeatPublisher(
            interval=1.0,
            shutdown_event=shutdown,
            publish_system_status=failing_then_ok,
            publish_connection_status=MagicMock(),
        )
        pub.start()
        # Wait for the 3rd call (recovery)
        done.wait(timeout=15)
        shutdown.set()
        pub.stop()

        # Should have been called at least 3 times (2 failures + 1 success)
        assert call_count >= 3

    def test_interval_clamped_to_minimum(self):
        shutdown = threading.Event()
        pub = HeartbeatPublisher(
            interval=0.001,  # Below minimum
            shutdown_event=shutdown,
            publish_system_status=MagicMock(),
            publish_connection_status=MagicMock(),
        )
        assert pub._interval == 1.0
        shutdown.set()
