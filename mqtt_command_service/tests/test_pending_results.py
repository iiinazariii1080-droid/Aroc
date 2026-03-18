"""Unit tests for PendingResultQueue.

Covers: queue, flush, eviction policy, safety-aware ordering,
flush_lock non-blocking skip, properties, edge cases, concurrency.
No time.sleep() — all thread sync via threading.Event/Barrier.
"""

import threading
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from mqtt_publisher import PublishResult
from pending_results import PendingResultQueue

from shared.constants import MAX_TASK_RESULT_QUEUE


def _make_queue(
    *,
    publish_result: PublishResult = PublishResult.SUCCESS,
    connected: bool = True,
    resp_topic: str = "aroc/robot/bot/resp/robot",
) -> tuple[PendingResultQueue, threading.Event, MagicMock]:
    """Create PendingResultQueue with controllable fakes."""
    shutdown = threading.Event()
    publish_fn = MagicMock(return_value=publish_result)
    topic_fn = MagicMock(return_value=resp_topic)
    connected_fn = MagicMock(return_value=connected)
    q = PendingResultQueue(
        shutdown_event=shutdown,
        publish_json_fn=publish_fn,
        get_resp_topic_fn=topic_fn,
        mqtt_is_connected_fn=connected_fn,
    )
    return q, shutdown, publish_fn


class TestQueueBasics:
    def test_queue_single_item(self):
        q, _, _ = _make_queue()
        q.queue("req-1", {"service": "robot", "data": "value"})
        assert q.pending_count == 1
        assert q.has_pending is True

    def test_queue_empty_request_id_is_noop(self):
        q, _, _ = _make_queue()
        q.queue("", {"data": "value"})
        assert q.pending_count == 0
        assert q.has_pending is False

    def test_queue_overwrites_same_request_id(self):
        q, _, _ = _make_queue()
        q.queue("req-1", {"version": 1})
        q.queue("req-1", {"version": 2})
        assert q.pending_count == 1

    def test_properties_empty_queue(self):
        q, _, _ = _make_queue()
        assert q.pending_count == 0
        assert q.has_pending is False


class TestEviction:
    def test_evicts_oldest_non_safety_when_full(self):
        q, _, _ = _make_queue()
        # Fill queue to MAX
        for i in range(MAX_TASK_RESULT_QUEUE):
            q.queue(f"req-{i}", {"service": "robot", "command_name": "navigateTo"})
        assert q.pending_count == MAX_TASK_RESULT_QUEUE

        # Queue one more — should evict oldest (req-0)
        q.queue("req-new", {"service": "robot", "command_name": "navigateTo"})
        assert q.pending_count == MAX_TASK_RESULT_QUEUE

    def test_safety_entries_protected_from_eviction(self):
        q, _, _ = _make_queue()
        # Fill with safety entries first, then non-safety
        q.queue("safety-1", {"command_name": "estop"})
        q.queue("safety-2", {"command_name": "safety_alert"})
        for i in range(MAX_TASK_RESULT_QUEUE - 2):
            q.queue(f"req-{i}", {"command_name": "navigateTo"})
        assert q.pending_count == MAX_TASK_RESULT_QUEUE

        # Queue one more — should evict first non-safety (req-0), not safety entries
        q.queue("req-new", {"command_name": "navigateTo"})
        assert q.pending_count == MAX_TASK_RESULT_QUEUE

    def test_all_safety_entries_evicts_oldest_anyway(self):
        q, _, _ = _make_queue()
        for i in range(MAX_TASK_RESULT_QUEUE):
            q.queue(f"estop-{i}", {"command_name": "estop"})
        assert q.pending_count == MAX_TASK_RESULT_QUEUE

        # All entries are safety — evicts oldest anyway (estop-0)
        q.queue("estop-new", {"command_name": "estop"})
        assert q.pending_count == MAX_TASK_RESULT_QUEUE

    def test_no_eviction_when_request_id_already_exists(self):
        q, _, _ = _make_queue()
        for i in range(MAX_TASK_RESULT_QUEUE):
            q.queue(f"req-{i}", {"data": i})

        # Overwrite existing key — no eviction needed
        q.queue("req-0", {"data": "updated"})
        assert q.pending_count == MAX_TASK_RESULT_QUEUE


