import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, Any
import socket
import ssl
from fastapi.encoders import jsonable_encoder

try:
    # Lazy import: module may not be installed yet until requirements are updated
    from asyncio_mqtt import Client, MqttError
except Exception:  # pragma: no cover - handled at runtime if missing
    Client = None  # type: ignore
    MqttError = Exception  # type: ignore

try:
    import paho.mqtt.client as paho
except Exception:  # pragma: no cover
    paho = None  # type: ignore


_LOGGER = logging.getLogger(__name__)


@dataclass
class MqttConfig:
    broker_host: str
    broker_port: int = 1883
    frequency_hz: float = 1.0
    activate: bool = True
    topic: str = "robot_service/status"
    username: Optional[str] = None
    password: Optional[str] = None
    tls: bool = False
    tls_insecure: bool = False
    client_id: Optional[str] = None
    keepalive_seconds: int = 60


class MqttPublisher:
    """Background MQTT publisher for robot status."""

    def __init__(self) -> None:
        self._config: Optional[MqttConfig] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._status_provider: Optional[Callable[[], Awaitable[dict]]] = None
        self._client: Optional[Any] = None
        self._using_paho: bool = False

    def set_status_provider(self, provider: Callable[[], Awaitable[dict]]) -> None:
        self._status_provider = provider

    async def configure(self, config: MqttConfig) -> None:
        self._config = config
        if config.activate:
            await self.start()
        else:
            await self.stop()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        if not self._config:
            raise RuntimeError("MQTT config is not set")
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="mqtt-publisher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except Exception:
                pass
            self._task = None
        if self._client:
            try:
                if self._using_paho:
                    try:
                        self._client.loop_stop()
                    except Exception:
                        pass
                    try:
                        self._client.disconnect()
                    except Exception:
                        pass
                else:
                    await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def _ensure_connected(self) -> None:
        assert self._config is not None
        if self._client is not None:
            return
        try:
            # Basic DNS pre-check for clearer logs
            try:
                socket.getaddrinfo(self._config.broker_host, self._config.broker_port)
            except Exception as e:
                _LOGGER.warning("MQTT DNS/resolve failed for %s:%s: %s", self._config.broker_host, self._config.broker_port, e)
                raise

            # Prefer paho-mqtt (matches your working test). Fallback to asyncio-mqtt.
            if paho is not None:
                client_id = self._config.client_id or "robot-service"
                c = paho.Client(client_id=client_id)
                if self._config.username is not None:
                    c.username_pw_set(self._config.username, self._config.password or None)
                if self._config.tls:
                    context = ssl.create_default_context()
                    if self._config.tls_insecure:
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE
                    c.tls_set_context(context)
                # Optional: basic callbacks for debug
                def _on_connect(client, userdata, flags, rc):  # noqa: ANN001
                    if rc == 0:
                        _LOGGER.info("paho connected: rc=0")
                    else:
                        _LOGGER.warning("paho connect failed: rc=%s", rc)
                c.on_connect = _on_connect

                c.loop_start()
                c.connect(self._config.broker_host, self._config.broker_port, self._config.keepalive_seconds)
                self._client = c
                self._using_paho = True
                _LOGGER.info("Connected to MQTT broker %s:%s via paho", self._config.broker_host, self._config.broker_port)
                return

            if Client is None:
                raise RuntimeError("Neither paho-mqtt nor asyncio-mqtt is available")

            kwargs: dict[str, Any] = {
                "hostname": self._config.broker_host,
                "port": self._config.broker_port,
                "client_id": self._config.client_id or "robot-service",
                "keepalive": self._config.keepalive_seconds,
            }
            if self._config.username is not None:
                kwargs["username"] = self._config.username
            if self._config.password is not None:
                kwargs["password"] = self._config.password
            if self._config.tls:
                context = ssl.create_default_context()
                if self._config.tls_insecure:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                kwargs["tls_context"] = context

            self._client = Client(**kwargs)
            await self._client.connect()
            self._using_paho = False
            _LOGGER.info("Connected to MQTT broker %s:%s via asyncio-mqtt", self._config.broker_host, self._config.broker_port)
        except Exception as e:
            _LOGGER.warning("MQTT connect failed: %s", e)
            self._client = None
            raise

    async def _publish_once(self) -> None:
        assert self._config is not None
        if not self._status_provider:
            _LOGGER.debug("No status provider set; skipping publish")
            return
        try:
            status = await self._status_provider()
            payload = json.dumps(jsonable_encoder(status), ensure_ascii=False)
            if self._using_paho:
                assert self._client is not None
                self._client.publish(self._config.topic, payload)
            else:
                await self._client.publish(self._config.topic, payload)  # type: ignore[arg-type]
        except Exception as e:
            _LOGGER.warning("MQTT publish failed: %s", e)
            raise

    async def _run_loop(self) -> None:
        assert self._config is not None
        period_s = max(0.001, 1.0 / float(self._config.frequency_hz))
        while not self._stop_event.is_set():
            try:
                await self._ensure_connected()
                await self._publish_once()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=period_s)
                except asyncio.TimeoutError:
                    pass
            except Exception:
                # Backoff on any error
                try:
                    if self._client:
                        if self._using_paho:
                            try:
                                self._client.loop_stop()
                            except Exception:
                                pass
                            try:
                                self._client.disconnect()
                            except Exception:
                                pass
                        else:
                            await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass


