"""Extended tests for MqttAdapter — connect, disconnect, _mark_disconnected, _safe_close_client, command consumer."""
import pytest
import asyncio
import json
import ssl
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from types import SimpleNamespace

from services.mqtt_adapter import MqttAdapter, MqttUnavailableError, AIOMQTT_AVAILABLE


# ─── Helpers ──────────────────────────────────────────────────────────

def _make_adapter(**overrides):
    a = MqttAdapter.__new__(MqttAdapter)
    a.robot_id = overrides.get("robot_id", "robot1")
    a.broker_host = overrides.get("broker_host", "localhost")
    a.broker_port = overrides.get("broker_port", 1883)
    a.username = overrides.get("username", None)
    a._password = overrides.get("password", None)
    a.use_tls = overrides.get("use_tls", False)
    a.client_id = overrides.get("client_id", "test_client")
    a.client = overrides.get("client", None)
    a._connected = overrides.get("connected", False)
    a._command_consumer_task = None
    a._subscribe_tasks = []
    a._connect_timeout_s = 8.0
    a._max_incoming_payload_bytes = 256 * 1024
    a._last_disconnect_log_time = 0.0
    a._disconnect_log_interval = 10.0
    return a


# ─── _safe_close_client ──────────────────────────────────────────────

class TestSafeCloseClient:
    @pytest.mark.asyncio
    async def test_closes_client(self):
        client = MagicMock()
        client.__aexit__ = AsyncMock()
        await MqttAdapter._safe_close_client(client)
        client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_errors(self):
        client = MagicMock()
        client.__aexit__ = AsyncMock(side_effect=RuntimeError("fail"))
        await MqttAdapter._safe_close_client(client)  # should not raise

    @pytest.mark.asyncio
    async def test_timeout_swallowed(self):
        """If __aexit__ hangs, timeout (3s) should be swallowed."""
        async def _hang(*a):
            await asyncio.sleep(100)

        client = MagicMock()
        client.__aexit__ = _hang
        # Should not hang forever
        await asyncio.wait_for(MqttAdapter._safe_close_client(client), timeout=5.0)


# ─── connect ─────────────────────────────────────────────────────────

class TestConnect:
    @pytest.mark.asyncio
    async def test_already_connected_noop(self):
        a = _make_adapter(connected=True)
        await a.connect()  # no-op

    @pytest.mark.asyncio
    async def test_no_broker_host_skips(self):
        a = _make_adapter(broker_host="")
        await a.connect()
        assert not a._connected

    @pytest.mark.asyncio
    async def test_connect_success_no_tls(self):
        a = _make_adapter()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"):
            await a.connect()
        assert a._connected is True

    @pytest.mark.asyncio
    async def test_connect_success_with_tls(self):
        a = _make_adapter(use_tls=True, broker_port=8883)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"), \
             patch("services.mqtt_adapter.settings") as s:
            s.mqtt_ca_cert = None
            s.mqtt_tls_insecure = False
            await a.connect()
        assert a._connected is True

    @pytest.mark.asyncio
    async def test_connect_success_with_tls_insecure(self):
        a = _make_adapter(use_tls=True, broker_port=8883)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"), \
             patch("services.mqtt_adapter.settings") as s:
            s.mqtt_ca_cert = None
            s.mqtt_tls_insecure = True
            await a.connect()
        assert a._connected is True

    @pytest.mark.asyncio
    async def test_connect_success_with_ca_cert(self):
        a = _make_adapter(use_tls=True, broker_port=8883)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"), \
             patch("services.mqtt_adapter.settings") as s, \
             patch("os.path.exists", return_value=True), \
             patch("ssl.create_default_context"):
            s.mqtt_ca_cert = "/path/to/ca.crt"
            s.mqtt_tls_insecure = False
            await a.connect()
        assert a._connected is True

    @pytest.mark.asyncio
    async def test_connect_with_credentials(self):
        a = _make_adapter(username="user", password="pass")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        with patch("services.mqtt_adapter.Client", return_value=mock_client) as cls, \
             patch("services.mqtt_adapter.reliability_metrics"):
            await a.connect()
        assert a._connected is True
        # Check username/password were passed
        call_kwargs = cls.call_args
        assert call_kwargs.kwargs.get("username") == "user" or "username" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_connect_timeout(self):
        a = _make_adapter()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"):
            with pytest.raises(asyncio.TimeoutError):
                await a.connect()
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_connect_refused(self):
        a = _make_adapter()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=ConnectionRefusedError)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"):
            with pytest.raises(ConnectionRefusedError):
                await a.connect()
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_connect_generic_error(self):
        a = _make_adapter()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("fail"))
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"):
            with pytest.raises(RuntimeError):
                await a.connect()
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_connect_tls_validation_failed_no_fallback(self):
        """If CA cert exists but validation fails, no auto-fallback to insecure."""
        a = _make_adapter(use_tls=True, broker_port=8883)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=ssl.SSLError("certificate verify failed"))
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"), \
             patch("services.mqtt_adapter.settings") as s, \
             patch("os.path.exists", return_value=True), \
             patch("ssl.create_default_context"):
            s.mqtt_ca_cert = "/path/to/ca.crt"
            s.mqtt_tls_insecure = False
            with pytest.raises(ssl.SSLError):
                await a.connect()
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_connect_cleans_leftover_client(self):
        """If a dead client exists from a previous connection, clean it up."""
        a = _make_adapter()
        old_client = MagicMock()
        old_client.__aexit__ = AsyncMock()
        a.client = old_client
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"):
            await a.connect()
        assert a._connected is True
        old_client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_client_id_fallback(self):
        """If Client doesn't accept client_id, try identifier, then no id."""
        a = _make_adapter()

        def _fail_client_id(**kwargs):
            if "client_id" in kwargs:
                raise TypeError("unexpected keyword argument 'client_id'")
            if "identifier" in kwargs:
                raise TypeError("unexpected keyword argument 'identifier'")
            m = MagicMock()
            m.__aenter__ = AsyncMock(return_value=m)
            return m

        with patch("services.mqtt_adapter.Client", side_effect=_fail_client_id), \
             patch("services.mqtt_adapter.reliability_metrics"):
            await a.connect()
        assert a._connected is True

    @pytest.mark.asyncio
    async def test_port_8883_without_tls_warns(self):
        """Port 8883 + TLS=false should still connect (warning only)."""
        a = _make_adapter(broker_port=8883, use_tls=False)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        with patch("services.mqtt_adapter.Client", return_value=mock_client), \
             patch("services.mqtt_adapter.reliability_metrics"):
            await a.connect()
        assert a._connected is True


