"""
Shared configuration dataclasses and protocols.

Used by all microservices (config-api, hub-auth, mqtt-bridge, mqtt-telemetry).
"""


from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


class AuthMode(StrEnum):
    """MQTT authentication modes for L0→L2 migration."""

    PASSWORD = "password"  # L0: username/password only
    MTLS = "mtls"  # L2: client certificate only
    MTLS_PASSWORD = "mtls_password"  # Transition: both


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    base_url: str
    watch_tasks: bool = False
    # Allowlist of permitted path prefixes. Empty tuple = all paths allowed (legacy).
    allowed_path_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class HubAuthSettings:
    base_url: str
    robot_id: str | None
    api_key: str | None
    refresh_margin: float = 60.0
    timeout: float = 5.0


@dataclass(frozen=True)
class MQTTConnectionConfig:
    """Shared MQTT connection parameters used by all MQTT-connected services."""

    broker: str
    broker_port: int
    mqtt_user: str
    mqtt_password: str
    robot_id: str
    client_id: str
    mqtt_publish_qos: int = 1
    mqtt_use_tls: bool = False
    mqtt_ca_certs: str | None = None
    mqtt_certfile: str | None = None
    mqtt_keyfile: str | None = None
    mqtt_tls_insecure: bool = False
    auth_mode: str = "password"

    def __repr__(self) -> str:
        return (
            f"MQTTConnectionConfig(broker={self.broker!r}, broker_port={self.broker_port}, "
            f"mqtt_user='***', mqtt_password='***', "
            f"robot_id={self.robot_id!r}, client_id={self.client_id!r}, "
            f"mqtt_use_tls={self.mqtt_use_tls})"
        )

    def topics(self) -> "TopicSchema":
        return TopicSchema(robot_id=self.robot_id)


@dataclass(frozen=True)
class BridgeConfig:
    """Bridge-specific configuration, embedding MQTTConnectionConfig for MQTT settings."""

    mqtt: MQTTConnectionConfig
    http_timeout: float
    task_poll_interval: float
    task_poll_timeout: float
    services: Mapping[str, ServiceConfig] = field(default_factory=lambda: MappingProxyType({}))
    status_heartbeat_interval: float = 15.0
    safety_gate_startup_grace: float = 30.0
    long_operations: Mapping[str, Mapping[str, float]] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def robot_id(self) -> str:
        return self.mqtt.robot_id

    @property
    def mqtt_publish_qos(self) -> int:
        return self.mqtt.mqtt_publish_qos

    def __repr__(self) -> str:
        return f"BridgeConfig(mqtt={self.mqtt!r})"


class AuthManagerProtocol(Protocol):
    """Structural interface for the authentication manager.

    Any object passed as ``auth_manager`` to the bridge must satisfy
    this protocol.  Defined here (shared) so the contract lives at the
    service boundary, not inside a single consumer.
    """

    def auth_headers(self) -> dict[str, str]: ...
    def robot_id(self) -> str | None: ...
    def has_valid_token(self) -> bool: ...
    def close(self) -> None: ...


class BridgeServices(Protocol):
    """Unified dependency contract for bridge components.

    Provides HTTP session management, response publishing, command
    lifecycle, and HTTP dispatching to CommandDispatcher, TaskWatcher,
    and HttpExecutor via a single adapter.
    """

    # HTTP session & auth
    def get_http_session(self) -> Any: ...
    def auth_headers(self) -> dict[str, str]: ...

    # Response publishing
    def send_response(self, service: str, payload: dict[str, Any]) -> None: ...
    def publish_navigation_status(
        self, state: str, success: bool | None, detail: Any, context: dict[str, Any] | None,
    ) -> None: ...

    # Command lifecycle
    def store_command_history(self, command_id: str, payload: dict[str, Any]) -> None: ...
    def finish_command(self, command_id: str | None) -> None: ...

    # HTTP dispatching (used by CommandDispatcher)
    def build_http_url(self, service: str, path: str) -> str | None: ...
    def submit_http(self, **kwargs: Any) -> None: ...
    def publish_command_error(self, cmd: str, cid: str | None, msg: str) -> None: ...


class TopicSchema:
    """Single source of truth for all MQTT topic paths for a given robot."""

    __slots__ = ("robot_id",)

    # Topic structure: aroc/robot/{robot_id}/{kind}/{name}
    #                  [0]   [1]     [2]       [3]    [4]
    _KIND_INDEX = 3
    _NAME_INDEX = 4

    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id

    @property
    def command_pattern(self) -> str:
        return f"aroc/robot/{self.robot_id}/commands/+"

    @property
    def resp_base(self) -> str:
        return f"aroc/robot/{self.robot_id}/resp"

    @property
    def status_base(self) -> str:
        return f"aroc/robot/{self.robot_id}/status"

    def status(self, name: str) -> str:
        return f"aroc/robot/{self.robot_id}/status/{name}"

    @property
    def navigation_status(self) -> str:
        return f"aroc/robot/{self.robot_id}/status/navigation"

    @property
    def system_status(self) -> str:
        return f"aroc/robot/{self.robot_id}/status/system"

    @property
    def connection_status(self) -> str:
        return f"aroc/robot/{self.robot_id}/status/connection"

    @property
    def safety_status(self) -> str:
        return f"aroc/robot/{self.robot_id}/status/safety"

    @property
    def telemetry(self) -> str:
        return f"aroc/robot/{self.robot_id}/telemetry"

    def parse_service(self, topic: str) -> str | None:
        """Extract the service/command name from a topic (position 4)."""
        parts = topic.split("/")
        return parts[self._NAME_INDEX] if len(parts) > self._NAME_INDEX else None

    def parse_command(self, topic: str) -> str | None:
        """Extract command name if topic is a 'commands' or legacy 'cmd' topic, else None."""
        parts = topic.split("/")
        if len(parts) > self._NAME_INDEX and parts[self._KIND_INDEX] in ("commands", "cmd"):
            return parts[self._NAME_INDEX]
        return None
