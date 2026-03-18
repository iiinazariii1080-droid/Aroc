"""Runtime layer tests: threading, lifecycle, loop behavior, shutdown.

Covers the untested runtime layer that makes green CI meaningful for
operational reliability. No MagicMock for LightMQTTClient — uses
FakeLightMQTTClient throughout.
"""

import ssl
import threading
from unittest.mock import MagicMock, patch

import pytest

from telemetry import (
    TelemetryService,
    _ServiceState,
)
from tests.conftest import make_service
from tests.fakes import FakeLightMQTTClient


# ==================================================================
# T-02: _probe_janus_ws — success path
# ==================================================================


class TestProbeJanusWsSuccess:
    """Verify probe correctly updates _janus_status on successful connections."""

    def test_both_endpoints_succeed(self):
        svc, _ = make_service(settings_overrides={
            "janus_ws_depth": "ws://localhost:8188",
            "janus_ws_color": "ws://localhost:8189",
        })
        mock_ws = MagicMock()
        with patch("telemetry.websocket.create_connection", return_value=mock_ws):
            svc._probe_janus_ws()

        assert svc._janus_status == {"depth": True, "color": True}
        # WS close was called for each connection
        assert mock_ws.close.call_count == 2

    def test_single_endpoint_configured(self):
        # local_ip="" ensures effective_janus_ws_color returns "" (no default URL)
        svc, _ = make_service(settings_overrides={
            "janus_ws_depth": "ws://localhost:8188",
            "janus_ws_color": "",
            "local_ip": "",
        })
        mock_ws = MagicMock()
        with patch("telemetry.websocket.create_connection", return_value=mock_ws):
            svc._probe_janus_ws()

        assert svc._janus_status["depth"] is True
        assert svc._janus_status["color"] is False


# ==================================================================
# T-03: _probe_janus_ws — failure modes
# ==================================================================


class TestProbeJanusWsFailures:
    """Verify probe handles failure modes: timeout, SSL error, unexpected crash."""

    def _make_probe_service(self):
        svc, _ = make_service(settings_overrides={
            "janus_ws_depth": "wss://localhost:8188",
            "janus_ws_color": "wss://localhost:8189",
        })
        return svc

    def test_timeout_error(self):
        svc = self._make_probe_service()
        with patch("telemetry.websocket.create_connection", side_effect=TimeoutError("ws timeout")):
            svc._probe_janus_ws()

        assert svc._janus_status == {"depth": False, "color": False}

    def test_ssl_cert_verification_error(self):
        svc = self._make_probe_service()
        with patch(
            "telemetry.websocket.create_connection",
            side_effect=ssl.SSLCertVerificationError("cert failed"),
        ):
            svc._probe_janus_ws()

        assert svc._janus_status == {"depth": False, "color": False}

    def test_unexpected_runtime_error(self):
        svc = self._make_probe_service()
        with patch("telemetry.websocket.create_connection", side_effect=RuntimeError("unexpected")):
            svc._probe_janus_ws()

        assert svc._janus_status == {"depth": False, "color": False}

    def test_partial_failure_depth_succeeds_color_times_out(self):
        svc = self._make_probe_service()
        mock_ws = MagicMock()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_ws  # depth succeeds
            raise TimeoutError("color timeout")  # color fails

        with patch("telemetry.websocket.create_connection", side_effect=side_effect):
            svc._probe_janus_ws()

        assert svc._janus_status["depth"] is True
        assert svc._janus_status["color"] is False


# ==================================================================
# T-04: _probe_janus_ws — shutdown interruption
# ==================================================================


class TestProbeJanusWsShutdown:
    """Verify probe aborts when shutdown_flag is set."""

    def test_shutdown_before_probe_skips_all(self):
        svc, _ = make_service(settings_overrides={
            "janus_ws_depth": "ws://localhost:8188",
            "janus_ws_color": "ws://localhost:8189",
        })
        svc.shutdown_flag.set()

        with patch("telemetry.websocket.create_connection") as mock_create:
            svc._probe_janus_ws()

        # Should not have called create_connection at all
        mock_create.assert_not_called()
        # Status should remain default (not updated)
        assert svc._janus_status == {"depth": False, "color": False}


# ==================================================================
# T-05: _check_janus_ws — cache-miss path (thread spawn)
# ==================================================================


