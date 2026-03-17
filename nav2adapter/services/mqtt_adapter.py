"""
MQTT adapter for AE.HUB communication.
"""
import asyncio
import json
import logging
import os
import random
import ssl
import time
from typing import Optional, Callable, Any, Awaitable, Dict

try:
    from aiomqtt import Client, MqttError
    from aiomqtt.exceptions import MqttCodeError
    AIOMQTT_AVAILABLE = True
except ImportError:
    AIOMQTT_AVAILABLE = False
    # Dummy classes for when aiomqtt is not available
    class Client:
        pass
    class MqttError(Exception):
        pass
    class MqttCodeError(Exception):
        pass

from app.config import settings
from services.reliability_metrics import reliability_metrics

_LOGGER = logging.getLogger(__name__)


class MqttUnavailableError(RuntimeError):
    """Raised when MQTT operations fail due to disconnection or unavailability."""
    pass


class MqttAdapter:
    """MQTT adapter for subscribing to commands and publishing status."""
    
    def __init__(
        self,
        robot_id: Optional[str] = None,
        broker_host: Optional[str] = None,
        broker_port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_tls: bool = False,
        client_id: Optional[str] = None
    ):
        if not AIOMQTT_AVAILABLE:
            raise ImportError(
                "aiomqtt is not installed. Install it with: pip install aiomqtt"
            )
        self.robot_id = robot_id or settings.robot_id
        self.broker_host = broker_host or settings.mqtt_broker_host
        # Port resolution: explicit arg > settings > auto-detect from TLS
        effective_tls = use_tls if broker_host else settings.mqtt_use_tls
        if broker_port is not None:
            self.broker_port = broker_port
        elif settings.mqtt_broker_port is not None:
            self.broker_port = settings.mqtt_broker_port
        else:
            # Auto-detect based on TLS
            self.broker_port = 8883 if effective_tls else 1883
        self.username = username or settings.mqtt_username
        self._password = password or settings.mqtt_password
        self.use_tls = effective_tls
        self.client_id = client_id or settings.mqtt_client_id or f"aehub_backend_{self.robot_id}"
        
        self.client: Optional[Client] = None
        self._connected = False
        # aiomqtt v2+ exposes a single client-wide messages queue, so we MUST have a single consumer.
        self._command_consumer_task: Optional[asyncio.Task] = None
        self._subscribe_tasks: list[asyncio.Task] = []
        self._connect_timeout_s: float = 8.0
        self._max_incoming_payload_bytes: int = 256 * 1024  # protect memory/logs
        # Rate limiting for disconnect logs
        self._last_disconnect_log_time: float = 0.0
        self._disconnect_log_interval: float = 10.0  # Log at most once per 10 seconds
    
    def _get_command_topic(self, command: str) -> str:
        """Get MQTT topic for a command."""
        return f"aroc/robot/{self.robot_id}/commands/{command}"
    
    @property
    def password(self) -> Optional[str]:
        """Backward-compatible accessor (private storage keeps it out of repr)."""
        return self._password

    def __repr__(self) -> str:
        return (
            f"MqttAdapter(broker={self.broker_host}:{self.broker_port}, "
            f"tls={self.use_tls}, robot_id={self.robot_id!r})"
        )

    def _get_status_topic(self, status_type: str) -> str:
        """Get MQTT topic for a status."""
        return f"aroc/robot/{self.robot_id}/status/{status_type}"

    def _get_event_topic(self, kind: str) -> str:
        """Get MQTT topic for events: ack/state/result."""
        # allow override via config
        return settings.mqtt_events_topic(kind)
    
    def _mark_disconnected(self, reason: str, exc: Exception | None = None) -> None:
        """
        Mark adapter as disconnected with rate-limited logging.
        
        Also closes the dead client so the reconnect loop creates a fresh one.
        
        Args:
            reason: Human-readable reason for disconnection
            exc: Optional exception that caused the disconnection
        """
        was_connected = self._connected
        self._connected = False
        
        # Clean up the dead client immediately so reconnect creates a fresh one
        old_client = self.client
        self.client = None
        if old_client is not None:
            try:
                # Schedule cleanup in a tracked task to avoid blocking
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    task = loop.create_task(self._safe_close_client(old_client))
                    self._subscribe_tasks.append(task)
                    task.add_done_callback(lambda t: self._subscribe_tasks.remove(t) if t in self._subscribe_tasks else None)
            except RuntimeError:
                pass  # No running loop (shutdown)
        
        if not was_connected:
            return  # Already logged
        
        # Rate-limited logging to avoid spam
        now = time.time()
        if now - self._last_disconnect_log_time >= self._disconnect_log_interval:
            if exc:
                _LOGGER.error("MQTT disconnected: %s (%s)", reason, str(exc))
            else:
                _LOGGER.error("MQTT disconnected: %s", reason)
            self._last_disconnect_log_time = now
    
    @staticmethod
    async def _safe_close_client(client: "Client") -> None:
        """Silently close a dead client, swallowing all errors."""
        try:
            await asyncio.wait_for(client.__aexit__(None, None, None), timeout=3.0)
        except Exception:
            pass

    async def connect(self) -> None:
        """Connect to MQTT broker."""
        if self._connected:
            return
        
        # Close any leftover dead client before creating a new one
        if self.client is not None:
            await self._safe_close_client(self.client)
            self.client = None
        
        if not self.broker_host:
            _LOGGER.warning("MQTT broker host not configured, skipping connection")
            return
        
        # Warn about TLS mismatch but don't silently override user config
        if self.broker_port == 8883 and not self.use_tls:
            _LOGGER.warning(
                "Port 8883 typically requires TLS but MQTT_USE_TLS is false. "
                "Set MQTT_USE_TLS=true if broker requires TLS."
            )
        
        try:
            reliability_metrics.inc("mqtt.connect.attempt")
            tls_ctx = None
            tls_insecure = False
            ca_cert_path = None  # Track CA cert path for fallback logic
            
            if self.use_tls:
                # Priority: 1) CA cert file, 2) TLS insecure flag, 3) default verification
                ca_cert_path = settings.mqtt_ca_cert
                if not ca_cert_path:
                    # Auto-detect ca.crt in project root
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    default_ca_path = os.path.join(project_root, "ca.crt")
                    if os.path.exists(default_ca_path):
                        ca_cert_path = default_ca_path
                        _LOGGER.info("Auto-detected CA certificate: %s", ca_cert_path)
                
                if ca_cert_path and os.path.exists(ca_cert_path):
                    # Use CA certificate for validation (preferred method)
                    _LOGGER.info("Using CA certificate for MQTT TLS: %s", ca_cert_path)
                    tls_ctx = ssl.create_default_context(cafile=ca_cert_path)
                    # Keep default verification (verify_mode=CERT_REQUIRED, check_hostname=True)
                elif settings.mqtt_tls_insecure:
                    # Fallback: disable verification if explicitly requested
                    _LOGGER.warning("MQTT_TLS_INSECURE=true: Disabling certificate verification (NOT RECOMMENDED for production)")
                    tls_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
                    tls_ctx.check_hostname = False
                    tls_ctx.verify_mode = ssl.CERT_NONE
                    tls_insecure = True
                else:
                    # Default: use system CA bundle (may fail with self-signed certs)
                    _LOGGER.info("Using system CA bundle for MQTT TLS verification")
                    tls_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
            
            base_kwargs: Dict[str, Any] = dict(
                hostname=self.broker_host,
                port=self.broker_port,
            )
            
            # Add TLS configuration
            if self.use_tls:
                if tls_ctx:
                    base_kwargs["tls_context"] = tls_ctx
                # Some aiomqtt versions support tls_insecure flag
                if tls_insecure:
                    try:
                        base_kwargs["tls_insecure"] = True
                    except TypeError:
                        # Older versions don't support this, rely on tls_context only
                        pass
            
            # Add credentials only if provided
            if self.username:
                base_kwargs["username"] = self.username
            if self._password:
                base_kwargs["password"] = self._password

            # aiomqtt API differs between versions. Try common parameter names.
            try:
                self.client = Client(**base_kwargs, client_id=self.client_id)  # type: ignore[arg-type]
            except TypeError:
                try:
                    self.client = Client(**base_kwargs, identifier=self.client_id)  # type: ignore[arg-type]
                except TypeError:
                    # Last resort: try without client_id
                    self.client = Client(**base_kwargs)  # type: ignore[arg-type]
            
            tls_info = f"TLS: {self.use_tls}"
            if self.use_tls and tls_ctx:
                tls_info += f", verify_mode={tls_ctx.verify_mode}, check_hostname={tls_ctx.check_hostname}"
            _LOGGER.info("Attempting to connect to MQTT broker at %s:%s (%s)", self.broker_host, self.broker_port, tls_info)
            
            # Try connection with current TLS settings
            try:
                # aiomqtt connect can hang on network issues; cap it.
                await asyncio.wait_for(self.client.__aenter__(), timeout=self._connect_timeout_s)
                self._connected = True
                reliability_metrics.inc("mqtt.connect.success")
                _LOGGER.info("Successfully connected to MQTT broker at %s:%s", self.broker_host, self.broker_port)
            except Exception as ssl_error:
                # If CA cert was used but failed, log clear error and DO NOT auto-fallback.
                # Auto-fallback to insecure mode is a MITM vector in production.
                # Users must explicitly set MQTT_TLS_INSECURE=true to disable verification.
                error_str = str(ssl_error).lower()
                if (ca_cert_path and os.path.exists(ca_cert_path) and 
                    not tls_insecure and 
                    ("certificate verify failed" in error_str or "ssl" in error_str or "cert" in error_str)):
                    _LOGGER.error(
                        "CA certificate validation failed: %s. "
                        "To disable verification, set MQTT_TLS_INSECURE=true explicitly. "
                        "Auto-fallback to insecure mode is disabled for security.",
                        ssl_error,
                    )
                    reliability_metrics.inc("mqtt.connect.tls_validation_failed")
                    self._connected = False
                    raise
                else:
                    # Re-raise if not an SSL/cert error or if already in insecure mode
                    raise
        except asyncio.TimeoutError:
            reliability_metrics.inc("mqtt.connect.failure")
            _LOGGER.error("MQTT connection timeout to %s:%s. Check if broker is running and accessible.", self.broker_host, self.broker_port)
            self._connected = False
            raise
        except ConnectionRefusedError:
            reliability_metrics.inc("mqtt.connect.failure")
            _LOGGER.error("MQTT broker refused connection at %s:%s. Check credentials and port.", self.broker_host, self.broker_port)
            self._connected = False
            raise
        except Exception as e:
            reliability_metrics.inc("mqtt.connect.failure")
            _LOGGER.error("Failed to connect to MQTT broker at %s:%s: %s", self.broker_host, self.broker_port, e)
            self._connected = False
            raise
    
    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if not self._connected or not self.client:
            return
        
        # Cancel consumer task first (it drains the shared message queue)
        if self._command_consumer_task:
            self._command_consumer_task.cancel()
            await asyncio.gather(self._command_consumer_task, return_exceptions=True)
            self._command_consumer_task = None

        # Cancel any remaining tasks created by this adapter
        for task in list(self._subscribe_tasks):
            task.cancel()
        if self._subscribe_tasks:
            await asyncio.gather(*self._subscribe_tasks, return_exceptions=True)
        self._subscribe_tasks.clear()
        
        try:
            await self.client.__aexit__(None, None, None)
            reliability_metrics.inc("mqtt.disconnect.success")
            _LOGGER.info("Disconnected from MQTT broker")
        except Exception as e:
            reliability_metrics.inc("mqtt.disconnect.failure")
            _LOGGER.error("Error disconnecting from MQTT broker: %s", e)
        finally:
            self._connected = False
            self.client = None
    
    def _decode_json_payload(self, payload: Any, *, topic: str) -> Optional[dict]:
        """Best-effort decode JSON payload with size/encoding protection."""
        if isinstance(payload, (bytes, bytearray, memoryview)):
            b = bytes(payload)
            if len(b) > self._max_incoming_payload_bytes:
                _LOGGER.warning("[MQTT IN] Payload too large on %s: %d bytes (drop)", topic, len(b))
                return None
            s = b.decode("utf-8", errors="replace")
        else:
            s = str(payload)
            if len(s.encode("utf-8", errors="ignore")) > self._max_incoming_payload_bytes:
                _LOGGER.warning("[MQTT IN] Payload too large on %s (drop)", topic)
                return None
        try:
            obj = json.loads(s)
        except Exception as e:
            _LOGGER.error("[MQTT IN] Invalid JSON on %s: %s", topic, str(e))
            _LOGGER.debug("[MQTT IN] Raw payload on %s (truncated): %s", topic, s[:500])
            return None
        return obj if isinstance(obj, dict) else {"value": obj}

    async def start_command_consumer(
        self,
        on_drive_to_position: Callable[[dict], Awaitable[Any]],
        on_cancel: Callable[[dict], Awaitable[Any]],
    ) -> None:
        """
        Start a single consumer for both command topics.

        This uses a single `client.messages` iterator for all subscriptions.
        The consumer includes automatic reconnection logic.
        """
        if self._command_consumer_task and not self._command_consumer_task.done():
            _LOGGER.info("Command consumer already running")
            return

        nav_topic = self._get_command_topic("driveToPosition")
        cancel_topic = self._get_command_topic("cancel")

        async def message_handler():
            """Message handler with automatic reconnection loop.
            
            Never gives up — retries forever with exponential backoff
            capped at max_backoff seconds.  After a successful reconnect
            the backoff resets to 1 s so transient blips recover fast.
            """
            max_backoff = 30.0  # Cap reconnect delay (was 60)
            backoff = 1.0
            consecutive_failures = 0
            
            while True:
                try:
                    # ── reconnect ──────────────────────────────────
                    if not self._connected or not self.client:
                        try:
                            reliability_metrics.inc("mqtt.reconnect.loop_attempt")
                            await self.connect()
                            backoff = 1.0
                            if consecutive_failures > 0:
                                _LOGGER.info(
                                    "MQTT reconnected after %d failed attempt(s)",
                                    consecutive_failures,
                                )
                            consecutive_failures = 0
                        except Exception as e:
                            consecutive_failures += 1
                            reliability_metrics.inc("mqtt.reconnect.loop_failure")
                            # Log every attempt for the first 5, then every 10th
                            if consecutive_failures <= 5 or consecutive_failures % 10 == 0:
                                _LOGGER.warning(
                                    "MQTT reconnect attempt %d failed: %s "
                                    "(retrying in %.1fs)",
                                    consecutive_failures, e, backoff,
                                )
                            await asyncio.sleep(backoff * random.uniform(0.75, 1.25))
                            backoff = min(backoff * 2, max_backoff)
                    
                    # ── subscribe ──────────────────────────────────
                    try:
                        await self.client.subscribe(nav_topic)
                        await self.client.subscribe(cancel_topic)
                        _LOGGER.info("Subscribed to %s and %s", nav_topic, cancel_topic)
                    except Exception as e:
                        _LOGGER.error("Failed to subscribe to MQTT topics: %s", e)
                        self._mark_disconnected("subscription failed", e)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, max_backoff)
                        continue

                    # ── message loop ──────────────────────────────
                    try:
                        async for message in self.client.messages:
                            topic_obj = getattr(message, "topic", None)
                            topic_str = str(topic_obj) if topic_obj is not None else ""

                            payload = self._decode_json_payload(
                                getattr(message, "payload", None), topic=topic_str,
                            )
                            if payload is None:
                                continue

                            _LOGGER.info("[MQTT IN] %s", topic_str)
                            _LOGGER.debug("[MQTT IN] %s -> %s", topic_str, str(payload)[:1000])

                            try:
                                if topic_str.endswith("/driveToPosition"):
                                    await on_drive_to_position(payload)
                                elif topic_str.endswith("/cancel"):
                                    await on_cancel(payload)
                                else:
                                    _LOGGER.warning("[MQTT IN] Unknown topic: %s", topic_str)
                            except Exception as e:
                                _LOGGER.error("Error handling command %s: %s", topic_str, e, exc_info=True)
                    except (MqttError, MqttCodeError, OSError) as e:
                        # Connection lost during message iteration — normal on WiFi
                        self._mark_disconnected("disconnected during message iteration", e)
                        # Start with short backoff — connection may come back fast
                        backoff = 1.0
                        await asyncio.sleep(backoff)
                        continue
                    except asyncio.CancelledError:
                        _LOGGER.info("Command consumer cancelled")
                        raise

                except asyncio.CancelledError:
                    _LOGGER.info("Command consumer cancelled")
                    raise
                except Exception as e:
                    _LOGGER.error("Error in command consumer: %s", e, exc_info=True)
                    self._mark_disconnected("unexpected error in consumer", e)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)

        self._command_consumer_task = asyncio.create_task(message_handler())
        # P3-14: prune finished tasks before appending to avoid unbounded growth.
        self._subscribe_tasks = [t for t in self._subscribe_tasks if not t.done()]
        self._subscribe_tasks.append(self._command_consumer_task)
    
    async def publish_navigation_status(self, status: dict) -> None:
        """
        Publish navigation status.
        
        Args:
            status: Navigation status dictionary
        """
        if not self._connected or not self.client:
            reliability_metrics.inc("mqtt.publish.navigation.skipped_disconnected")
            return  # Silently skip telemetry when disconnected
        
        topic = self._get_status_topic("navigation")
        payload = json.dumps(status, ensure_ascii=False)
        started = time.perf_counter()
        
        try:
            # QoS=0 is typically fine for telemetry; keep it default.
            try:
                await self.client.publish(topic, payload, qos=0)
            except TypeError:
                await self.client.publish(topic, payload)
            reliability_metrics.inc("mqtt.publish.navigation.success")
            reliability_metrics.observe_duration(
                "mqtt.publish.navigation.latency_s",
                time.perf_counter() - started,
            )
            _LOGGER.debug("[MQTT OUT] %s -> %s", topic, payload)
        except (MqttCodeError, OSError) as e:
            # Connection lost - mark disconnected and silently return (telemetry can be lost)
            reliability_metrics.inc("mqtt.publish.navigation.failure")
            self._mark_disconnected("publish navigation status failed", e)
            return
        except Exception as e:
            # Other errors - log once but don't spam
            reliability_metrics.inc("mqtt.publish.navigation.failure")
            self._mark_disconnected("unexpected error publishing navigation status", e)
            return
    
    async def publish_position_status(self, position: dict) -> None:
        """
        Publish position status.
        
        Args:
            position: Position status dictionary
        """
        if not self._connected or not self.client:
            reliability_metrics.inc("mqtt.publish.position.skipped_disconnected")
            return  # Silently skip telemetry when disconnected
        
        topic = self._get_status_topic("position")
        payload = json.dumps(position, ensure_ascii=False)
        started = time.perf_counter()
        
        try:
            try:
                await self.client.publish(topic, payload, qos=0)
            except TypeError:
                await self.client.publish(topic, payload)
            reliability_metrics.inc("mqtt.publish.position.success")
            reliability_metrics.observe_duration(
                "mqtt.publish.position.latency_s",
                time.perf_counter() - started,
            )
            _LOGGER.debug("[MQTT OUT] %s -> %s", topic, payload)
        except (MqttCodeError, OSError) as e:
            # Connection lost - mark disconnected and silently return (telemetry can be lost)
            reliability_metrics.inc("mqtt.publish.position.failure")
            self._mark_disconnected("publish position status failed", e)
            return
        except Exception as e:
            # Other errors - log once but don't spam
            reliability_metrics.inc("mqtt.publish.position.failure")
            self._mark_disconnected("unexpected error publishing position status", e)
            return

    async def publish_safety_state(self, safety_state: dict) -> None:
        """Publish safety state with reliable delivery (QoS=1, retained)."""
        if not self._connected or not self.client:
            reliability_metrics.inc("mqtt.publish.safety.skipped_disconnected")
            return

        topic = self._get_status_topic("safety")
        payload = json.dumps(safety_state, ensure_ascii=False)
        started = time.perf_counter()

        try:
            try:
                await self.client.publish(topic, payload, qos=1, retain=True)
            except TypeError:
                await self.client.publish(topic, payload)
            reliability_metrics.inc("mqtt.publish.safety.success")
            reliability_metrics.observe_duration(
                "mqtt.publish.safety.latency_s",
                time.perf_counter() - started,
            )
            _LOGGER.debug("[MQTT OUT] %s -> %s", topic, payload)
        except (MqttCodeError, OSError) as e:
            reliability_metrics.inc("mqtt.publish.safety.failure")
            self._mark_disconnected("publish safety state failed", e)
            return
        except Exception as e:
            reliability_metrics.inc("mqtt.publish.safety.failure")
            self._mark_disconnected("unexpected error publishing safety state", e)
            return

    async def publish_event(self, kind: str, event: dict) -> None:
        """Publish an ack/state/result event."""
        if not self._connected or not self.client:
            reliability_metrics.inc("mqtt.publish.event.skipped_disconnected")
            return  # Silently skip events when disconnected
        
        topic = self._get_event_topic(kind)
        payload = json.dumps(event, ensure_ascii=False)
        started = time.perf_counter()
        try:
            # Events are more important than telemetry; prefer QoS=1 when supported.
            try:
                await self.client.publish(topic, payload, qos=1)
            except TypeError:
                await self.client.publish(topic, payload)
            reliability_metrics.inc("mqtt.publish.event.success")
            reliability_metrics.observe_duration(
                "mqtt.publish.event.latency_s",
                time.perf_counter() - started,
            )
            _LOGGER.debug("[MQTT OUT] %s -> %s", topic, payload)
        except (MqttCodeError, OSError) as e:
            # Connection lost - mark disconnected and silently return (events can be lost)
            reliability_metrics.inc("mqtt.publish.event.failure")
            self._mark_disconnected("publish event failed", e)
            return
        except Exception as e:
            # Other errors - log once but don't spam
            reliability_metrics.inc("mqtt.publish.event.failure")
            self._mark_disconnected("unexpected error publishing event", e)
            return

    async def publish_command(self, command: str, payload_obj: dict) -> None:
        """Publish an inbound-style command (driveToPosition/cancel) to the broker."""
        if not self._connected or not self.client:
            reliability_metrics.inc("mqtt.publish.command.skipped_disconnected")
            raise MqttUnavailableError("MQTT client not connected")
        
        topic = self._get_command_topic(command)
        payload = json.dumps(payload_obj, ensure_ascii=False)
        started = time.perf_counter()
        
        try:
            # Commands should be delivered reliably; prefer QoS=1 when supported.
            try:
                await self.client.publish(topic, payload, qos=1)
            except TypeError:
                await self.client.publish(topic, payload)
            reliability_metrics.inc("mqtt.publish.command.success")
            reliability_metrics.observe_duration(
                "mqtt.publish.command.latency_s",
                time.perf_counter() - started,
            )
            _LOGGER.debug("[MQTT OUT] %s -> %s", topic, payload)
        except MqttCodeError as e:
            # rc=4: not connected
            reliability_metrics.inc("mqtt.publish.command.failure")
            self._mark_disconnected("publish_command rc error", e)
            raise MqttUnavailableError("MQTT publish failed (not connected)") from e
        except OSError as e:
            # WinError 10054, etc.
            reliability_metrics.inc("mqtt.publish.command.failure")
            self._mark_disconnected("socket error during publish", e)
            raise MqttUnavailableError("MQTT publish failed (socket error)") from e
        except Exception as e:
            reliability_metrics.inc("mqtt.publish.command.failure")
            _LOGGER.error("Failed to publish command: %s", e, exc_info=True)
            self._mark_disconnected("unexpected error publishing command", e)
            raise MqttUnavailableError(f"MQTT publish failed: {e}") from e
    
    @property
    def is_connected(self) -> bool:
        """Check if MQTT client is connected."""
        return self._connected
