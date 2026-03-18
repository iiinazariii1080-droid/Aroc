"""End-to-end integration tests with real component instances.

Real: CommandDispatcher, HttpExecutor, PendingResultQueue, BridgeResponsePublisher,
      MQTTResponsePublisher, CommandDeduplicator, TaskWatcher.
Mocked: HTTP transport (requests.Session), MQTT transport (FakeMQTTClient).

No time.sleep() — all sync via threading.Event and join_estop_threads().
"""

import threading
from typing import Any
from unittest.mock import MagicMock, patch

from command_dedup import CommandDeduplicator
from command_dispatcher import CommandDispatcher
from http_executor import HttpExecutor
from mqtt_publisher import MQTTResponsePublisher
from pending_results import PendingResultQueue
from response_publisher import BridgeResponsePublisher
from task_watcher import TaskWatcher

from shared.config_types import BridgeConfig, ServiceConfig, TopicSchema
from tests.conftest import make_bridge_config, make_mock_response, make_mock_session


class FakeMQTTClient:
    """Functional MQTT fake with in-memory pub/sub and connectivity control."""

    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self.published: list[tuple[str, str, int]] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False) -> bool:
        if not self._connected:
            return False
        self.published.append((topic, payload, qos))
        return True

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False


def _wire_components(
    *,
    mock_session: MagicMock | None = None,
    connected: bool = True,
    config: BridgeConfig | None = None,
) -> dict[str, Any]:
    """Wire all real components together, mocking only HTTP and MQTT transport."""
    shutdown = threading.Event()
    cfg = config or make_bridge_config()

    # MQTT fake
    mqtt_client = FakeMQTTClient(connected=connected)

    # Real components
    dedup = CommandDeduplicator()
    service_dedup = CommandDeduplicator()

    # MQTTResponsePublisher (real, uses fake MQTT client)
    low_level_publisher = MQTTResponsePublisher(mqtt_client=mqtt_client, config=cfg)

    topics = TopicSchema(robot_id=cfg.robot_id)

    # BridgeResponsePublisher (real)
    resp_publisher = BridgeResponsePublisher(
        publisher=low_level_publisher,
        mqtt_client=mqtt_client,
        get_topics_fn=lambda: topics,
        get_robot_id_fn=lambda: cfg.robot_id,
        extract_service_fn=lambda topic: topics.parse_service(topic),
    )

    # PendingResultQueue (real)
    pending_queue = PendingResultQueue(
        shutdown_event=shutdown,
        publish_json_fn=resp_publisher.publish_json,
        get_resp_topic_fn=lambda svc: f"{topics.resp_base}/{svc}",
        mqtt_is_connected_fn=lambda: mqtt_client.is_connected,
    )

    # Wire pending queue
    resp_publisher.set_pending_queue(pending_queue.queue)

    # TaskWatcher (real, but we don't exercise it deeply in these tests)
    watcher_deps = MagicMock()
    watcher_deps.get_http_session = MagicMock()
    watcher_deps.auth_headers = MagicMock(return_value={})
    watcher_deps.send_response = resp_publisher.send_response
    watcher_deps.publish_navigation_status = resp_publisher.publish_navigation_status
    watcher_deps.store_command_history = dedup.store
    task_watcher = TaskWatcher(
        task_poll_interval=cfg.task_poll_interval,
        task_poll_timeout=cfg.task_poll_timeout,
        http_timeout=cfg.http_timeout,
        shutdown_event=shutdown,
        deps=watcher_deps,
    )

    # HttpExecutor deps adapter (real routing)
    class RealExecutorDeps:
        def auth_headers(self) -> dict[str, str]:
            return {}

        def send_response(self, service: str, payload: dict[str, Any]) -> None:
            resp_publisher.send_response(service, payload)

        def finish_command(self, command_id: str | None) -> None:
            dedup.finish(command_id)

        def store_command_history(self, command_id: str, payload: dict[str, Any]) -> None:
            dedup.store(command_id, payload)

        def publish_navigation_status(
            self, state: str, success: bool | None, detail: Any, context: dict[str, Any] | None
        ) -> None:
            resp_publisher.publish_navigation_status(state, success, detail, context)

    if mock_session is None:
        mock_session = make_mock_session()

    # Patch requests.Session so both constructor AND get_http_session() return mock
    session_patcher = patch("http_executor.requests.Session", return_value=mock_session)
    session_patcher.start()

    http_executor = HttpExecutor(
        config=cfg,
        shutdown_event=shutdown,
        deps=RealExecutorDeps(),
        service_dedup=service_dedup,
        task_watcher=task_watcher,
    )

    # Keep patcher reference for cleanup
    # (tests must call session_patcher.stop() after shutdown)

    # CommandDispatcher deps adapter (real routing)
    from path_validator import build_http_url, compute_allowed_hosts
    allowed_hosts = compute_allowed_hosts(cfg.services)

    class RealDispatcherDeps:
        def get_http_session(self) -> Any:
            return http_executor.get_http_session()

        def auth_headers(self) -> dict[str, str]:
            return {}

        def send_response(self, service: str, payload: dict[str, Any]) -> None:
            resp_publisher.send_response(service, payload)

        def publish_navigation_status(
            self, state: str, success: bool | None, detail: Any, context: dict[str, Any] | None
        ) -> None:
            resp_publisher.publish_navigation_status(state, success, detail, context)

        def store_command_history(self, command_id: str, payload: dict[str, Any]) -> None:
            dedup.store(command_id, payload)

        def finish_command(self, command_id: str | None) -> None:
            dedup.finish(command_id)

        def build_http_url(self, service: str, path: str) -> str | None:
            return build_http_url(service, path, cfg.services, allowed_hosts)

        def submit_http(self, **kwargs: Any) -> None:
            http_executor.submit(**kwargs)

        def publish_command_error(self, cmd: str, cid: str | None, msg: str) -> None:
            resp_publisher.publish_command_error(cmd, cid, msg)

    cmd_dispatcher = CommandDispatcher(deps=RealDispatcherDeps())
    cmd_dispatcher.set_shutdown_event(shutdown)

    return {
        "shutdown": shutdown,
        "config": cfg,
        "mqtt_client": mqtt_client,
        "dedup": dedup,
        "resp_publisher": resp_publisher,
        "pending_queue": pending_queue,
        "http_executor": http_executor,
        "cmd_dispatcher": cmd_dispatcher,
        "mock_session": mock_session,
        "topics": topics,
        "_session_patcher": session_patcher,
    }