# ─── disconnect extended ─────────────────────────────────────────────

class TestDisconnectExtended:
    @pytest.mark.asyncio
    async def test_disconnect_cancels_consumer_task(self):
        a = _make_adapter(connected=True)
        mock_client = MagicMock()
        mock_client.__aexit__ = AsyncMock()
        a.client = mock_client

        async def _noop():
            await asyncio.sleep(100)

        a._command_consumer_task = asyncio.create_task(_noop())
        await a.disconnect()
        assert a._connected is False
        assert a._command_consumer_task is None

    @pytest.mark.asyncio
    async def test_disconnect_cancels_subscribe_tasks(self):
        a = _make_adapter(connected=True)
        mock_client = MagicMock()
        mock_client.__aexit__ = AsyncMock()
        a.client = mock_client

        async def _noop():
            await asyncio.sleep(100)

        a._subscribe_tasks = [asyncio.create_task(_noop())]
        await a.disconnect()
        assert len(a._subscribe_tasks) == 0

    @pytest.mark.asyncio
    async def test_disconnect_error_in_aexit(self):
        a = _make_adapter(connected=True)
        mock_client = MagicMock()
        mock_client.__aexit__ = AsyncMock(side_effect=RuntimeError("close fail"))
        a.client = mock_client
        with patch("services.mqtt_adapter.reliability_metrics"):
            await a.disconnect()
        assert a._connected is False
        assert a.client is None


# ─── _mark_disconnected extended ─────────────────────────────────────

class TestMarkDisconnectedExtended:
    def test_cleans_up_dead_client(self):
        a = _make_adapter(connected=True)
        old_client = MagicMock()
        a.client = old_client
        a._mark_disconnected("test reason")
        assert a._connected is False
        assert a.client is None

    def test_rate_limited_logging(self):
        a = _make_adapter(connected=True)
        a.client = None
        import time
        a._last_disconnect_log_time = time.time()  # just logged
        a._connected = False  # already disconnected
        a._mark_disconnected("second call")  # should be a no-op (already disconnected)

    def test_with_exception(self):
        a = _make_adapter(connected=True)
        a.client = None
        a._mark_disconnected("fail", exc=RuntimeError("boom"))
        assert a._connected is False

    def test_no_running_loop_doesnt_crash(self):
        """If there's no running event loop (shutdown), should not crash."""
        a = _make_adapter(connected=True)
        mock_client = MagicMock()
        a.client = mock_client
        # Patch so get_running_loop raises
        with patch("asyncio.get_running_loop", side_effect=RuntimeError):
            a._mark_disconnected("shutdown")
        assert a._connected is False


# ─── _decode_json_payload extended ────────────────────────────────────

class TestDecodePayloadExtended:
    def test_memoryview_input(self):
        a = _make_adapter()
        data = memoryview(json.dumps({"key": "val"}).encode("utf-8"))
        result = a._decode_json_payload(data, topic="test")
        assert result == {"key": "val"}

    def test_oversized_string_input(self):
        a = _make_adapter()
        a._max_incoming_payload_bytes = 10
        result = a._decode_json_payload("x" * 100, topic="test")
        assert result is None

    def test_non_bytes_non_str_input(self):
        a = _make_adapter()
        result = a._decode_json_payload(12345, topic="test")
        assert result == {"value": 12345}


# ─── publish methods — error paths ───────────────────────────────────

