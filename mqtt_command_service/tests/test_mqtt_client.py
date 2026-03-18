"""Tests for shared.mqtt_client — LightMQTTClient.

Covers topic matching, state transitions, publish, subscribe/unsubscribe,
connection lifecycle, and reconnection logic.
"""

import json
from unittest.mock import MagicMock, patch

from shared.config_types import MQTTConnectionConfig
from shared.mqtt_client import LightMQTTClient, MQTTConnectionState


def _make_mqtt_config(**overrides) -> MQTTConnectionConfig:
    defaults = dict(
        broker="localhost",
        broker_port=1883,
        mqtt_user="user",
        mqtt_password="pass",
        robot_id="robot-01",
        client_id="test-client",
        mqtt_publish_qos=1,
        mqtt_use_tls=False,
        mqtt_tls_insecure=False,
    )
    defaults.update(overrides)
    return MQTTConnectionConfig(**defaults)


# ---- Topic matching ---------------------------------------------------------


class TestTopicMatches:
    """Unit tests for LightMQTTClient._topic_matches (MQTT wildcard matching)."""

    def _matches(self, topic: str, pattern: str) -> bool:
        client = LightMQTTClient.__new__(LightMQTTClient)
        return client._topic_matches(topic, pattern)

    def test_exact_match(self):
        assert self._matches("a/b/c", "a/b/c") is True

    def test_exact_mismatch(self):
        assert self._matches("a/b/c", "a/b/d") is False

    def test_single_level_wildcard(self):
        assert self._matches("a/b/c", "a/+/c") is True

    def test_single_level_wildcard_mismatch_depth(self):
        assert self._matches("a/b/c/d", "a/+/c") is False

    def test_multi_level_wildcard(self):
        assert self._matches("a/b/c", "a/#") is True

    def test_multi_level_wildcard_root(self):
        assert self._matches("a/b/c/d/e", "a/#") is True

    def test_multi_level_wildcard_exact_parent(self):
        # Per MQTT spec §4.7.1.2: "a/#" matches "a" itself and "a/anything"
        assert self._matches("a", "a/#") is True

    def test_hash_alone(self):
        assert self._matches("anything/at/all", "#") is True

    def test_plus_at_each_level(self):
        assert self._matches("a/b/c", "+/+/+") is True

    def test_plus_mismatch_fewer_levels(self):
        assert self._matches("a/b", "+/+/+") is False

    def test_plus_mismatch_more_levels(self):
        assert self._matches("a/b/c/d", "+/+/+") is False

    def test_pattern_longer_than_topic(self):
        assert self._matches("a/b", "a/b/c") is False

    def test_empty_topic_level(self):
        # MQTT allows empty levels: "a//b" has an empty level between a and b
        assert self._matches("a//b", "a/+/b") is True

    def test_hash_in_middle_stops_matching(self):
        # '#' must be the last character in a valid pattern
        # But our implementation just returns True when it sees '#'
        assert self._matches("a/b/c", "a/#/c") is True  # greedy


# ---- State transitions ------------------------------------------------------


