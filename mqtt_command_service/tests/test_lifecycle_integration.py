"""Lifecycle & reconnection integration tests.

Tests the bridge across MQTT state transitions using HonestFakeMQTTClient.
Proves: subscribe on start, reconnect fires on_connect → flush, publish-failure queuing.

Real: MqttCommandBridge, CommandDispatcher, HttpExecutor, PendingResultQueue,
      ResponsePublisher, CommandDeduplicator, SafetyGate.
Fake: HonestFakeMQTTClient (replaces paho), mock requests.Session (replaces HTTP).
No time.sleep().
"""

import json
import threading
from unittest.mock import MagicMock, patch

from bridge import MqttCommandBridge

from tests.conftest import make_bridge_config, make_mock_response, make_mock_session
from tests.fakes import HonestFakeMQTTClient


def _make_bridge(
    *,
    mock_session: MagicMock | None = None,
    start_connected: bool = True,
    publish_succeeds: bool = True,
):
    """Create a real MqttCommandBridge with HonestFakeMQTTClient.

    Returns (bridge, fake_client, mock_session, session_patcher).
    Caller MUST call session_patcher.stop() after bridge.stop().
    """
    if mock_session is None:
        mock_session = make_mock_session()

    cfg = make_bridge_config(safety_gate_startup_grace=60.0)
    fake_client = HonestFakeMQTTClient(
        config=cfg.mqtt,
        component_name="bridge",
        start_connected=start_connected,
        publish_succeeds=publish_succeeds,
    )

    # Keep session patch active during test execution (worker threads need it)
    session_patcher = patch("http_executor.requests.Session", return_value=mock_session)
    session_patcher.start()

    with patch("bridge.LightMQTTClient", return_value=fake_client):
        bridge = MqttCommandBridge(config=cfg)

    return bridge, fake_client, mock_session, session_patcher


def _cleanup(bridge, patcher):
    bridge.stop()
    patcher.stop()


class TestBridgeStartSubscription:
    def test_start_subscribes_command_topics(self):
        """Bridge.start() subscribes to command and safety topics."""
        bridge, fake, _, patcher = _make_bridge()
        bridge.start()

        subscribed = set(fake.subscribe_log)
        robot_id = bridge.config.robot_id
        assert f"aroc/robot/{robot_id}/commands/+" in subscribed
        assert f"aroc/robot/{robot_id}/status/safety" in subscribed

        _cleanup(bridge, patcher)

    def test_start_registers_flush_callback(self):
        """Bridge.start() registers pending_queue.flush as on_connect callback."""
        bridge, fake, _, patcher = _make_bridge()
        bridge.start()

        assert len(fake._on_connect_callbacks) >= 1

        _cleanup(bridge, patcher)


class TestDisconnectReconnectFlush:
    def test_disconnect_queues_reconnect_flushes_automatically(self):
        """Disconnect → response queued → reconnect → on_connect fires flush → published."""
        mock_response = make_mock_response(200, {"detail": "ok"})
        mock_session = make_mock_session(mock_response)
        bridge, fake, _, patcher = _make_bridge(mock_session=mock_session)
        bridge.start()

        fake.simulate_disconnect()
        assert not fake.is_connected

        done = threading.Event()
        original_finish = bridge._dedup.finish

        def finish_and_signal(cid):
            original_finish(cid)
            if cid:
                done.set()

        bridge._command_dispatcher._finish_command = finish_and_signal

        bridge._command_dispatcher.dispatch(
            "navigateto",
            "cmd-recon-1",
            {"target_id": "station-A"},
        )
        done.wait(timeout=5)

        # Nothing published to MQTT (disconnected), but result should be queued
        resp_before = [(t, p) for t, p, q, r in fake.publish_log if "/resp/" in t]
        assert len(resp_before) == 0
        assert bridge._pending_queue.has_pending

        # Simulate reconnect — fires on_connect callbacks including flush
        fake.simulate_reconnect()

        resp_after = [(t, p) for t, p, q, r in fake.publish_log if "/resp/" in t]
        assert len(resp_after) >= 1

        payload = json.loads(resp_after[0][1])
        assert payload["request_id"] == "cmd-recon-1"
        assert payload["success"] is True

        _cleanup(bridge, patcher)

    def test_resubscription_on_reconnect(self):
        """Reconnect triggers resubscription of all topics."""
        bridge, fake, _, patcher = _make_bridge()
        bridge.start()

        initial_topics = set(fake.subscribe_log)

        fake.simulate_disconnect()
        fake.simulate_reconnect()

        assert len(fake.resubscribe_log) >= 2  # start + reconnect
        reconnect_topics = set(fake.resubscribe_log[-1])
        for topic in initial_topics:
            assert topic in reconnect_topics, f"Topic {topic} not resubscribed"

        _cleanup(bridge, patcher)


class TestPublishFailureWhileConnected:
    def test_publish_false_while_connected_queues_to_pending(self):
        """When publish returns False while connected, response goes to pending queue."""
        mock_response = make_mock_response(200, {"detail": "ok"})
        mock_session = make_mock_session(mock_response)
        bridge, fake, _, patcher = _make_bridge(
            mock_session=mock_session,
            publish_succeeds=False,
        )
        bridge.start()

        done = threading.Event()
        original_finish = bridge._dedup.finish

        def finish_and_signal(cid):
            original_finish(cid)
            if cid:
                done.set()

        bridge._command_dispatcher._finish_command = finish_and_signal

        bridge._command_dispatcher.dispatch(
            "navigateto",
            "cmd-pubfail-1",
            {"target_id": "station-X"},
        )
        done.wait(timeout=5)

        assert bridge._pending_queue.has_pending

        fake.set_publish_succeeds(True)
        bridge._pending_queue.flush()

        resp_publishes = [(t, p) for t, p, q, r in fake.publish_log if "/resp/" in t]
        assert len(resp_publishes) >= 1

        _cleanup(bridge, patcher)


class TestBridgeStop:
    def test_stop_sets_shutdown(self):
        bridge, fake, _, patcher = _make_bridge()
        bridge.start()
        bridge.stop()
        assert bridge._shutdown.is_set()
        patcher.stop()

    def test_stop_is_idempotent(self):
        bridge, fake, _, patcher = _make_bridge()
        bridge.start()
        bridge.stop()
        bridge.stop()
        patcher.stop()


class TestSafetyGateIntegration:
    def test_safety_lockout_rejects_navigate(self):
        """Inject safety lockout → navigate command rejected."""
        mock_response = make_mock_response(200, {"detail": "ok"})
        mock_session = make_mock_session(mock_response)
        bridge, fake, _, patcher = _make_bridge(mock_session=mock_session)
        # Use zero grace period for immediate safety gate activation
        bridge._safety_gate = __import__("safety_gate").SafetyGate(
            heartbeat_timeout=60.0, startup_grace_seconds=0.0
        )
        bridge.start()

        safety_payload = json.dumps({
            "safety_lockout": True,
            "reason": "collision detected",
        }).encode()
        fake.inject_message(
            f"aroc/robot/{bridge.config.robot_id}/status/safety",
            safety_payload,
        )

        bridge._handle_command_message(
            "navigateto",
            {"command_id": "cmd-locked", "target_id": "A"},
        )

        mock_session.request.assert_not_called()
        resp_publishes = [(t, p) for t, p, q, r in fake.publish_log if "/resp/" in t]
        assert len(resp_publishes) >= 1
        payload = json.loads(resp_publishes[0][1])
        assert payload["success"] is False

        _cleanup(bridge, patcher)