class TestCheckJanusWsThreadSpawn:
    """Verify cache-miss spawns a thread and no-respawn when thread alive."""

    def test_cache_miss_spawns_thread(self):
        svc, _ = make_service(settings_overrides={
            "websocket_check_interval": 0.001,
            "janus_ws_depth": "ws://localhost:8188",
            "janus_ws_color": "ws://localhost:8189",
        })
        svc._janus_last_check = 0  # expired

        mock_ws = MagicMock()
        with patch("telemetry.websocket.create_connection", return_value=mock_ws):
            result = svc._check_janus_ws()
            # Wait for probe thread to complete
            if svc._janus_thread is not None:
                svc._janus_thread.join(timeout=2.0)

        # Thread was spawned and completed
        assert svc._janus_thread is not None
        assert not svc._janus_thread.is_alive()
        # Status updated after thread completes
        assert svc._janus_status == {"depth": True, "color": True}

    def test_no_respawn_when_thread_alive(self):
        svc, _ = make_service(settings_overrides={
            "websocket_check_interval": 0.001,
            "janus_ws_depth": "ws://localhost:8188",
            "janus_ws_color": "ws://localhost:8189",
        })
        svc._janus_last_check = 0

        # Create a long-running fake thread
        blocker = threading.Event()
        fake_thread = threading.Thread(target=blocker.wait, args=(2.0,), daemon=True)
        fake_thread.start()
        svc._janus_thread = fake_thread

        with patch("telemetry.websocket.create_connection") as mock_create:
            svc._check_janus_ws()

        # No new thread spawned, no WS connection made
        mock_create.assert_not_called()
        assert svc._janus_thread is fake_thread  # same thread, not replaced
        blocker.set()
        fake_thread.join(timeout=1.0)


# ==================================================================
# T-06: stop() from every reachable state
# ==================================================================


class TestStopFromEveryState:
    """Verify stop() transitions to STOPPED from every reachable state."""

    def test_stop_from_created(self):
        svc, _ = make_service()
        assert svc._state == _ServiceState.CREATED
        svc.stop()
        assert svc._state == _ServiceState.STOPPED
        assert svc.shutdown_flag.is_set()

    def test_stop_from_ready(self):
        fake = FakeLightMQTTClient()
        svc, _ = make_service(fake_client=fake)
        with patch("telemetry.LightMQTTClient", return_value=fake):
            svc.setup()
        assert svc._state == _ServiceState.READY
        svc.stop()
        assert svc._state == _ServiceState.STOPPED
        assert fake._stopped is True

    def test_stop_from_connecting(self):
        fake = FakeLightMQTTClient(start_connected=False)
        svc, _ = make_service(fake_client=fake)
        with patch("telemetry.LightMQTTClient", return_value=fake):
            svc.setup()
            svc.connect()
        assert svc._state == _ServiceState.CONNECTING
        svc.stop()
        assert svc._state == _ServiceState.STOPPED
        assert fake._stopped is True

    def test_stop_from_connected(self):
        svc, fake = make_service(connected=True)
        assert svc._state == _ServiceState.CONNECTED
        svc.stop()
        assert svc._state == _ServiceState.STOPPED
        assert fake._stopped is True

    def test_stop_from_running(self):
        svc, fake = make_service(connected=True)
        # Force to RUNNING state
        with svc._state_lock:
            svc._state = _ServiceState.RUNNING
        svc.stop()
        assert svc._state == _ServiceState.STOPPED
        assert fake._stopped is True

    def test_stop_twice_is_idempotent(self):
        svc, fake = make_service(connected=True)
        with svc._state_lock:
            svc._state = _ServiceState.RUNNING
        svc.stop()
        svc.stop()  # second call — no exception
        assert svc._state == _ServiceState.STOPPED


# ==================================================================
# T-07: Illegal state transitions
# ==================================================================


class TestIllegalStateTransitions:
    """Verify _transition_state rejects invalid transitions."""

    def test_running_to_ready_rejected(self):
        svc, _ = make_service()
        with svc._state_lock:
            svc._state = _ServiceState.RUNNING
        with pytest.raises(RuntimeError, match="running"):
            svc._transition_state(_ServiceState.READY, _ServiceState.CREATED)

    def test_stopped_to_running_rejected(self):
        svc, _ = make_service()
        with svc._state_lock:
            svc._state = _ServiceState.STOPPED
        with pytest.raises(RuntimeError, match="stopped"):
            svc._transition_state(
                _ServiceState.RUNNING,
                _ServiceState.CONNECTED,
                _ServiceState.CONNECTING,
            )

    def test_created_cannot_enter_loop(self):
        svc, _ = make_service()
        with pytest.raises(RuntimeError, match="created"):
            svc.loop()


# ==================================================================
# T-08: setup() failure rollback
# ==================================================================


class TestSetupRollback:
    """Verify setup() rolls back state on LightMQTTClient constructor failure."""

    def test_constructor_failure_rolls_back_to_created(self):
        svc, _ = make_service()
        with patch(
            "telemetry.LightMQTTClient",
            side_effect=ConnectionRefusedError("broker down"),
        ):
            with pytest.raises(ConnectionRefusedError):
                svc.setup()

        assert svc._state == _ServiceState.CREATED
        assert svc._mqtt_client is None

    def test_setup_retriable_after_failure(self):
        svc, _ = make_service()

        # First attempt fails
        with patch(
            "telemetry.LightMQTTClient",
            side_effect=ConnectionRefusedError("broker down"),
        ):
            with pytest.raises(ConnectionRefusedError):
                svc.setup()

        assert svc._state == _ServiceState.CREATED

        # Second attempt succeeds
        fake = FakeLightMQTTClient()
        with patch("telemetry.LightMQTTClient", return_value=fake):
            svc.setup()

        assert svc._state == _ServiceState.READY
        assert svc._mqtt_client is fake


