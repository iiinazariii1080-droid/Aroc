import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.state_store import StateStore


def test_enqueue_persistence_counters() -> None:
    store = StateStore()

    with patch("services.state_store.reliability_metrics") as metrics:
        store._persistence_queue = None
        store._enqueue_persistence({"type": "upsert"})
        metrics.inc.assert_any_call("persistence.enqueue.skipped_no_queue")

        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait({"type": "seed"})
        store._persistence_queue = queue
        store._enqueue_persistence({"type": "upsert"})
        metrics.inc.assert_any_call("persistence.enqueue.drop_queue_full")

        queue = asyncio.Queue(maxsize=2)
        store._persistence_queue = queue
        store._enqueue_persistence({"type": "upsert"})
        metrics.inc.assert_any_call("persistence.enqueue.ok")


@pytest.mark.asyncio
async def test_persistence_writer_success_metrics() -> None:
    store = StateStore()
    store._persistence = MagicMock()
    store._persistence.upsert = AsyncMock()
    store._persistence_queue = asyncio.Queue()
    store._persistence_running = True
    await store._persistence_queue.put({"type": "upsert", "data": {"k": "v"}})

    with patch("services.state_store.reliability_metrics") as metrics:
        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.sleep(0.05)
        store._persistence_running = False
        await asyncio.wait_for(task, timeout=1.0)

        metrics.inc.assert_any_call("persistence.writer.dequeue")
        metrics.inc.assert_any_call("persistence.writer.upsert.ok")
        metrics.observe_duration.assert_called()


@pytest.mark.asyncio
async def test_persistence_writer_failure_metric() -> None:
    store = StateStore()
    store._persistence = MagicMock()
    store._persistence.upsert = AsyncMock(side_effect=Exception("persist-error"))
    store._persistence_queue = asyncio.Queue()
    store._persistence_running = True
    await store._persistence_queue.put({"type": "upsert", "data": {"k": "v"}})

    with patch("services.state_store.reliability_metrics") as metrics:
        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.sleep(0.05)
        store._persistence_running = False
        await asyncio.wait_for(task, timeout=1.0)

        metrics.inc.assert_any_call("persistence.writer.failed")