class TestPublishErrorPaths:
    @pytest.mark.asyncio
    async def test_publish_navigation_connection_lost(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        from services.mqtt_adapter import MqttError
        a.client.publish = AsyncMock(side_effect=OSError("broken pipe"))
        with patch("services.mqtt_adapter.reliability_metrics"):
            await a.publish_navigation_status({"status": "idle"})
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_publish_navigation_unexpected_error(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        a.client.publish = AsyncMock(side_effect=ValueError("unexpected"))
        with patch("services.mqtt_adapter.reliability_metrics"):
            await a.publish_navigation_status({"status": "idle"})
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_publish_position_connection_lost(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        a.client.publish = AsyncMock(side_effect=OSError("broken pipe"))
        with patch("services.mqtt_adapter.reliability_metrics"):
            await a.publish_position_status({"x": 1})
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_publish_event_connection_lost(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        a.client.publish = AsyncMock(side_effect=OSError("broken"))
        with patch("services.mqtt_adapter.reliability_metrics"), \
             patch("services.mqtt_adapter.settings") as s:
            s.mqtt_events_topic = lambda kind: f"aroc/robot/robot1/events/{kind}"
            await a.publish_event("ack", {"type": "received"})
        assert a._connected is False

    @pytest.mark.asyncio
    async def test_publish_command_mqtt_code_error(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        from services.mqtt_adapter import MqttCodeError
        a.client.publish = AsyncMock(side_effect=MqttCodeError())
        with patch("services.mqtt_adapter.reliability_metrics"):
            with pytest.raises(MqttUnavailableError):
                await a.publish_command("navigateTo", {"target_id": "A"})

    @pytest.mark.asyncio
    async def test_publish_command_os_error(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        a.client.publish = AsyncMock(side_effect=OSError("socket error"))
        with patch("services.mqtt_adapter.reliability_metrics"):
            with pytest.raises(MqttUnavailableError):
                await a.publish_command("navigateTo", {"target_id": "A"})

    @pytest.mark.asyncio
    async def test_publish_command_unexpected_error(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        a.client.publish = AsyncMock(side_effect=ValueError("unexpected"))
        with patch("services.mqtt_adapter.reliability_metrics"):
            with pytest.raises(MqttUnavailableError):
                await a.publish_command("navigateTo", {"target_id": "A"})

    @pytest.mark.asyncio
    async def test_publish_navigation_type_error_fallback(self):
        """If publish(topic, payload, qos=0) raises TypeError, retry without qos."""
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        call_count = 0

        async def _typed_publish(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and "qos" in kwargs:
                raise TypeError("unexpected keyword argument 'qos'")

        a.client.publish = _typed_publish
        with patch("services.mqtt_adapter.reliability_metrics"):
            await a.publish_navigation_status({"status": "idle"})
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_publish_position_type_error_fallback(self):
        a = _make_adapter(connected=True)
        a.client = MagicMock()
        call_count = 0

        async def _typed_publish(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and "qos" in kwargs:
                raise TypeError("unexpected keyword argument 'qos'")

        a.client.publish = _typed_publish
        with patch("services.mqtt_adapter.reliability_metrics"):
            await a.publish_position_status({"x": 1})
        assert call_count == 2


# ─── start_command_consumer ──────────────────────────────────────────

class TestStartCommandConsumer:
    @pytest.mark.asyncio
    async def test_creates_consumer_task(self):
        a = _make_adapter(connected=True)
        on_nav = AsyncMock()
        on_cancel = AsyncMock()
        await a.start_command_consumer(on_nav, on_cancel)
        assert a._command_consumer_task is not None
        assert not a._command_consumer_task.done()
        a._command_consumer_task.cancel()
        await asyncio.gather(a._command_consumer_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_idempotent(self):
        a = _make_adapter(connected=True)

        async def _fake():
            await asyncio.sleep(100)

        a._command_consumer_task = asyncio.create_task(_fake())
        on_nav = AsyncMock()
        on_cancel = AsyncMock()
        await a.start_command_consumer(on_nav, on_cancel)
        # should not have replaced the task — it's still running
        a._command_consumer_task.cancel()
        await asyncio.gather(a._command_consumer_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_prunes_finished_tasks(self):
        a = _make_adapter(connected=True)

        async def _done():
            pass

        t = asyncio.create_task(_done())
        await asyncio.sleep(0)  # let it finish
        a._subscribe_tasks = [t]
        on_nav = AsyncMock()
        on_cancel = AsyncMock()
        await a.start_command_consumer(on_nav, on_cancel)
        # Finished task should be pruned, new consumer appended
        assert all(not t.done() for t in a._subscribe_tasks if t != a._command_consumer_task or True)
        a._command_consumer_task.cancel()
        await asyncio.gather(a._command_consumer_task, return_exceptions=True)


# ─── _get_event_topic ────────────────────────────────────────────────

class TestGetEventTopic:
    def test_uses_settings(self):
        a = _make_adapter()
        with patch("services.mqtt_adapter.settings") as s:
            s.mqtt_events_topic = lambda kind: f"custom/{kind}"
            assert a._get_event_topic("ack") == "custom/ack"
