"""Self-tests for HonestFakeMQTTClient.

Verifies the fake matches LightMQTTClient's behavioral contract:
state machine, subscribe/handler dispatch, on_connect callbacks,
resubscription, publish semantics, topic matching.
"""

from unittest.mock import MagicMock

from tests.fakes import HonestFakeMQTTClient, MQTTConnectionState


class TestStateLifecycle:
    def test_initial_state_disconnected(self):
        client = HonestFakeMQTTClient()
        assert client.state == MQTTConnectionState.DISCONNECTED
        assert client.is_connected is False

    def test_start_transitions_to_connected(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        assert client.state == MQTTConnectionState.CONNECTED
        assert client.is_connected is True

    def test_start_without_connect(self):
        client = HonestFakeMQTTClient(start_connected=False)
        client.start()
        assert client.state == MQTTConnectionState.DISCONNECTED
        assert client.is_connected is False

    def test_stop_transitions_to_disconnected(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        client.stop()
        assert client.state == MQTTConnectionState.DISCONNECTED
        assert client.is_connected is False

    def test_simulate_disconnect(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        client.simulate_disconnect()
        assert client.state == MQTTConnectionState.DISCONNECTED
        assert client.is_connected is False

    def test_simulate_reconnect(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        client.simulate_disconnect()
        client.simulate_reconnect()
        assert client.state == MQTTConnectionState.CONNECTED
        assert client.is_connected is True


class TestPublish:
    def test_publish_when_connected(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        result = client.publish("test/topic", "hello", qos=1, retain=False)
        assert result is True
        assert len(client.publish_log) == 1
        assert client.publish_log[0] == ("test/topic", "hello", 1, False)

    def test_publish_when_disconnected_returns_false(self):
        client = HonestFakeMQTTClient(start_connected=False)
        client.start()
        result = client.publish("test/topic", "hello")
        assert result is False
        assert len(client.publish_log) == 0

    def test_publish_fails_when_publish_succeeds_false(self):
        client = HonestFakeMQTTClient(start_connected=True, publish_succeeds=False)
        client.start()
        result = client.publish("test/topic", "hello")
        assert result is False
        assert len(client.publish_log) == 0

    def test_publish_dict_serialized_to_json(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        client.publish("test/topic", {"key": "value", "num": 42})
        _, payload_str, _, _ = client.publish_log[0]
        import json
        parsed = json.loads(payload_str)
        assert parsed == {"key": "value", "num": 42}

    def test_publish_dict_preserves_unicode(self):
        """Matches LightMQTTClient: ensure_ascii=False for dict payloads."""
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        client.publish("test/topic", {"name": "робот"})
        _, payload_str, _, _ = client.publish_log[0]
        assert "робот" in payload_str  # Not escaped to \\u...
        assert "\\u" not in payload_str

    def test_set_publish_succeeds_toggle(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        assert client.publish("t", "a") is True
        client.set_publish_succeeds(False)
        assert client.publish("t", "b") is False
        client.set_publish_succeeds(True)
        assert client.publish("t", "c") is True
        assert len(client.publish_log) == 2  # only 'a' and 'c'


class TestSubscribeAndHandlers:
    def test_subscribe_registers_handler(self):
        client = HonestFakeMQTTClient()
        handler = MagicMock()
        client.subscribe("test/topic", handler)
        assert "test/topic" in client.subscribe_log

    def test_inject_message_dispatches_to_handler(self):
        client = HonestFakeMQTTClient()
        handler = MagicMock()
        client.subscribe("test/topic", handler)
        client.inject_message("test/topic", b'{"data": 1}')
        handler.assert_called_once()
        msg = handler.call_args[0][0]
        assert msg.topic == "test/topic"
        assert msg.payload == b'{"data": 1}'

    def test_inject_message_wildcard_plus(self):
        client = HonestFakeMQTTClient()
        handler = MagicMock()
        client.subscribe("aroc/robot/+/commands/+", handler)
        client.inject_message("aroc/robot/bot-1/commands/navigateTo", b'{}')
        handler.assert_called_once()

    def test_inject_message_wildcard_hash(self):
        client = HonestFakeMQTTClient()
        handler = MagicMock()
        client.subscribe("aroc/#", handler)
        client.inject_message("aroc/robot/bot-1/commands/navigateTo", b'{}')
        handler.assert_called_once()

    def test_inject_message_no_match(self):
        client = HonestFakeMQTTClient()
        handler = MagicMock()
        client.subscribe("other/topic", handler)
        client.inject_message("aroc/robot/bot-1/commands/navigateTo", b'{}')
        handler.assert_not_called()

    def test_unsubscribe_removes_handler(self):
        client = HonestFakeMQTTClient()
        handler = MagicMock()
        client.subscribe("test/topic", handler)
        client.unsubscribe("test/topic", handler)
        client.inject_message("test/topic", b'{}')
        handler.assert_not_called()

    def test_unsubscribe_all_handlers(self):
        client = HonestFakeMQTTClient()
        h1 = MagicMock()
        h2 = MagicMock()
        client.subscribe("test/topic", h1)
        client.subscribe("test/topic", h2)
        client.unsubscribe("test/topic")  # remove all
        client.inject_message("test/topic", b'{}')
        h1.assert_not_called()
        h2.assert_not_called()


class TestOnConnectCallbacks:
    def test_on_connect_callback_fires_on_start(self):
        client = HonestFakeMQTTClient(start_connected=True)
        cb = MagicMock()
        client.add_on_connect_callback(cb)
        client.start()
        cb.assert_called_once()

    def test_on_connect_callback_fires_on_reconnect(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        cb = MagicMock()
        client.add_on_connect_callback(cb)
        client.simulate_disconnect()
        client.simulate_reconnect()
        cb.assert_called_once()

    def test_multiple_callbacks_all_fire(self):
        client = HonestFakeMQTTClient(start_connected=True)
        cb1 = MagicMock()
        cb2 = MagicMock()
        client.add_on_connect_callback(cb1)
        client.add_on_connect_callback(cb2)
        client.start()
        cb1.assert_called_once()
        cb2.assert_called_once()


class TestResubscription:
    def test_resubscribe_on_start(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.subscribe("topic/a", MagicMock())
        client.subscribe("topic/b", MagicMock())
        client.start()
        assert len(client.resubscribe_log) == 1
        assert set(client.resubscribe_log[0]) == {"topic/a", "topic/b"}

    def test_resubscribe_on_reconnect(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.subscribe("topic/a", MagicMock())
        client.start()
        client.simulate_disconnect()
        client.simulate_reconnect()
        # resubscribe_log has entries for both start and reconnect
        assert len(client.resubscribe_log) == 2
        assert "topic/a" in client.resubscribe_log[1]

    def test_no_resubscribe_when_no_handlers(self):
        client = HonestFakeMQTTClient(start_connected=True)
        client.start()
        assert len(client.resubscribe_log) == 0


class TestTopicMatching:
    def test_exact_match(self):
        assert HonestFakeMQTTClient._topic_matches("a/b/c", "a/b/c") is True

    def test_plus_wildcard(self):
        assert HonestFakeMQTTClient._topic_matches("a/b/c", "a/+/c") is True
        assert HonestFakeMQTTClient._topic_matches("a/x/c", "a/+/c") is True

    def test_hash_wildcard(self):
        assert HonestFakeMQTTClient._topic_matches("a/b/c/d", "a/#") is True

    def test_no_match(self):
        assert HonestFakeMQTTClient._topic_matches("a/b/c", "a/b/d") is False

    def test_different_length_no_match(self):
        assert HonestFakeMQTTClient._topic_matches("a/b", "a/b/c") is False
        assert HonestFakeMQTTClient._topic_matches("a/b/c", "a/b") is False
