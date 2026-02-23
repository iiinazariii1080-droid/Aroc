"""Application-layer facade for navigation commands.

Why this exists
---------------
Historically, HTTP routes published to MQTT directly. That creates several issues:
- Routes become stateful and harder to test.
- Any MQTT transient error turns into a 500 if not caught perfectly.
- There is no single place to implement delivery policies (MQTT vs local), idempotency,
  and error mapping.

This facade keeps routes thin and provides an explicit API surface.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from domain.models import NavigationCommand

try:
    # aiomqtt is optional depending on environment
    from aiomqtt.exceptions import MqttCodeError  # type: ignore
except Exception:  # pragma: no cover
    class MqttCodeError(Exception):
        pass

from services.mqtt_adapter import MqttAdapter, MqttUnavailableError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandSendResult:
    topic: str
    payload: Dict[str, Any]
    delivery: str  # "mqtt" | "local"


class NavigationFacade:
    """Application-layer command facade.

    The facade does not perform long-polling, transport watching, or caching.
    Those are owned by lower layers (CommandHandler / StatusPublisher).
    """

    def __init__(
        self,
        mqtt_adapter: Optional[MqttAdapter],
        *,
        command_handler: Optional[Any] = None,
        allow_direct_http_commands: bool = False,
    ) -> None:
        self._mqtt = mqtt_adapter
        self._command_handler = command_handler
        self._allow_direct = bool(allow_direct_http_commands)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def send_navigate_to(
        self,
        *,
        target_id: str,
        command_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> CommandSendResult:
        cid = command_id or str(uuid.uuid4())
        ts = timestamp or self._now_iso()
        payload = {"command_id": cid, "timestamp": ts, "target_id": target_id}

        # Prefer MQTT (the official integration path).
        if self._mqtt is not None and getattr(self._mqtt, "is_connected", False):
            try:
                await self._mqtt.publish_command("navigateTo", payload)
                topic = f"aroc/robot/{settings.robot_id}/commands/navigateTo"
                return CommandSendResult(topic=topic, payload=payload, delivery="mqtt")
            except (MqttUnavailableError, MqttCodeError, OSError) as e:
                raise MqttUnavailableError(str(e))
            except Exception as e:  # defensive: never leak as HTTP 500
                raise MqttUnavailableError(f"publish_failed:{e}")

        # Optional local mode (tests / dev without MQTT broker).
        if self._allow_direct and self._command_handler is not None:
            cmd = NavigationCommand(**payload)
            await self._command_handler.handle_navigate_to(cmd)
            return CommandSendResult(topic="local", payload=payload, delivery="local")

        raise MqttUnavailableError("MQTT adapter not connected")

    async def send_cancel(
        self,
        *,
        command_id: str,
        timestamp: Optional[str] = None,
    ) -> CommandSendResult:
        ts = timestamp or self._now_iso()
        payload = {"command_id": command_id, "timestamp": ts}

        if self._mqtt is not None and getattr(self._mqtt, "is_connected", False):
            try:
                await self._mqtt.publish_command("cancel", payload)
                topic = f"aroc/robot/{settings.robot_id}/commands/cancel"
                return CommandSendResult(topic=topic, payload=payload, delivery="mqtt")
            except (MqttUnavailableError, MqttCodeError, OSError) as e:
                raise MqttUnavailableError(str(e))
            except Exception as e:
                raise MqttUnavailableError(f"publish_failed:{e}")

        if self._allow_direct and self._command_handler is not None:
            await self._command_handler.handle_cancel(command_id)
            return CommandSendResult(topic="local", payload=payload, delivery="local")

        raise MqttUnavailableError("MQTT adapter not connected")
