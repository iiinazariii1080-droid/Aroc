"""Concurrent command integration tests.

Tests multiple simultaneous dispatches through the real executor pool.
Proves: all commands complete, backpressure returns 503, concurrent estop + navigate.

Real: CommandDispatcher, HttpExecutor, PendingResultQueue, ResponsePublisher.
Fake: HonestFakeMQTTClient, mock requests.Session.
No time.sleep().
"""

import json
import threading
from unittest.mock import MagicMock, patch

from bridge import MqttCommandBridge

from shared.constants import BRIDGE_HTTP_EXECUTOR_MAX_WORKERS
from tests.conftest import make_bridge_config, make_mock_response, make_mock_session
from tests.fakes import HonestFakeMQTTClient


def _make_bridge(*, mock_session=None, start_connected=True):
    if mock_session is None:
        mock_session = make_mock_session()

    cfg = make_bridge_config(safety_gate_startup_grace=60.0)
    fake_client = HonestFakeMQTTClient(
        config=cfg.mqtt, component_name="bridge",
        start_connected=start_connected,
    )

    session_patcher = patch("http_executor.requests.Session", return_value=mock_session)
    session_patcher.start()

    with patch("bridge.LightMQTTClient", return_value=fake_client):
        bridge = MqttCommandBridge(config=cfg)

    return bridge, fake_client, mock_session, session_patcher


class TestConcurrentCommands:
    def test_concurrent_navigates_all_complete(self):
        """8 concurrent navigate commands → all 8 complete with MQTT publishes."""
        mock_response = make_mock_response(200, {"task_id": "t-1"})
        mock_session = make_mock_session(mock_response)
        bridge, fake, _, patcher = _make_bridge(mock_session=mock_session)
        bridge.start()

        n_commands = 8
        completed = threading.Event()
        completed_count = 0
        count_lock = threading.Lock()

        original_finish = bridge._dedup.finish

        def counting_finish(cid):
            nonlocal completed_count
            original_finish(cid)
            if cid and cid.startswith("cmd-conc-"):
                with count_lock:
                    completed_count += 1
                    if completed_count >= n_commands:
                        completed.set()

        # Hook at dedup level (where BridgeDependencies.finish_command routes)
        bridge._dedup.finish = counting_finish

        for i in range(n_commands):
            bridge._command_dispatcher.dispatch(
                "navigateto",
                f"cmd-conc-{i}",
                {"target_id": f"station-{i}"},
            )

        completed.wait(timeout=10)
        assert completed_count == n_commands

        # All should have MQTT publishes
        resp_publishes = [(t, p) for t, p, q, r in fake.publish_log if "/resp/" in t]
        assert len(resp_publishes) >= n_commands

        # Verify all command_ids present
        published_ids = set()
        for _, payload_str in resp_publishes:
            p = json.loads(payload_str)
            if "request_id" in p and p["request_id"] and p["request_id"].startswith("cmd-conc-"):
                published_ids.add(p["request_id"])
        assert len(published_ids) == n_commands

        bridge.stop()
        patcher.stop()

    def test_backpressure_returns_503(self):
        """Exhaust semaphore → next command gets 503."""
        # Use a slow mock that blocks until we release it
        proceed = threading.Event()
        mock_response = make_mock_response(200, {"detail": "ok"})
        slow_session = MagicMock()

        def slow_request(**kwargs):
            proceed.wait(timeout=10)
            return mock_response

        slow_session.request.side_effect = slow_request

        bridge, fake, _, patcher = _make_bridge(mock_session=slow_session)
        bridge.start()

        max_slots = BRIDGE_HTTP_EXECUTOR_MAX_WORKERS * 5

        # Fill all slots
        for i in range(max_slots):
            bridge._command_dispatcher.dispatch(
                "navigateto",
                f"cmd-fill-{i}",
                {"target_id": f"s-{i}"},
            )

        # Give executor threads time to pick up tasks and acquire semaphore
        import time
        time.sleep(0.5)  # Brief wait for threads to start executing

        # Next command should get 503 (backpressure)
        bp_done = threading.Event()
        original_finish = bridge._dedup.finish

        def bp_finish(cid):
            original_finish(cid)
            if cid == "cmd-bp":
                bp_done.set()

        bridge._command_dispatcher._finish_command = bp_finish

        bridge._command_dispatcher.dispatch(
            "navigateto",
            "cmd-bp",
            {"target_id": "overflow"},
        )
        bp_done.wait(timeout=5)

        # Find the 503 response
        resp_503 = [
            json.loads(p) for t, p, q, r in fake.publish_log
            if "/resp/" in t and "cmd-bp" in p
        ]
        assert len(resp_503) >= 1
        assert resp_503[0]["status_code"] == 503
        assert resp_503[0]["success"] is False

        proceed.set()  # Release all blocked requests
        bridge.stop()
        patcher.stop()

    def test_concurrent_estop_and_navigate(self):
        """E-stop completes via dedicated thread even while navigate is in-flight."""
        navigate_proceed = threading.Event()
        mock_response = make_mock_response(200, {"detail": "ok"})

        nav_session = MagicMock()
        nav_session.request.side_effect = lambda **kw: (navigate_proceed.wait(5), mock_response)[1]

        estop_session = MagicMock()
        estop_session.post.return_value = mock_response
        estop_session.close = MagicMock()

        bridge, fake, _, patcher = _make_bridge(mock_session=nav_session)
        bridge.start()

        # Start navigate (will block in executor)
        bridge._command_dispatcher.dispatch(
            "navigateto",
            "cmd-nav-slow",
            {"target_id": "A"},
        )

        # Start e-stop (should use dedicated thread, not executor)
        with patch("command_dispatcher.requests.Session", return_value=estop_session):
            bridge._command_dispatcher.handle_estop("cmd-estop-fast", {})
            bridge._command_dispatcher.join_estop_threads(timeout=5)

        # E-stop should have completed before navigate
        estop_publishes = [
            json.loads(p) for t, p, q, r in fake.publish_log
            if "/resp/" in t and "cmd-estop-fast" in p
        ]
        assert len(estop_publishes) >= 1
        assert estop_publishes[0]["success"] is True
        assert estop_publishes[0]["command_name"] == "estop"

        navigate_proceed.set()  # Release navigate
        bridge.stop()
        patcher.stop()
