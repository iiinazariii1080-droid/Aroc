"""Tests for CommandDeduplicator: in-flight guard and result history."""

import threading
import time

from command_dedup import CommandDeduplicator


class TestTryStart:
    def test_first_call_returns_true(self):
        d = CommandDeduplicator()
        assert d.try_start("cmd-1") is True

    def test_duplicate_call_returns_false(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        assert d.try_start("cmd-1") is False

    def test_empty_command_id_always_returns_true(self):
        d = CommandDeduplicator()
        assert d.try_start("") is True
        assert d.try_start("") is True

    def test_different_ids_are_independent(self):
        d = CommandDeduplicator()
        assert d.try_start("cmd-1") is True
        assert d.try_start("cmd-2") is True


class TestFinish:
    def test_finish_allows_restart(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        d.finish("cmd-1")
        assert d.try_start("cmd-1") is True

    def test_finish_none_is_noop(self):
        d = CommandDeduplicator()
        d.finish(None)  # should not raise

    def test_finish_empty_string_is_noop(self):
        d = CommandDeduplicator()
        d.finish("")  # should not raise

    def test_finish_unknown_id_is_noop(self):
        d = CommandDeduplicator()
        d.finish("never-started")  # should not raise


class TestInFlightCount:
    def test_count_starts_at_zero(self):
        d = CommandDeduplicator()
        assert d.in_flight_count == 0

    def test_count_increments(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        d.try_start("cmd-2")
        assert d.in_flight_count == 2

    def test_count_decrements_on_finish(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        d.try_start("cmd-2")
        d.finish("cmd-1")
        assert d.in_flight_count == 1

    def test_empty_id_not_counted(self):
        d = CommandDeduplicator()
        d.try_start("")
        assert d.in_flight_count == 0


class TestStore:
    def test_store_and_get(self):
        d = CommandDeduplicator()
        payload = {"service": "robot", "success": True}
        d.store("cmd-1", payload)
        result = d.get("cmd-1")
        assert result == payload

    def test_stored_payload_is_deep_copied(self):
        d = CommandDeduplicator()
        payload = {"service": "robot", "nested": {"key": "value"}}
        d.store("cmd-1", payload)
        payload["nested"]["key"] = "mutated"
        result = d.get("cmd-1")
        assert result["nested"]["key"] == "value"

    def test_get_returns_deep_copy(self):
        d = CommandDeduplicator()
        d.store("cmd-1", {"data": [1, 2, 3]})
        result1 = d.get("cmd-1")
        result2 = d.get("cmd-1")
        assert result1 == result2
        assert result1 is not result2

    def test_store_empty_id_is_noop(self):
        d = CommandDeduplicator()
        d.store("", {"data": "value"})
        assert d.get("") is None


class TestGetExpiry:
    def test_expired_entry_returns_none(self):
        d = CommandDeduplicator(history_ttl=0.05)
        d.store("cmd-1", {"data": "value"})
        time.sleep(0.1)
        assert d.get("cmd-1") is None

    def test_non_expired_entry_returns_payload(self):
        d = CommandDeduplicator(history_ttl=10.0)
        d.store("cmd-1", {"data": "value"})
        assert d.get("cmd-1") is not None

    def test_absent_entry_returns_none(self):
        d = CommandDeduplicator()
        assert d.get("nonexistent") is None


class TestMaxHistory:
    def test_excess_entries_evicted(self):
        d = CommandDeduplicator(max_history=3, history_ttl=60.0)
        for i in range(5):
            d.store(f"cmd-{i}", {"index": i})
        # Oldest entries (cmd-0, cmd-1) should be evicted
        assert d.get("cmd-0") is None
        assert d.get("cmd-1") is None
        assert d.get("cmd-2") is not None
        assert d.get("cmd-3") is not None
        assert d.get("cmd-4") is not None


class TestThreadSafety:
    def test_concurrent_try_start(self):
        d = CommandDeduplicator()
        results = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            results.append(d.try_start("shared-cmd"))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread should succeed
        assert results.count(True) == 1
        assert results.count(False) == 9


class TestConcurrentStoreAndGet:
    """Test concurrent store/get operations."""

    def test_concurrent_store_different_ids(self):
        """Multiple threads storing different command IDs concurrently."""
        dedup = CommandDeduplicator(history_ttl=60, max_history=100)
        results = []
        barrier = threading.Barrier(10)

        def worker(i):
            barrier.wait()
            cid = f"cmd-{i}"
            dedup.try_start(cid)
            dedup.store(cid, {"status": i})
            results.append(dedup.get(cid))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 10
        assert all(r is not None for r in results)

    def test_concurrent_try_start_same_id(self):
        """Only one thread wins try_start for the same command_id."""
        dedup = CommandDeduplicator(history_ttl=60, max_history=100)
        winners = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            if dedup.try_start("same-cmd"):
                winners.append(True)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(winners) == 1  # Exactly one winner
