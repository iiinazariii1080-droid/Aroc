import asyncio
import time
from typing import Callable, Dict, Any, Optional


class SafetyLayer:
    def __init__(self,
                 send_stop: Callable[[str], None],
                 forward_command: Callable[[Dict[str, Any]], None],
                 watchdog_timeout: float = 0.2,
                 hold_timeout: float = 0.2):
        """Safety checks layer.
        :param send_stop: async function to send STOP (reason)
        :param forward_command: async function to pass commands downstream
        :param watchdog_timeout: max idle time without messages (sec)
        :param hold_timeout: max hold time for step_over (sec)
        """
        self.send_stop = send_stop
        self.forward_command = forward_command
        self.watchdog_timeout = watchdog_timeout
        self.hold_timeout = hold_timeout

        self.last_msg_ts = time.time()
        self.last_hold_ts = None
        self.running = True

        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def handle_message(self, msg: Dict[str, Any]):
        """Process incoming message"""
        now = time.time()
        self.last_msg_ts = now

        cmd = msg.get("cmd")
        mtype = msg.get("type")
        data = msg.get("data", {})

        # TTL check only when data is a dict
        if isinstance(data, dict):
            ts = data.get("ts", now)
            ttl = data.get("ttl", self.watchdog_timeout)
            if (now - ts) > ttl:
                await self.send_stop("expired_command")
                return

        # Deadman-switch: step hold
        if cmd == "xarm_move_step_over":
            self.last_hold_ts = now

        # Forward downstream
        await self.forward_command(msg)

    def heartbeat(self):
        """Update last message timestamp for heartbeat"""
        self.last_msg_ts = time.time()

    async def _watchdog_loop(self):
        """Watchdog loop"""
        while self.running:
            await asyncio.sleep(0.05)
            now = time.time()
            if not self.running:
                return
            # main connection timeout
            if now - self.last_msg_ts > self.watchdog_timeout:
                await self.send_stop("watchdog_timeout")
                self.last_msg_ts = now  # avoid repeated STOP

            # hold timeout
            if self.last_hold_ts and (now - self.last_hold_ts > self.hold_timeout):
                await self.send_stop("deadman_release")
                self.last_hold_ts = None

    async def stop(self):
        """Stop the layer"""
        self.running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
