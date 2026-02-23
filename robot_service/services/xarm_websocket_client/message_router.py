import json
import time
import inspect
from typing import Callable, Dict, Any, Optional
from response_manager import ResponseManager


class MessageRouter:
    def __init__(self,
                 on_report: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_response: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_request: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_invalid: Optional[Callable[[Dict[str, Any]], None]] = None,
                 response_manager: Optional[ResponseManager] = None):
        self.on_report = on_report
        self.on_response = on_response
        self.on_request = on_request
        self.on_invalid = on_invalid
        self.response_manager = response_manager

        self.last_seq_id = -1
    async def _maybe_await(self, func, *args, **kwargs):
        if not func:
            return None
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def route(self, msg: Any):
        """Route incoming messages by type"""

        # Support raw list reports (xArm sometimes sends status arrays)
        if isinstance(msg, list):
            msg = {
                "type": "report",
                "cmd": "devices_status_report",
                "data": msg
            }

        # If not a dict -> invalid
        if not isinstance(msg, dict):
            await self._invalid({"reason": "not_dict", "raw": msg})
            return

        msg_type = msg.get("type")
        cmd = msg.get("cmd")

        # Basic validation
        if msg_type not in ("report", "response", "request"):
            await self._invalid({"reason": "bad_type", "msg": msg})
            return

        if msg_type != "response" and not cmd:
            await self._invalid({"reason": "missing_cmd", "msg": msg})
            return

        # Dispatch
        if msg_type == "report" and self.on_report:
            await self._maybe_await(self.on_report, msg)
        elif msg_type == "response":
            # resolve pending waiter first
            if self.response_manager:
                await self.response_manager.resolve(msg)
            if self.on_response:
                await self._maybe_await(self.on_response, msg)
        elif msg_type == "request" and self.on_request:
            await self._maybe_await(self.on_request, msg)

    async def _invalid(self, info: Dict[str, Any]):
        """Handle invalid messages"""
        if self.on_invalid:
            await self._maybe_await(self.on_invalid, info)
        else:
            print("Invalid message:", info)
