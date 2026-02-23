import time
import asyncio
from typing import Dict, Any, Callable, Optional
from response_manager import ResponseManager
import logging


class ManipulatorCommands:
    def __init__(self, sender: Callable[[Dict[str, Any]], None], user_id="test", version="xarm6", stop_cmd_name: str = "emergency_stop", response_manager: Optional[ResponseManager] = None):
        """
        :param sender: function that actually sends the payload (usually ConnectionManager.send)
        :param user_id: user ID
        :param version: manipulator version
        """
        self.send = sender
        self.user_id = user_id
        self.version = version
        self.seq = 0  # id counter
        self.stop_cmd_name = stop_cmd_name
        self.responses = response_manager

    def _build(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Build standard packet"""
        payload = {
            "id": str(self.seq),
            "type": "request",
            "cmd": cmd,
            "data": {
                "userId": self.user_id,
                "version": self.version,
                "ts": time.time(),
                **data
            }
        }
        self.seq += 1
        return payload

    async def move_step(self, direction: str, acc: int = 500, mode: int = 0, is_move_tool: bool = True, is_loop: bool = True, wait_response: bool = False, timeout: float = 5.0):
        """Single step movement"""
        payload = self._build("xarm_move_step", {
            "direction": direction,
            "isLoop": is_loop,
            "isMoveTool": is_move_tool,
            "acc": acc,
            "mode": mode
        })
        logging.debug("xarm_move_step send: %s", payload)
        fut = None
        if wait_response and self.responses:
            fut = self.responses.create_waiter(payload["id"], timeout)
        await self.send(payload)
        if fut:
            return await fut

    async def move_step_over(self, wait_response: bool = False, timeout: float = 5.0):
        """Start hold / step over"""
        payload = self._build("xarm_move_step_over", {})
        logging.debug("xarm_move_step_over send: %s", payload)
        fut = None
        if wait_response and self.responses:
            fut = self.responses.create_waiter(payload["id"], timeout)
        await self.send(payload)
        if fut:
            return await fut

    async def stop(self, reason="manual_stop", wait_response: bool = False, timeout: float = 5.0):
        """Emergency stop"""
        payload = self._build(self.stop_cmd_name, {"reason": reason})
        logging.warning("xarm_stop send: %s", payload)
        fut = None
        if wait_response and self.responses:
            fut = self.responses.create_waiter(payload["id"], timeout)
        await self.send(payload)
        if fut:
            return await fut

    async def ping(self, wait_response: bool = False, timeout: float = 5.0):
        """Ping"""
        payload = self._build("ping", {"ts": time.time()})
        fut = None
        if wait_response and self.responses:
            fut = self.responses.create_waiter(payload["id"], timeout)
        await self.send(payload)
        if fut:
            return await fut