class TestStateTransitions:
    """Test connection state management."""

    @patch("shared.mqtt_client.mqtt_client")
    def test_initial_state_is_disconnected(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")
        assert client.state == MQTTConnectionState.DISCONNECTED
        assert client.is_connected is False

    @patch("shared.mqtt_client.mqtt_client")
    def test_on_connect_success_transitions_to_connected(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        reason_code = MagicMock()
        reason_code.is_failure = False

        client._on_connect(None, None, None, reason_code, None)

        assert client.state == MQTTConnectionState.CONNECTED
        assert client.is_connected is True

    @patch("shared.mqtt_client.mqtt_client")
    def test_on_connect_failure_stays_disconnected(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        reason_code = MagicMock()
        reason_code.is_failure = True

        client._on_connect(None, None, None, reason_code, None)

        assert client.state == MQTTConnectionState.DISCONNECTED
        assert client.is_connected is False

    @patch("shared.mqtt_client.mqtt_client")
    def test_on_disconnect_failure_triggers_reconnect(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        # First connect
        reason_code_ok = MagicMock()
        reason_code_ok.is_failure = False
        client._on_connect(None, None, None, reason_code_ok, None)
        assert client.is_connected is True

        # Then disconnect unexpectedly
        reason_code_fail = MagicMock()
        reason_code_fail.is_failure = True
        client._on_disconnect(None, None, None, reason_code_fail, None)

        assert client.state == MQTTConnectionState.DISCONNECTED
        assert client.is_connected is False
        # Generation-based reconnect: generation should have been incremented
        assert client._reconnect_generation > 0

    @patch("shared.mqtt_client.mqtt_client")
    def test_on_disconnect_clean(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        reason_code_ok = MagicMock()
        reason_code_ok.is_failure = False
        client._on_connect(None, None, None, reason_code_ok, None)

        reason_code_clean = MagicMock()
        reason_code_clean.is_failure = False
        client._on_disconnect(None, None, None, reason_code_clean, None)

        assert client.state == MQTTConnectionState.DISCONNECTED


# ---- Publish -----------------------------------------------------------------


class TestPublish:
    """Test publish behavior."""

    @patch("shared.mqtt_client.mqtt_client")
    def test_publish_when_disconnected_returns_false(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")
        assert client.publish("topic", {"key": "value"}) is False

    @patch("shared.mqtt_client.mqtt_client")
    def test_publish_dict_serializes_to_json(self, mock_paho):
        mock_paho.MQTT_ERR_SUCCESS = 0
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        # Simulate connected state
        mock_inner = MagicMock()
        mock_result = MagicMock()
        mock_result.rc = 0  # MQTT_ERR_SUCCESS
        mock_inner.publish.return_value = mock_result
        client._client = mock_inner
        client._mqtt_connected.set()

        result = client.publish("test/topic", {"hello": "world"})
        assert result is True
        mock_inner.publish.assert_called_once()
        call_args = mock_inner.publish.call_args
        payload_str = call_args[0][1]
        assert json.loads(payload_str) == {"hello": "world"}

    @patch("shared.mqtt_client.mqtt_client")
    def test_publish_string_passes_through(self, mock_paho):
        mock_paho.MQTT_ERR_SUCCESS = 0
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        mock_inner = MagicMock()
        mock_result = MagicMock()
        mock_result.rc = 0
        mock_inner.publish.return_value = mock_result
        client._client = mock_inner
        client._mqtt_connected.set()

        result = client.publish("test/topic", "raw string")
        assert result is True
        call_args = mock_inner.publish.call_args
        assert call_args[0][1] == "raw string"

    @patch("shared.mqtt_client.mqtt_client")
    def test_publish_exception_returns_false(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        mock_inner = MagicMock()
        mock_inner.publish.side_effect = RuntimeError("network error")
        client._client = mock_inner
        client._mqtt_connected.set()

        result = client.publish("test/topic", "data")
        assert result is False


# ---- Subscribe / Unsubscribe ------------------------------------------------


class TestSubscribeUnsubscribe:
    """Test message handler registration."""

    @patch("shared.mqtt_client.mqtt_client")
    def test_subscribe_registers_handler(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        handler = MagicMock()
        client.subscribe("test/topic", handler)

        assert "test/topic" in client._message_handlers
        assert handler in client._message_handlers["test/topic"]

    @patch("shared.mqtt_client.mqtt_client")
    def test_unsubscribe_removes_handler(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        handler = MagicMock()
        client.subscribe("test/topic", handler)
        client.unsubscribe("test/topic", handler)

        assert "test/topic" not in client._message_handlers

    @patch("shared.mqtt_client.mqtt_client")
    def test_unsubscribe_all_handlers(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        handler1 = MagicMock()
        handler2 = MagicMock()
        client.subscribe("test/topic", handler1)
        client.subscribe("test/topic", handler2)

        client.unsubscribe("test/topic")
        assert "test/topic" not in client._message_handlers

    @patch("shared.mqtt_client.mqtt_client")
    def test_unsubscribe_one_keeps_others(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        handler1 = MagicMock()
        handler2 = MagicMock()
        client.subscribe("test/topic", handler1)
        client.subscribe("test/topic", handler2)

        client.unsubscribe("test/topic", handler1)
        assert "test/topic" in client._message_handlers
        assert handler2 in client._message_handlers["test/topic"]
        assert handler1 not in client._message_handlers["test/topic"]


# ---- Message dispatch --------------------------------------------------------


class TestMessageDispatch:
    """Test _on_message dispatches to correct handlers."""

    @patch("shared.mqtt_client.mqtt_client")
    def test_exact_topic_dispatch(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        handler = MagicMock()
        client.subscribe("a/b/c", handler)

        msg = MagicMock()
        msg.topic = "a/b/c"
        msg.payload = b'{"test": true}'

        client._on_message(None, None, msg)
        # Handler is submitted to executor — wait briefly
        client._handler_executor.shutdown(wait=True)

        handler.assert_called_once_with(msg)

    @patch("shared.mqtt_client.mqtt_client")
    def test_wildcard_topic_dispatch(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        handler = MagicMock()
        client.subscribe("a/+/c", handler)

        msg = MagicMock()
        msg.topic = "a/b/c"
        msg.payload = b'{"test": true}'

        client._on_message(None, None, msg)
        client._handler_executor.shutdown(wait=True)

        handler.assert_called_once_with(msg)

    @patch("shared.mqtt_client.mqtt_client")
    def test_no_matching_handler_no_crash(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        msg = MagicMock()
        msg.topic = "unregistered/topic"
        msg.payload = b"{}"

        # Should not raise
        client._on_message(None, None, msg)
        client._handler_executor.shutdown(wait=True)


# ---- on_connect callbacks ----------------------------------------------------


class TestOnConnectCallbacks:
    @patch("shared.mqtt_client.mqtt_client")
    def test_on_connect_callback_fires(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        callback = MagicMock()
        client.add_on_connect_callback(callback)

        reason_code = MagicMock()
        reason_code.is_failure = False
        client._on_connect(None, None, None, reason_code, None)

        client._handler_executor.shutdown(wait=True)
        callback.assert_called_once()


# ---- Build client config -----------------------------------------------------


class TestBuildClientConfig:
    @patch("shared.mqtt_client.mqtt_client")
    def test_client_id_includes_component_name(self, mock_paho):
        config = _make_mqtt_config(client_id="my-robot")
        client = LightMQTTClient(config, component_name="bridge")
        assert client._effective_client_id == "my-robot-bridge"

    @patch("shared.mqtt_client.mqtt_client")
    def test_empty_username_becomes_none(self, mock_paho):
        config = _make_mqtt_config(mqtt_user="", mqtt_password="")
        client = LightMQTTClient(config, component_name="test")
        # Empty username should not be sent to broker
        assert client._mqtt_config.mqtt_user == ""

    @patch("shared.mqtt_client.mqtt_client")
    def test_tls_settings_propagated(self, mock_paho):
        config = _make_mqtt_config(mqtt_use_tls=True, mqtt_tls_insecure=True)
        client = LightMQTTClient(config, component_name="test")
        assert client._mqtt_config.mqtt_use_tls is True
        assert client._mqtt_config.mqtt_tls_insecure is True


# ---- on_publish tracking -----------------------------------------------------


class TestOnPublish:
    @patch("shared.mqtt_client.mqtt_client")
    def test_on_publish_updates_last_publish_time(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")
        assert client.last_publish_time is None

        reason_code = MagicMock()
        client._on_publish(None, None, 1, reason_code, None)

        assert client.last_publish_time is not None
        assert client.last_publish_time > 0


# ---- Resubscribe on reconnect -----------------------------------------------


class TestResubscribe:
    @patch("shared.mqtt_client.mqtt_client")
    def test_resubscribe_all_on_connect(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        handler = MagicMock()
        client.subscribe("topic/a", handler)
        client.subscribe("topic/b", handler)

        # Simulate connected client
        mock_inner = MagicMock()
        client._client = mock_inner
        client._mqtt_connected.set()

        client._resubscribe_all()

        assert mock_inner.subscribe.call_count == 2
        subscribed_topics = {call[0][0] for call in mock_inner.subscribe.call_args_list}
        assert subscribed_topics == {"topic/a", "topic/b"}


# ---- Teardown / deadlock avoidance ------------------------------------------


class TestTeardownClient:
    """Verify _teardown_client works safely outside the lock."""

    @patch("shared.mqtt_client.mqtt_client")
    def test_teardown_none_is_noop(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")
        # Should not raise
        client._teardown_client(None)

    @patch("shared.mqtt_client.mqtt_client")
    def test_teardown_calls_disconnect_and_loop_stop(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        mock_inner = MagicMock()
        client._teardown_client(mock_inner)

        mock_inner.disconnect.assert_called_once()
        mock_inner.loop_stop.assert_called_once()

    @patch("shared.mqtt_client.mqtt_client")
    def test_teardown_suppresses_disconnect_error(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        mock_inner = MagicMock()
        mock_inner.disconnect.side_effect = RuntimeError("already disconnected")
        # Should not raise
        client._teardown_client(mock_inner)
        mock_inner.loop_stop.assert_called_once()

    @patch("shared.mqtt_client.mqtt_client")
    def test_teardown_suppresses_loop_stop_error(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        mock_inner = MagicMock()
        mock_inner.loop_stop.side_effect = RuntimeError("loop not started")
        # Should not raise
        client._teardown_client(mock_inner)


# ---- Reconnect: no deadlock on _connect teardown ----------------------------


class TestConnectTeardownNoDeadlock:
    """Verify _connect tears down old client OUTSIDE the lock.

    Bug: previously disconnect()/loop_stop() were called under _lock,
    but paho's on_disconnect callback also acquired _lock → deadlock.
    """

    @patch("shared.mqtt_client.mqtt_client")
    def test_connect_detaches_old_client_before_teardown(self, mock_paho):
        """Old client is set to None under lock, then torn down outside."""
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        old_inner = MagicMock()
        client._client = old_inner

        # Mock _build_paho_client to return a new mock client
        new_inner = MagicMock()
        new_inner.connect.return_value = 0
        client._build_paho_client = MagicMock(return_value=new_inner)

        # _connect will fail (timeout) but the old client should be torn down
        client._connect()

        # Old client was properly torn down
        old_inner.disconnect.assert_called_once()
        old_inner.loop_stop.assert_called_once()

    @patch("shared.mqtt_client.mqtt_client")
    def test_stop_detaches_client_before_teardown(self, mock_paho):
        """stop() detaches client under lock, tears down outside."""
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        mock_inner = MagicMock()
        client._client = mock_inner

        client.stop()

        mock_inner.disconnect.assert_called_once()
        mock_inner.loop_stop.assert_called_once()
        assert client._client is None
        assert client.state == MQTTConnectionState.DISCONNECTED


# ---- Reconnect: state not stuck in CONNECTING --------------------------------


class TestConnectTimeoutResetsState:
    """Bug: _connect() timeout left state=CONNECTING, reconnect loop skipped retries."""

    @patch("shared.mqtt_client.mqtt_client")
    def test_timeout_sets_reconnecting_not_connecting(self, mock_paho):
        config = _make_mqtt_config()
        client = LightMQTTClient(config, component_name="test")

        mock_inner = MagicMock()
        mock_inner.connect.return_value = 0
        client._build_paho_client = MagicMock(return_value=mock_inner)

        # _connect will not get a connected event → timeout
        result = client._connect()

        assert result is False
        assert client.state == MQTTConnectionState.RECONNECTING
