"""Shared test configuration and helpers.

Path setup is handled by [tool.pytest.ini_options] pythonpath in pyproject.toml.
"""

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from config import TelemetryServiceSettings
from shared.config_types import BridgeConfig, MQTTConnectionConfig, ServiceConfig, TopicSchema
from tests.fakes import FakeLightMQTTClient
from telemetry import TelemetryService


# ---------------------------------------------------------------------------
# MQTT / Telemetry helpers (existing)
# ---------------------------------------------------------------------------


def make_mqtt_config(**overrides) -> MQTTConnectionConfig:
    defaults = dict(
        broker="localhost",
        broker_port=1883,
        mqtt_user="user",
        mqtt_password="pass",
        robot_id="test-bot",
        client_id="test-bot-telemetry",
        mqtt_publish_qos=1,
        mqtt_use_tls=False,
    )
    defaults.update(overrides)
    return MQTTConnectionConfig(**defaults)


def make_settings(**overrides) -> TelemetryServiceSettings:
    defaults = dict(
        local_ip="127.0.0.1",
        is_remote=False,
        poll_interval_seconds=1,
        telemetry_http_timeout=1,
    )
    defaults.update(overrides)
    return TelemetryServiceSettings(**defaults)


def make_service(
    *,
    connected: bool = False,
    fake_client: FakeLightMQTTClient | None = None,
    settings_overrides: dict | None = None,
    mqtt_config_overrides: dict | None = None,
) -> tuple[TelemetryService, FakeLightMQTTClient | None]:
    """Build a TelemetryService with FakeLightMQTTClient.

    Args:
        connected: If True, runs setup() + connect() to reach CONNECTED state.
        fake_client: Pre-built fake. If None and connected=True, one is created.
        settings_overrides: Overrides for TelemetryServiceSettings.
        mqtt_config_overrides: Overrides for MQTTConnectionConfig.

    Returns:
        (service, fake_client_or_None)
    """
    s_overrides = settings_overrides or {}
    m_overrides = mqtt_config_overrides or {}

    if connected and fake_client is None:
        fake_client = FakeLightMQTTClient(start_connected=True)

    with patch("telemetry.warmup_psutil"):
        if fake_client is not None:
            with patch("telemetry.LightMQTTClient", return_value=fake_client):
                svc = TelemetryService(
                    mqtt_config=make_mqtt_config(**m_overrides),
                    settings=make_settings(**s_overrides),
                )
                if connected:
                    svc.setup()
                    svc.connect()
        else:
            svc = TelemetryService(
                mqtt_config=make_mqtt_config(**m_overrides),
                settings=make_settings(**s_overrides),
            )

    return svc, fake_client


# ---------------------------------------------------------------------------
# Bridge helpers (shared across bridge test files)
# ---------------------------------------------------------------------------


def make_bridge_config(**overrides: Any) -> BridgeConfig:
    """Create a BridgeConfig with sensible test defaults."""
    defaults: dict[str, Any] = dict(
        mqtt=make_mqtt_config(client_id="test-bot-bridge"),
        http_timeout=5.0,
        task_poll_interval=0.5,
        task_poll_timeout=3.0,
        services={
            "robot": ServiceConfig(name="robot", base_url="http://robot:8110", watch_tasks=True),
        },
        status_heartbeat_interval=15.0,
        safety_gate_startup_grace=0.0,
        long_operations={},
    )
    defaults.update(overrides)
    return BridgeConfig(**defaults)


def make_mock_response(
    status_code: int = 200,
    body: Any = None,
    ok: bool | None = None,
) -> MagicMock:
    """Create a MagicMock pretending to be requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = ok if ok is not None else (200 <= status_code < 400)
    if body is None:
        body = {"detail": "ok"}
    resp.json.return_value = body
    resp.text = str(body)
    resp.headers = {"content-type": "application/json"}
    return resp


def make_mock_session(response: MagicMock | None = None) -> MagicMock:
    """Create a MagicMock pretending to be requests.Session."""
    session = MagicMock()
    if response is None:
        response = make_mock_response()
    session.request.return_value = response
    session.post.return_value = response
    session.get.return_value = response
    return session


def make_executor_deps(**overrides: Any) -> SimpleNamespace:
    """Create a SimpleNamespace satisfying BridgeServices protocol (executor subset)."""
    defaults = dict(
        auth_headers=MagicMock(return_value={}),
        send_response=MagicMock(),
        finish_command=MagicMock(),
        store_command_history=MagicMock(),
        publish_navigation_status=MagicMock(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_dispatcher_deps(**overrides: Any) -> SimpleNamespace:
    """Create a SimpleNamespace satisfying BridgeServices protocol."""
    defaults = dict(
        build_http_url=MagicMock(return_value="http://robot:8110/tasks/estop"),
        submit_http=MagicMock(),
        publish_command_error=MagicMock(),
        finish_command=MagicMock(),
        get_http_session=MagicMock(return_value=make_mock_session()),
        auth_headers=MagicMock(return_value={}),
        send_response=MagicMock(),
        store_command_history=MagicMock(),
        publish_navigation_status=MagicMock(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_topic_schema(robot_id: str = "test-bot") -> TopicSchema:
    """Create a TopicSchema for tests."""
    return TopicSchema(robot_id=robot_id)