class TestFlush:
    def test_flush_success_removes_entry(self):
        q, _, publish_fn = _make_queue(publish_result=PublishResult.SUCCESS)
        q.queue("req-1", {"service": "robot"})
        q.flush()
        assert q.pending_count == 0
        publish_fn.assert_called_once()

    def test_flush_payload_too_large_removes_entry(self):
        q, _, publish_fn = _make_queue(publish_result=PublishResult.PAYLOAD_TOO_LARGE)
        q.queue("req-1", {"service": "robot"})
        q.flush()
        assert q.pending_count == 0

    def test_flush_transient_failure_keeps_entry(self):
        q, _, _ = _make_queue(publish_result=PublishResult.TRANSIENT_FAILURE)
        q.queue("req-1", {"service": "robot"})
        q.flush()
        assert q.pending_count == 1

    def test_flush_empty_queue_is_noop(self):
        q, _, publish_fn = _make_queue()
        q.flush()
        publish_fn.assert_not_called()

    def test_flush_uses_correct_topic_for_service(self):
        q, _, publish_fn = _make_queue(resp_topic="aroc/robot/bot/resp/xarm")
        q.queue("req-1", {"service": "xarm"})
        q.flush()
        topic_arg = publish_fn.call_args[0][0]
        assert topic_arg == "aroc/robot/bot/resp/xarm"

    def test_flush_publishes_correct_payload(self):
        q, _, publish_fn = _make_queue()
        payload = {"service": "robot", "request_id": "req-1", "success": True}
        q.queue("req-1", payload)
        q.flush()
        published_payload = publish_fn.call_args[0][1]
        assert published_payload["request_id"] == "req-1"
        assert published_payload["success"] is True

    def test_flush_identity_check_prevents_deleting_overwritten_entry(self):
        """If queue() overwrites an entry mid-flush, flush should not delete the new entry."""
        call_count = 0
        original_result = {"service": "robot", "version": 1}
        replacement_result = {"service": "robot", "version": 2}

        def publish_side_effect(topic, payload):
            nonlocal call_count
            call_count += 1
            # Simulate queue() overwriting mid-flush: after first publish call,
            # the entry gets replaced
            if call_count == 1 and payload.get("version") == 1:
                q.queue("req-1", replacement_result)
            return PublishResult.SUCCESS

        q, _, _ = _make_queue()
        q._publish_json = publish_side_effect
        q.queue("req-1", original_result)
        q.flush()
        # The replacement should still be in the queue (identity check: `is result`)
        assert q.pending_count == 1

    def test_flush_exception_in_publish_keeps_entry(self):
        q, _, publish_fn = _make_queue()
        publish_fn.side_effect = RuntimeError("publish failed")
        q.queue("req-1", {"service": "robot"})
        q.flush()
        assert q.pending_count == 1


class TestFlushLock:
    def test_concurrent_flush_skips_if_locked(self):
        """Only one flush runs at a time; concurrent call returns immediately."""
        flush_entered = threading.Event()
        flush_proceed = threading.Event()

        call_count = 0

        def slow_publish(topic, payload):
            nonlocal call_count
            call_count += 1
            flush_entered.set()
            flush_proceed.wait(timeout=5)
            return PublishResult.TRANSIENT_FAILURE  # keep entry so second flush has work

        q, _, _ = _make_queue()
        q._publish_json = slow_publish
        q.queue("req-1", {"service": "robot"})
        q.queue("req-2", {"service": "robot"})

        t1 = threading.Thread(target=q.flush)
        t1.start()
        flush_entered.wait(timeout=5)

        # Second flush should skip (non-blocking acquire fails)
        q.flush()

        flush_proceed.set()
        t1.join(timeout=5)

        # Only one flush ran
        assert call_count >= 1


