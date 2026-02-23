"""MQTT client types and configuration data classes."""
from dataclasses import dataclass
from enum import Enum


class MQTTConnectionState(Enum):
    """MQTT connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class MQTTClientConfig:
    """Configuration for MQTT client."""
    broker: str
    port: int
    username: str | None
    password: str | None
    client_id: str
    use_tls: bool
    ca_certs: str | None
    certfile: str | None
    keyfile: str | None
    tls_insecure: bool
    keepalive: int = 30
    qos: int = 1
