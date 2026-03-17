"""Command handler methods for MqttCommandBridge.

Handles navigateTo, goToCharging, cancel, and estop robot commands.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bridge_protocol import BridgeProtocol

logger = logging.getLogger(__name__)


class CommandHandlerMixin:
    """Navigate / goToCharging / cancel / estop command processing.

    Mixed into :class:`MqttCommandBridge` — all ``self.*`` references
    are typed via :class:`BridgeProtocol`.
    """

    def _handle_navigate_command(self: BridgeProtocol, command_id: str, data: dict[str, Any]) -> None:
        target_id = data.get("target_id")
        if not target_id:
            self._publish_command_error("navigateTo", command_id, "target_id is required.")
            self._finish_command(command_id)
            return

        headers = data.get("headers") or {}
        if not isinstance(headers, dict):
            self._publish_command_error("navigateTo", command_id, "headers must be an object.")
            self._finish_command(command_id)
            return

        url = self._build_http_url("robot", "/tasks/navigate")
        if not url:
            self._publish_command_error("navigateTo", command_id, "Robot service is not configured.")
            self._finish_command(command_id)
            return

        body = {
            "command_id": command_id,
            "target_id": target_id,
            "priority": data.get("priority", "normal"),
            "metadata": data.get("metadata"),
        }

        # Forward timestamp if it was validated
        if "_validated_timestamp" in data:
            body["timestamp"] = data["_validated_timestamp"]
            logger.debug(
                "Including timestamp in navigateTo command body: %s",
                data["_validated_timestamp"],
            )

        context = {
            "source": "command",
            "command_name": "navigateTo",
            "command_id": command_id,
            "target_id": target_id,
            "status_type": "navigation",
            "publish_navigation": True,
            "metadata": {
                "command_name": "navigateTo",
                "command_id": command_id,
                "target_id": target_id,
                "status_type": "navigation",
            },
        }

        self._submit_http(
            service="robot",
            request_id=command_id,
            method="POST",
            url=url,
            headers=headers,
            body=body,
            context=context,
        )

    def _handle_go_to_charging_command(self: BridgeProtocol, command_id: str, data: dict[str, Any]) -> None:
        station_id = data.get("station_id")
        if not station_id:
            self._publish_command_error("goToCharging", command_id, "station_id is required.")
            self._finish_command(command_id)
            return

        headers = data.get("headers") or {}
        if not isinstance(headers, dict):
            self._publish_command_error("goToCharging", command_id, "headers must be an object.")
            self._finish_command(command_id)
            return

        url = self._build_http_url("robot", "/tasks/go_to_charging_station")
        if not url:
            self._publish_command_error("goToCharging", command_id, "Robot service is not configured.")
            self._finish_command(command_id)
            return

        body = {
            "station_id": station_id,
        }

        context = {
            "source": "command",
            "command_name": "goToCharging",
            "command_id": command_id,
            "station_id": station_id,
            "status_type": "navigation",
            "publish_navigation": True,
            "metadata": {
                "command_name": "goToCharging",
                "command_id": command_id,
                "station_id": station_id,
                "status_type": "navigation",
            },
        }

        self._submit_http(
            service="robot",
            request_id=command_id,
            method="POST",
            url=url,
            headers=headers,
            body=body,
            context=context,
        )

    def _handle_cancel_command(self: BridgeProtocol, command_id: str, data: dict[str, Any]) -> None:
        task_id = data.get("task_id") or data.get("current_task_id")
        if not task_id:
            self._publish_command_error("cancel", command_id, "task_id is required for cancel command.")
            self._finish_command(command_id)
            return

        headers = data.get("headers") or {}
        if not isinstance(headers, dict):
            self._publish_command_error("cancel", command_id, "headers must be an object.")
            self._finish_command(command_id)
            return

        url = self._build_http_url("robot", "/tasks/cancel")
        if not url:
            self._publish_command_error("cancel", command_id, "Robot service is not configured.")
            self._finish_command(command_id)
            return

        body = {
            "command_id": command_id,
            "task_id": task_id,
            "reason": data.get("reason"),
        }

        # Forward timestamp if it was validated
        if "_validated_timestamp" in data:
            body["timestamp"] = data["_validated_timestamp"]
            logger.debug(
                "Including timestamp in cancel command body: %s",
                data["_validated_timestamp"],
            )

        metadata = {
            "command_name": "cancel",
            "command_id": command_id,
            "task_id": task_id,
            "status_type": "navigation",
        }
        context = {
            "source": "command",
            "command_name": "cancel",
            "command_id": command_id,
            "status_type": "navigation",
            "publish_navigation": True,
            "metadata": metadata,
        }

        self._submit_http(
            service="robot",
            request_id=command_id,
            method="POST",
            url=url,
            headers=headers,
            body=body,
            context=context,
        )

    def _handle_estop_command(self: BridgeProtocol, command_id: str, data: dict[str, Any]) -> None:
        headers = data.get("headers") or {}
        if not isinstance(headers, dict):
            self._publish_command_error("estop", command_id, "headers must be an object.")
            self._finish_command(command_id)
            return

        url = self._build_http_url("robot", "/tasks/estop")
        if not url:
            self._publish_command_error("estop", command_id, "Robot service is not configured.")
            self._finish_command(command_id)
            return

        body = {
            "command_id": command_id,
            "reason": data.get("reason"),
        }

        metadata = {
            "command_name": "estop",
            "command_id": command_id,
            "status_type": "navigation",
        }
        context = {
            "source": "command",
            "command_name": "estop",
            "command_id": command_id,
            "status_type": "navigation",
            "publish_navigation": True,
            "metadata": metadata,
        }

        self._submit_http(
            service="robot",
            request_id=command_id,
            method="POST",
            url=url,
            headers=headers,
            body=body,
            context=context,
        )
