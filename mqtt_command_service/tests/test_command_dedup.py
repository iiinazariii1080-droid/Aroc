"""Tests for CommandDeduplicator — in-flight guard and result history."""

import threading
import time

from command_dedup import CommandDeduplicator

# ---------------------------------------------------------------------------
# In-flight guard
# ---------------------------------------------------------------------------

class TestTryStart:
    def test_first_call_returns_true(self):
        d = CommandDeduplicator()
        assert d.try_start("cmd-1") is True

    def test_duplicate_returns_false(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        assert d.try_start("cmd-1") is False

    def test_different_ids_both_succeed(self):
        d = CommandDeduplicator()
        assert d.try_start("cmd-1") is True
        assert d.try_start("cmd-2") is True

    def test_empty_id_always_returns_true(self):
        d = CommandDeduplicator()
        assert d.try_start("") is True
        assert d.try_start("") is True


class TestFinish:
    def test_releases_lock(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        d.finish("cmd-1")
        # Should be able to start again
        assert d.try_start("cmd-1") is True

    def test_finish_none_is_noop(self):
        d = CommandDeduplicator()
        d.finish(None)  # Should not raise

    def test_finish_unknown_id_is_noop(self):
        d = CommandDeduplicator()
        d.finish("unknown")  # Should not raise


class TestInFlightCount:
    def test_zero_initially(self):
        d = CommandDeduplicator()
        assert d.in_flight_count == 0

    def test_increments_on_start(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        d.try_start("cmd-2")
        assert d.in_flight_count == 2

    def test_decrements_on_finish(self):
        d = CommandDeduplicator()
        d.try_start("cmd-1")
        d.finish("cmd-1")
        assert d.in_flight_count == 0


# ---------------------------------------------------------------------------
# Result history
# ---------------------------------------------------------------------------

class TestStore:
    def test_stores_and_retrieves(self):
        d = CommandDeduplicator()
        d.store("cmd-1", {"success": True, "data": 42})
        result = d.get("cmd-1")
        assert result == {"success": True, "data": 42}

    def test_deep_copies_payload(self):
        d = CommandDeduplicator()
        original = {"nested": {"value": 1}}
        d.store("cmd-1", original)
        # Mutate original — should not affect stored copy
        original["nested"]["value"] = 999
        result = d.get("cmd-1")
        assert result["nested"]["value"] == 1

    def test_get_returns_deep_copy(self):
        d = CommandDeduplicator()
        d.store("cmd-1", {"nested": {"value": 1}})
        r1 = d.get("cmd-1")
        r2 = d.get("cmd-1")
        assert r1 is not r2
        assert r1 == r2

    def test_empty_id_is_noop(self):
        d = CommandDeduplicator()
        d.store("", {"data": 1})
        assert d.get("") is None


class TestHistoryExpiry:
    def test_expired_entry_returns_none(self):
        d = CommandDeduplicator(history_ttl=0.1)
        d.store("cmd-1", {"data": 1})
        time.sleep(0.15)
        assert d.get("cmd-1") is None

    def test_non_expired_entry_returns_value(self):
        d = CommandDeduplicator(history_ttl=10.0)
        d.store("cmd-1", {"data": 1})
        assert d.get("cmd-1") == {"data": 1}


class TestHistorySizeCap:
    def test_evicts_oldest_when_over_cap(self):
        d = CommandDeduplicator(max_history=3)
        for i in range(5):
            d.store(f"cmd-{i}", {"i": i})
            time.sleep(0.01)  # ensure distinct timestamps
        # Oldest 2 should be evicted
        assert d.get("cmd-0") is None
        assert d.get("cmd-1") is None
        # Newest 3 should remain
        assert d.get("cmd-2") is not None
        assert d.get("cmd-3") is not None
        assert d.get("cmd-4") is not None


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_try_start(self):
        """Only one of N threads should win try_start for the same command_id."""
        d = CommandDeduplicator()
        results = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            results.append(d.try_start("cmd-race"))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 9