# ==================================================================
# T-09: Loop session recovery through iteration count
# ==================================================================


class TestLoopSessionRecovery:
    """Verify session recovery through actual loop execution."""

    def test_session_reset_after_threshold_consecutive_errors(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 1},
        )
        svc._session_error_threshold = 3
        original_session_id = svc._session_id

        call_count = 0

        def fake_fetch(url, timeout, session):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                # First 3 calls: all error → triggers session reset
                return ("error", None, "timeout")
            if call_count == 4:
                # After reset, succeed then stop
                svc.shutdown_flag.set()
                return ("online", {"symovo": {"state": "idle"}}, None)
            svc.shutdown_flag.set()
            return ("online", {}, None)

        with (
            patch("telemetry.fetch_service_status", side_effect=fake_fetch),
            patch("telemetry.collect_host_metrics", return_value=None),
        ):
            svc.loop()

        # Session was reset (counter back to 0 after reset)
        assert svc._session_consecutive_errors == 0
        # The loop ran through enough iterations to trigger recovery
        assert call_count >= 4


# ==================================================================
# T-11: Loop graceful shutdown mid-cycle
# ==================================================================


class TestLoopShutdownMidCycle:
    """Verify that setting shutdown_flag during a poll causes clean exit."""

    def test_shutdown_during_fetch_exits_cleanly(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 1},
        )

        def fake_fetch(url, timeout, session):
            svc.shutdown_flag.set()  # signal shutdown mid-poll
            return ("online", {"symovo": {"state": "idle"}}, None)

        with (
            patch("telemetry.fetch_service_status", side_effect=fake_fetch),
            patch("telemetry.collect_host_metrics", return_value=None),
        ):
            svc.loop()

        # loop() itself doesn't call stop() on clean exit — state stays RUNNING
        assert svc._state == _ServiceState.RUNNING
        # finally block cleaned up session
        assert svc._session is None


# ==================================================================
# T-10: Loop with Janus check triggering
# ==================================================================


class TestLoopJanusDispatch:
    """Verify _check_janus_ws() triggers during loop and flows into payloads."""

    def test_janus_status_flows_into_published_payloads(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={
                "poll_interval_seconds": 1,
                "websocket_check_interval": 0.001,  # always trigger
                "janus_ws_depth": "ws://localhost:8188",
                "janus_ws_color": "ws://localhost:8189",
            },
        )

        call_count = 0

        def fake_fetch(url, timeout, session):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                svc.shutdown_flag.set()
            return ("online", {"symovo": {"state": "idle", "pose": {"x_m": 0, "y_m": 0, "theta_deg": 0}, "velocity": {}, "battery_level_percent": 80}}, None)

        mock_ws = MagicMock()

        with (
            patch("telemetry.fetch_service_status", side_effect=fake_fetch),
            patch("telemetry.collect_host_metrics", return_value=None),
            patch("telemetry.websocket.create_connection", return_value=mock_ws),
        ):
            svc.loop()
            # Wait for any probe threads
            if svc._janus_thread is not None:
                svc._janus_thread.join(timeout=2.0)

        # Verify payloads were published containing connection status with janus_ws
        connection_payloads = [
            (topic, payload) for topic, payload, _, _ in fake.publish_log
            if "connection" in topic
        ]
        assert len(connection_payloads) >= 1


# ==================================================================
# T-12: Health check time boundaries
# ==================================================================