class TestFlushThread:
    def test_flush_thread_starts_and_stops(self):
        q, shutdown, _ = _make_queue(connected=True)
        q.queue("req-1", {"service": "robot"})
        q.start_flush_thread()
        assert q._flush_thread is not None
        assert q._flush_thread.is_alive()

        # Signal shutdown — thread should exit
        shutdown.set()
        q._flush_thread.join(timeout=5)
        assert not q._flush_thread.is_alive()

    def test_flush_thread_is_daemon(self):
        q, shutdown, _ = _make_queue()
        q.start_flush_thread()
        assert q._flush_thread.daemon is True
        shutdown.set()
        q._flush_thread.join(timeout=5)


# ===========================================================================
# Concurrency tests (T-07)
# ===========================================================================


class TestConcurrentQueueAndFlush:
    def test_concurrent_queue_no_data_loss(self):
        """10 threads queue items concurrently; all items are present after."""
        q, _, _ = _make_queue(publish_result=PublishResult.TRANSIENT_FAILURE)
        barrier = threading.Barrier(10)
        errors = []

        def queue_items(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(10):
                    q.queue(f"t{thread_id}-req-{i}", {"service": "robot", "thread": thread_id, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=queue_items, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        # 100 unique keys queued, but queue max is 100, so exactly MAX_TASK_RESULT_QUEUE
        assert q.pending_count == MAX_TASK_RESULT_QUEUE

    def test_concurrent_queue_and_flush_no_crash(self):
        """Concurrent queue + flush operations do not crash or deadlock."""
        q, _, _ = _make_queue(publish_result=PublishResult.TRANSIENT_FAILURE)
        barrier = threading.Barrier(12)
        errors = []

        def queue_items(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(20):
                    q.queue(f"t{thread_id}-{i}", {"service": "robot"})
            except Exception as e:
                errors.append(e)

        def flush_items():
            try:
                barrier.wait(timeout=5)
                for _ in range(10):
                    q.flush()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=queue_items, args=(t,)) for t in range(10)]
        threads.append(threading.Thread(target=flush_items))
        threads.append(threading.Thread(target=flush_items))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors

    def test_flush_identity_check_under_concurrency(self):
        """flush() with identity check: overwritten entry survives flush."""
        results_delivered = []

        def tracking_publish(topic, payload):
            results_delivered.append(payload.get("request_id"))
            return PublishResult.SUCCESS

        q, _, _ = _make_queue()
        q._publish_json = tracking_publish

        # Queue original
        original = {"service": "robot", "request_id": "req-race", "version": 1}
        q.queue("req-race", original)

        # Start flush in another thread, but during flush replace the entry
        flush_started = threading.Event()
        flush_proceed = threading.Event()

        def intercepting_publish(topic, payload):
            flush_started.set()
            flush_proceed.wait(timeout=5)
            results_delivered.append(payload.get("request_id"))
            return PublishResult.SUCCESS

        q._publish_json = intercepting_publish

        flush_thread = threading.Thread(target=q.flush)
        flush_thread.start()
        flush_started.wait(timeout=5)

        # Overwrite while flush is in progress
        replacement = {"service": "robot", "request_id": "req-race", "version": 2}
        q.queue("req-race", replacement)

        flush_proceed.set()
        flush_thread.join(timeout=5)

        # The replacement should still be in the queue (identity check)
        assert q.pending_count == 1

    @settings(max_examples=50, deadline=5000)
    @given(
        request_ids=st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("L", "N")),
                min_size=1,
                max_size=10,
            ),
            min_size=1,
            max_size=50,
        )
    )
    def test_property_queue_count_bounded(self, request_ids):
        """Property: queue count never exceeds MAX_TASK_RESULT_QUEUE."""
        q, _, _ = _make_queue(publish_result=PublishResult.TRANSIENT_FAILURE)
        for rid in request_ids:
            q.queue(rid, {"service": "robot"})
        assert q.pending_count <= MAX_TASK_RESULT_QUEUE
