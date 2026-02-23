import asyncio
from typing import Dict, Optional, Any


class ResponseManager:
    def __init__(self, default_timeout: float = 5.0, loop: Optional[asyncio.AbstractEventLoop] = None):
        self._loop = loop or asyncio.get_event_loop()
        self._default_timeout = default_timeout
        self._pending: Dict[str, asyncio.Future] = {}
        self._timeouts: Dict[str, asyncio.TimerHandle] = {}

    def create_waiter(self, request_id: str, timeout: Optional[float] = None) -> asyncio.Future:
        if request_id in self._pending:
            raise RuntimeError(f"Duplicate request id {request_id}")
        fut: asyncio.Future = self._loop.create_future()
        self._pending[str(request_id)] = fut

        t = timeout if timeout is not None else self._default_timeout

        def on_timeout():
            if not fut.done():
                fut.set_exception(asyncio.TimeoutError(f"Response timeout for id={request_id}"))
            self._pending.pop(str(request_id), None)
            handle = self._timeouts.pop(str(request_id), None)
            if handle:
                handle.cancel()

        handle = self._loop.call_later(t, on_timeout)
        self._timeouts[str(request_id)] = handle
        return fut

    async def resolve(self, msg: Dict[str, Any]):
        request_id = str(msg.get("id")) if msg is not None else None
        if request_id is None:
            return
        fut = self._pending.pop(request_id, None)
        handle = self._timeouts.pop(request_id, None)
        if handle:
            handle.cancel()
        if fut and not fut.done():
            # if protocol includes code, map non-zero to error
            code = msg.get("code")
            if isinstance(code, int) and code != 0:
                fut.set_exception(RuntimeError(f"response error code={code} for id={request_id}"))
            else:
                fut.set_result(msg)

    def cancel_all(self):
        for _, fut in list(self._pending.items()):
            if not fut.done():
                fut.cancel()
        for _, h in list(self._timeouts.items()):
            h.cancel()
        self._pending.clear()
        self._timeouts.clear()


