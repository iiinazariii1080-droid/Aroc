"""Unit tests for telemetry.py — TelemetryService lifecycle, publish, error handling."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from config import TelemetryServiceSettings
from shared.config_types import MQTTConnectionConfig
from telemetry import (
    ServiceStatusResult,
    TelemetryService,
    _build_poll_urls,
    _build_telemetry_messages,
    _ServiceState,
    fetch_service_status,
)
from tests.conftest import make_mqtt_config, make_settings, make_service


# ------------------------------------------------------------------
# _build_poll_urls
# ------------------------------------------------------------------


class TestBuildPollUrls:
    def test_local(self):
        s = make_settings(local_ip="10.0.0.5")
        result = _build_poll_urls(s)
        assert "robot" in result
        assert "10.0.0.5" in result["robot"]

    def test_remote(self):
        s = make_settings(is_remote=True, remote_address="cloud.host")
        result = _build_poll_urls(s)
        assert result == {"robot": "http://cloud.host/robot/status"}

    def test_remote_without_address_returns_empty(self, caplog):
        s = make_settings(is_remote=True, remote_address="")
        result = _build_poll_urls(s)
        assert result == {}


# ------------------------------------------------------------------
# fetch_service_status
# ------------------------------------------------------------------


class TestFetchServiceStatus:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"key": "val"}
        session = MagicMock()
        session.get.return_value = mock_resp
        result = fetch_service_status("http://x/status", 3, session)
        assert isinstance(result, ServiceStatusResult)
        status, data, error = result
        assert status == "online"
        assert data == {"key": "val"}
        assert error is None
        assert result.status == "online"
        assert result.data == {"key": "val"}
        assert result.error is None

    def test_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        session = MagicMock()
        session.get.return_value = mock_resp
        status, _data, error = fetch_service_status("http://x/status", 3, session)
        assert status == "error"
        assert error == "http_status_500"

    def test_invalid_json_returns_degraded(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad json")
        mock_resp.text = "not-json"
        session = MagicMock()
        session.get.return_value = mock_resp
        status, data, error = fetch_service_status("http://x/status", 3, session)
        assert status == "degraded"
        assert data is None
        assert error == "invalid_json"

    def test_invalid_json_long_body_not_leaked(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad json")
        mock_resp.text = "x" * 1000
        session = MagicMock()
        session.get.return_value = mock_resp
        status, data, error = fetch_service_status("http://x/status", 3, session)
        assert status == "degraded"
        assert error == "invalid_json"
        assert data is None

    def test_timeout(self):
        session = MagicMock()
        session.get.side_effect = requests.exceptions.Timeout()
        status, _data, error = fetch_service_status("http://x/status", 3, session)
        assert status == "error"
        assert error == "timeout"

    def test_connection_error(self):
        session = MagicMock()
        session.get.side_effect = requests.exceptions.ConnectionError()
        status, _data, error = fetch_service_status("http://x/status", 3, session)
        assert status == "offline"
        assert error == "connection_error"


# ------------------------------------------------------------------
# TelemetryService lifecycle
# ------------------------------------------------------------------


class TestServiceLifecycle:
    def test_initial_state_is_created(self):
        svc, _ = make_service()
        assert svc._state == _ServiceState.CREATED

    def test_setup_transitions_to_ready(self):
        svc, _ = make_service()
        with patch("telemetry.LightMQTTClient"):
            svc.setup()
        assert svc._state == _ServiceState.READY

    def test_setup_twice_raises(self):
        svc, _ = make_service()
        with patch("telemetry.LightMQTTClient"):
            svc.setup()
        with pytest.raises(RuntimeError, match="ready"):
            svc.setup()

    def test_connect_before_setup_raises(self):
        svc, _ = make_service()
        with pytest.raises(RuntimeError, match="setup\\(\\) must be called"):
            svc.connect()

    def test_connect_transitions_to_connected(self):
        svc, fake = make_service(connected=True)
        assert svc._state == _ServiceState.CONNECTED

    def test_connect_returns_false_on_timeout(self):
        from tests.fakes import FakeLightMQTTClient
        fake = FakeLightMQTTClient(start_connected=False)
        svc, _ = make_service(connected=False, fake_client=fake)
        with patch("telemetry.LightMQTTClient", return_value=fake):
            svc.setup()
            result = svc.connect()
        assert result is False
        assert svc._state == _ServiceState.CONNECTING

    def test_loop_before_connect_raises(self):
        svc, _ = make_service()
        with patch("telemetry.LightMQTTClient"):
            svc.setup()
        with pytest.raises(RuntimeError, match="ready"):
            svc.loop()

    def test_stop_is_idempotent(self):
        svc, _ = make_service()
        svc.stop()
        svc.stop()  # should not raise
        assert svc._state == _ServiceState.STOPPED

    def test_stop_calls_mqtt_stop(self):
        svc, fake = make_service(connected=True)
        svc.stop()
        assert fake._stopped is True
        assert svc.shutdown_flag.is_set()


# ------------------------------------------------------------------
# TelemetryService._publish
# ------------------------------------------------------------------


class TestServicePublish:
    def test_publish_success(self):
        svc, fake = make_service(connected=True)
        result = svc._publish("test/topic", {"key": "value"})
        assert result is True
        assert svc._publish_count == 1
        assert svc._drop_count == 0
        assert svc._last_successful_publish > 0.0
        assert len(fake.publish_log) == 1

    def test_publish_when_disconnected(self):
        svc, fake = make_service(connected=True)
        fake._connected = False
        result = svc._publish("test/topic", {"key": "value"})
        assert result is False
        assert svc._drop_count == 1

    def test_publish_failure_increments_drop(self):
        from tests.fakes import FakeLightMQTTClient
        fake = FakeLightMQTTClient(start_connected=True, publish_succeeds=False)
        svc, _ = make_service(connected=True, fake_client=fake)
        result = svc._publish("test/topic", {"key": "value"})
        assert result is False
        assert svc._drop_count == 1

    def test_oversized_payload_dropped(self):
        svc, fake = make_service(connected=True)
        huge_payload = {"data": "x" * (1024 * 1024 + 1)}
        result = svc._publish("test/topic", huge_payload)
        assert result is False
        assert svc._drop_count == 1
        assert len(fake.publish_log) == 0

    def test_publish_no_client(self):
        svc, _ = make_service(connected=False)
        result = svc._publish("test/topic", {"key": "value"})
        assert result is False
        assert svc._drop_count == 1


# ------------------------------------------------------------------
# TelemetryService.loop (integration-style) — uses real Events
# ------------------------------------------------------------------


class TestServiceLoop:
    def test_loop_runs_one_cycle_then_stops(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 1},
        )

        robot_response = MagicMock()
        robot_response.status_code = 200
        robot_response.json.return_value = {
            "symovo": {
                "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
                "state": "idle",
                "battery_level_percent": 80,
                "velocity": {},
            }
        }

        call_count = 0

        def fake_fetch(url, timeout, session):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                svc.shutdown_flag.set()
            return ("online", robot_response.json.return_value, None)

        with (
            patch("telemetry.fetch_service_status", side_effect=fake_fetch),
            patch("telemetry.collect_host_metrics", return_value=None),
        ):
            svc.loop()

        assert len(fake.publish_log) >= 1
        assert svc._publish_count >= 1


# ------------------------------------------------------------------
# TelemetryService._check_janus_ws
# ------------------------------------------------------------------


class TestCheckJanusWs:
    def test_returns_default_when_no_check_yet(self):
        svc, _ = make_service()
        assert svc._janus_status == {"depth": False, "color": False}

    def test_caches_result_within_interval(self):
        svc, _ = make_service(settings_overrides={"websocket_check_interval": 1000.0})
        svc._janus_last_check = 999999999999.0  # far future
        svc._janus_status = {"depth": True, "color": True}
        result = svc._check_janus_ws()
        assert result == {"depth": True, "color": True}


# ------------------------------------------------------------------
# TLS insecure warning
# ------------------------------------------------------------------


class TestTlsInsecureWarning:
    def test_warning_logged_when_tls_insecure(self, caplog):
        with caplog.at_level("WARNING"):
            make_service(settings_overrides={"websocket_tls_insecure": True})
        assert "TLS certificate verification DISABLED" in caplog.text

    def test_tls_insecure_blocked_in_remote_mode(self):
        with pytest.raises(ValueError, match=r"WEBSOCKET_TLS_INSECURE.*remote"):
            make_settings(
                is_remote=True,
                remote_address="cloud.host",
                websocket_tls_insecure=True,
            )

    def test_tls_insecure_allowed_in_remote_with_override(self, caplog):
        settings = make_settings(
            is_remote=True,
            remote_address="cloud.host",
            websocket_tls_insecure=True,
            allow_tls_insecure_remote=True,
        )
        assert settings.websocket_tls_insecure is True

    def test_no_warning_when_tls_secure(self, caplog):
        with caplog.at_level("WARNING"):
            make_service(settings_overrides={"websocket_tls_insecure": False})
        assert "TLS certificate verification DISABLED" not in caplog.text


# ------------------------------------------------------------------
# MQTTConnectionConfig __repr__ (credential masking)
# ------------------------------------------------------------------


class TestMQTTConnectionConfigRepr:
    def test_password_masked(self):
        cfg = make_mqtt_config(mqtt_password="super_secret")
        r = repr(cfg)
        assert "super_secret" not in r
        assert "***" in r
        assert "localhost" in r

    def test_user_masked(self):
        cfg = make_mqtt_config(mqtt_user="admin")
        r = repr(cfg)
        assert "admin" not in r
        assert "***" in r


# ------------------------------------------------------------------
# TelemetryService.is_healthy
# ------------------------------------------------------------------


class TestIsHealthy:
    def test_healthy_when_connected_no_publishes_yet(self):
        svc, _ = make_service(connected=True)
        assert svc.is_healthy() is True

    def test_healthy_after_recent_publish(self):
        svc, _ = make_service(connected=True)
        svc._publish("test/topic", {"key": "value"})
        assert svc.is_healthy() is True

    def test_unhealthy_when_mqtt_disconnected(self):
        svc, fake = make_service(connected=True)
        fake._connected = False
        assert svc.is_healthy() is False

    def test_unhealthy_when_no_client(self):
        svc, _ = make_service(connected=False)
        assert svc.is_healthy() is False

    def test_unhealthy_when_publish_stale(self):
        svc, _ = make_service(connected=True)
        with svc._stats_lock:
            svc._last_successful_publish = 1.0  # epoch time far in the past
        assert svc.is_healthy() is False

    def test_health_reason_ok_when_healthy(self):
        svc, _ = make_service(connected=True)
        assert svc.health_reason() == "ok"

    def test_health_reason_mqtt_disconnected(self):
        svc, fake = make_service(connected=True)
        fake._connected = False
        assert "mqtt_disconnected" in svc.health_reason()

    def test_health_reason_no_client(self):
        svc, _ = make_service(connected=False)
        assert "not_initialized" in svc.health_reason()

    def test_health_reason_stale_publish(self):
        svc, _ = make_service(connected=True)
        with svc._stats_lock:
            svc._last_successful_publish = 1.0
        reason = svc.health_reason()
        assert "last_publish" in reason
        assert "threshold" in reason


# ------------------------------------------------------------------
# Exponential backoff
# ------------------------------------------------------------------


class TestExponentialBackoff:
    def test_delay_increases(self):
        from telemetry import _BACKOFF_EXPONENT_CAP

        base = TelemetryService._RESTART_DELAY_BASE
        max_delay = TelemetryService._RESTART_DELAY_MAX
        delays = []
        for i in range(1, 7):
            delay = min(base * (2 ** min(i - 1, _BACKOFF_EXPONENT_CAP)), max_delay)
            delays.append(delay)
        assert delays[0] == base
        assert delays[1] > delays[0]
        assert delays[2] > delays[1]
        assert all(d <= max_delay for d in delays)


# ------------------------------------------------------------------
# Error recovery in loop() — uses real Events, no lambda replacement
# ------------------------------------------------------------------


class TestLoopErrorRecovery:
    def test_recoverable_error_retries(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 1},
        )

        call_count = 0

        def fake_fetch(url, timeout, session):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise requests.exceptions.ConnectionError("simulated")
            svc.shutdown_flag.set()
            return ("online", {"symovo": {"state": "idle"}}, None)

        with (
            patch("telemetry.fetch_service_status", side_effect=fake_fetch),
            patch("telemetry.collect_host_metrics", return_value=None),
        ):
            svc.loop()

        assert call_count >= 2
        assert svc._state == _ServiceState.RUNNING

    def test_non_recoverable_error_stops(self):
        svc, fake = make_service(connected=True)

        def fatal_fetch(url, timeout, session):
            raise RuntimeError("fatal bug")

        with (
            patch("telemetry.fetch_service_status", side_effect=fatal_fetch),
            patch("telemetry.collect_host_metrics", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="fatal bug"):
                svc.loop()

        assert svc._state == _ServiceState.STOPPED
        assert svc.shutdown_flag.is_set()


# ------------------------------------------------------------------
# _build_telemetry_messages (pure function)
# ------------------------------------------------------------------


class TestBuildTelemetryMessages:
    def _robot_id(self):
        return "test-bot"

    def _topics(self):
        from shared.config_types import TopicSchema
        return TopicSchema("test-bot")

    def _janus(self, depth=False, color=False):
        return {"depth": depth, "color": color}

    def test_with_full_robot_data(self):
        service_data = {
            "robot": (
                "online",
                {
                    "symovo": {
                        "pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0},
                        "state": "idle",
                        "velocity": {},
                        "battery_level_percent": 80,
                    },
                },
                None,
            ),
        }
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(), mqtt_ok=True,
        )
        topics = [t for t, _ in messages]
        assert any("navigation" in t for t in topics)
        assert any("telemetry" in t for t in topics)
        assert any("system" in t for t in topics)
        assert any("connection" in t for t in topics)

    def test_without_symovo_data(self):
        service_data = {"robot": ("online", {"other": "data"}, None)}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(), mqtt_ok=False,
        )
        topics = [t for t, _ in messages]
        assert not any("navigation" in t for t in topics)
        assert not any("/telemetry" in t for t in topics)
        assert not any("system" in t for t in topics)
        assert any("connection" in t for t in topics)

    def test_robot_offline(self):
        service_data = {"robot": ("offline", None, "connection_error")}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(), mqtt_ok=False,
        )
        topics = [t for t, _ in messages]
        assert any("connection" in t for t in topics)
        assert len(messages) == 2

    def test_connection_payload_reflects_janus_and_mqtt(self):
        service_data = {"robot": ("offline", None, None)}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(depth=True, color=True), mqtt_ok=True,
        )
        conn_msgs = [(t, p) for t, p in messages if "connection" in t]
        assert len(conn_msgs) == 1
        _, payload = conn_msgs[0]
        assert payload["janus_ws"] == {"depth": True, "color": True}
        assert payload["mqtt"] is True

    def test_connection_payload_partial_janus(self):
        service_data = {"robot": ("offline", None, None)}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(depth=True, color=False), mqtt_ok=True,
        )
        conn_msgs = [(t, p) for t, p in messages if "connection" in t]
        _, payload = conn_msgs[0]
        assert payload["janus_ws"]["depth"] is True
        assert payload["janus_ws"]["color"] is False

    def test_empty_service_data(self):
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), {},
            janus_status=self._janus(), mqtt_ok=False,
        )
        assert any("connection" in t for t, _ in messages)
        assert len(messages) == 1

    def test_poll_seq_injected(self):
        service_data = {"robot": ("offline", None, None)}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(), mqtt_ok=False, poll_seq=42,
        )
        for _, payload in messages:
            assert payload.get("poll_seq") == 42

    def test_session_id_injected(self):
        service_data = {"robot": ("offline", None, None)}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(), mqtt_ok=False, poll_seq=1, session_id="abc12345",
        )
        for _, payload in messages:
            assert payload.get("session_id") == "abc12345"

    def test_session_id_omitted_when_empty(self):
        service_data = {"robot": ("offline", None, None)}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(), mqtt_ok=False, poll_seq=1, session_id="",
        )
        for _, payload in messages:
            assert "session_id" not in payload

    def test_payload_builder_data_error_does_not_crash(self):
        service_data = {
            "robot": (
                "online",
                {"symovo": {"pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0}, "state": "idle", "velocity": {}, "battery_level_percent": 80}},
                None,
            ),
        }
        with patch("telemetry.build_navigation_status_payload", side_effect=KeyError("missing")):
            messages = _build_telemetry_messages(
                self._robot_id(), self._topics(), service_data,
                janus_status=self._janus(), mqtt_ok=True,
            )
        topics = [t for t, _ in messages]
        assert not any("navigation" in t for t in topics)
        assert any("connection" in t for t in topics)

    def test_payload_builder_programming_error_caught_gracefully(self):
        service_data = {
            "robot": (
                "online",
                {"symovo": {"pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0}, "state": "idle", "velocity": {}, "battery_level_percent": 80}},
                None,
            ),
        }
        with patch("telemetry.build_navigation_status_payload", side_effect=TypeError("bug")):
            messages = _build_telemetry_messages(
                self._robot_id(), self._topics(), service_data,
                janus_status=self._janus(), mqtt_ok=True,
            )
        topics = [t for t, _ in messages]
        assert not any("navigation" in t for t in topics)
        assert any("connection" in t for t in topics)

    def test_message_id_injected(self):
        service_data = {"robot": ("offline", None, None)}
        messages = _build_telemetry_messages(
            self._robot_id(), self._topics(), service_data,
            janus_status=self._janus(), mqtt_ok=False, poll_seq=7, session_id="sess1",
        )
        ids = [p["message_id"] for _, p in messages]
        assert all(ids)
        assert len(ids) == len(set(ids))


# ------------------------------------------------------------------
# TelemetryService session recovery
# ------------------------------------------------------------------


class TestSessionRecovery:
    def test_reset_session_closes_existing(self):
        svc, _ = make_service()
        mock_session = MagicMock()
        svc._session = mock_session
        svc._reset_session()
        mock_session.close.assert_called_once()
        assert svc._session is None

    def test_reset_session_noop_when_no_session(self):
        svc, _ = make_service()
        svc._session = None
        svc._reset_session()  # should not raise
        assert svc._session is None