class TestE2ECommandFlow:
    """End-to-end tests with real component instances."""

    def test_navigate_command_full_flow(self):
        """navigateTo → HTTP POST → ack published to MQTT with correct topic and payload."""
        mock_response = make_mock_response(200, {"task_id": "task-1"})
        mock_session = make_mock_session(mock_response)
        ctx = _wire_components(mock_session=mock_session)

        done = threading.Event()
        original_finish = ctx["dedup"].finish

        def finish_and_signal(cid):
            original_finish(cid)
            if cid == "cmd-nav-1":
                done.set()

        # Temporarily hook finish to signal completion
        ctx["http_executor"]._deps.finish_command = finish_and_signal

        ctx["cmd_dispatcher"].dispatch(
            "navigateto",
            "cmd-nav-1",
            {"target_id": "station-A"},
        )

        done.wait(timeout=5)

        # Verify MQTT publish happened
        assert len(ctx["mqtt_client"].published) >= 1
        # Find the response message (on resp topic)
        resp_messages = [
            (t, p) for t, p, q in ctx["mqtt_client"].published
            if "/resp/" in t
        ]
        assert len(resp_messages) >= 1
        topic, payload_str = resp_messages[0]
        assert topic == f"aroc/robot/{ctx['config'].robot_id}/resp/robot"

        import json
        payload = json.loads(payload_str)
        assert payload["success"] is True
        assert payload["status_code"] == 200
        assert payload["request_id"] == "cmd-nav-1"
        assert payload["service"] == "robot"

        ctx["shutdown"].set()
        ctx["http_executor"].shutdown()
        ctx["_session_patcher"].stop()

    def test_navigate_http_failure_publishes_error(self):
        """HTTP failure → error ack published to MQTT."""
        mock_response = make_mock_response(500, {"detail": "Internal server error"})
        mock_session = make_mock_session(mock_response)
        ctx = _wire_components(mock_session=mock_session)

        done = threading.Event()
        ctx["http_executor"]._deps.finish_command = lambda cid: done.set() if cid else None

        ctx["cmd_dispatcher"].dispatch(
            "navigateto",
            "cmd-fail-1",
            {"target_id": "station-B"},
        )

        done.wait(timeout=5)

        resp_messages = [
            (t, p) for t, p, q in ctx["mqtt_client"].published
            if "/resp/" in t
        ]
        assert len(resp_messages) >= 1

        import json
        payload = json.loads(resp_messages[0][1])
        assert payload["success"] is False
        assert payload["status_code"] == 500
        assert payload["error"]["type"] == "http_error"

        ctx["shutdown"].set()
        ctx["http_executor"].shutdown()
        ctx["_session_patcher"].stop()

    def test_estop_full_flow(self):
        """E-stop → dedicated thread → HTTP POST → ack published to MQTT."""
        mock_response = make_mock_response(200, {})
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_session.close = MagicMock()

        ctx = _wire_components(mock_session=mock_session)

        with patch("command_dispatcher.requests.Session", return_value=mock_session):
            ctx["cmd_dispatcher"].handle_estop("cmd-estop-1", {"reason": "emergency"})
            ctx["cmd_dispatcher"].join_estop_threads(timeout=5)

        resp_messages = [
            (t, p) for t, p, q in ctx["mqtt_client"].published
            if "/resp/" in t
        ]
        assert len(resp_messages) >= 1

        import json
        payload = json.loads(resp_messages[0][1])
        assert payload["success"] is True
        assert payload["command_id"] == "cmd-estop-1"
        assert payload["command_name"] == "estop"

        ctx["shutdown"].set()
        ctx["http_executor"].shutdown()
        ctx["_session_patcher"].stop()

    def test_mqtt_disconnect_queues_then_flush_delivers(self):
        """MQTT disconnect → result queued → reconnect → flush delivers."""
        mock_response = make_mock_response(200, {"detail": "ok"})
        mock_session = make_mock_session(mock_response)
        ctx = _wire_components(mock_session=mock_session, connected=False)

        done = threading.Event()
        ctx["http_executor"]._deps.finish_command = lambda cid: done.set() if cid else None

        ctx["cmd_dispatcher"].dispatch(
            "navigateto",
            "cmd-q-1",
            {"target_id": "station-C"},
        )

        done.wait(timeout=5)

        # MQTT is disconnected — nothing published yet
        assert len(ctx["mqtt_client"].published) == 0
        # But result should be in pending queue
        assert ctx["pending_queue"].has_pending

        # Reconnect MQTT and flush
        ctx["mqtt_client"].connect()
        ctx["pending_queue"].flush()

        # Now the result should be published
        assert len(ctx["mqtt_client"].published) >= 1

        import json
        resp_messages = [
            (t, p) for t, p, q in ctx["mqtt_client"].published
            if "/resp/" in t
        ]
        assert len(resp_messages) >= 1
        payload = json.loads(resp_messages[0][1])
        assert payload["success"] is True
        assert payload["request_id"] == "cmd-q-1"

        ctx["shutdown"].set()
        ctx["http_executor"].shutdown()
        ctx["_session_patcher"].stop()