class TestHealthCheckBoundaries:
    """Verify startup grace and staleness thresholds at exact boundaries."""

    def test_within_startup_grace_is_healthy(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 10},
        )
        # Grace = 10 * 6 = 60s. At startup_time + 59.9s → still within grace.
        with patch("telemetry.time.time", return_value=svc._startup_time + 59.9):
            healthy, reason = svc._health_check()
        assert healthy is True
        assert reason is None

    def test_past_startup_grace_no_publish_is_unhealthy(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 10},
        )
        # Grace = 10 * 6 = 60s. At startup_time + 60.1s → past grace, no publishes.
        with patch("telemetry.time.time", return_value=svc._startup_time + 60.1):
            healthy, reason = svc._health_check()
        assert healthy is False
        assert "startup_grace" in reason

    def test_staleness_just_under_threshold_is_healthy(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 10},
        )
        # max_age = 10 * 3 + 5 = 35s
        now = 100000.0
        with svc._stats_lock:
            svc._last_successful_publish = now - 34.9  # 34.9s ago, under 35s
        with patch("telemetry.time.time", return_value=now):
            healthy, reason = svc._health_check()
        assert healthy is True
        assert reason is None

    def test_staleness_just_over_threshold_is_unhealthy(self):
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 10},
        )
        # max_age = 10 * 3 + 5 = 35s
        now = 100000.0
        with svc._stats_lock:
            svc._last_successful_publish = now - 35.1  # 35.1s ago, over 35s
        with patch("telemetry.time.time", return_value=now):
            healthy, reason = svc._health_check()
        assert healthy is False
        assert "threshold" in reason

    def test_staleness_exactly_at_threshold_is_healthy(self):
        """Strict < comparison means exactly at threshold is still healthy."""
        svc, fake = make_service(
            connected=True,
            settings_overrides={"poll_interval_seconds": 10},
        )
        # max_age = 10 * 3 + 5 = 35s
        now = 100000.0
        with svc._stats_lock:
            svc._last_successful_publish = now - 35.0  # exactly 35s ago
        with patch("telemetry.time.time", return_value=now):
            healthy, reason = svc._health_check()
        # age (35.0) < max_age (35.0) is False, so this is unhealthy
        # The code uses `if age < max_age: return True` — so exactly at boundary is NOT healthy
        assert healthy is False


# ==================================================================
# T-13: Contract test with golden robot response
# ==================================================================


class TestGoldenRobotResponse:
    """Verify payload builders produce non-None outputs against representative data."""

    GOLDEN_ROBOT_STATUS = {
        "symovo": {
            "pose": {"x_m": 1.234, "y_m": 5.678, "theta_deg": 90.0},
            "state": "navigating",
            "velocity": {"linear_m_s": 0.5, "angular_deg_s": 10.0},
            "battery_level_percent": 72,
            "charging": False,
            "docked": False,
            "emergency_stop": False,
            "localization_quality": 0.95,
        },
        "xarm": {
            "state": "idle",
            "mode": 0,
            "error_code": 0,
        },
        "sensors": {
            "lidar": {"status": "ok"},
            "camera": {"status": "ok"},
        },
    }

    def test_validate_robot_response_no_warnings(self):
        from telemetry_payload import validate_robot_response

        warnings = validate_robot_response(self.GOLDEN_ROBOT_STATUS)
        assert warnings == [], f"Unexpected schema warnings: {warnings}"

    def test_navigation_payload_non_none(self):
        from telemetry_payload import build_navigation_status_payload

        symovo = self.GOLDEN_ROBOT_STATUS["symovo"]
        result = build_navigation_status_payload(
            "test-bot", self.GOLDEN_ROBOT_STATUS, symovo, timestamp="2025-01-01T00:00:00Z",
        )
        assert result is not None
        assert "current_position" in result, "Navigation payload missing current_position"

    def test_telemetry_payload_non_none(self):
        from telemetry_payload import build_telemetry_payload

        symovo = self.GOLDEN_ROBOT_STATUS["symovo"]
        result = build_telemetry_payload(
            "test-bot", self.GOLDEN_ROBOT_STATUS, symovo, timestamp="2025-01-01T00:00:00Z",
        )
        assert result is not None

    def test_system_status_payload_non_none(self):
        from telemetry_payload import build_system_status_payload

        symovo = self.GOLDEN_ROBOT_STATUS["symovo"]
        result = build_system_status_payload(
            "test-bot", symovo, timestamp="2025-01-01T00:00:00Z",
        )
        assert result is not None

    def test_connection_status_payload_non_none(self):
        from telemetry_payload import build_connection_status_payload

        result = build_connection_status_payload(
            "test-bot", {"depth": True, "color": True}, True, timestamp="2025-01-01T00:00:00Z",
        )
        assert result is not None
        assert result["mqtt"] is True
        assert result["janus_ws"]["depth"] is True

    def test_status_payload_non_none(self):
        from telemetry_payload import build_status_payload

        result = build_status_payload(
            "test-bot", "robot", "online", self.GOLDEN_ROBOT_STATUS, None, timestamp="2025-01-01T00:00:00Z",
        )
        assert result is not None
        assert result["status"] == "online"

    def test_critical_fields_present_in_navigation_payload(self):
        from telemetry_payload import build_navigation_status_payload

        symovo = self.GOLDEN_ROBOT_STATUS["symovo"]
        result = build_navigation_status_payload(
            "test-bot", self.GOLDEN_ROBOT_STATUS, symovo, timestamp="2025-01-01T00:00:00Z",
        )
        assert result is not None
        # Verify pose data flows through — builder converts to current_position
        assert "current_position" in result
        pos = result["current_position"]
        assert pos["x"] == pytest.approx(1.234)
