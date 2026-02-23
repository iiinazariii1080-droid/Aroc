import asyncio
import json
import time
import random
import websockets
import inspect
import logging

async def _maybe_await(func, *args, **kwargs):
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    else:
        return func(*args, **kwargs)
    
class ConnectionManager:
    def __init__(self, url, on_message=None, on_connect=None, on_disconnect=None):
        self.url = url
        self.ws = None
        self.connected = False
        self._stopped = False

        # callbacks
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

        # reconnect params
        self.reconnect_delay = 1
        self.max_reconnect_delay = 10

        # heartbeat params
        self.heartbeat_interval = 3.0
        self._heartbeat_task = None
        # processing queue/worker
        self._queue = asyncio.Queue(maxsize=1000)
        self._processor_task = None


    async def connect(self):
        """Connect with auto-reconnect"""
        while not self._stopped:
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=10) as ws:
                    self.ws = ws
                    self.connected = True
                    self.reconnect_delay = 1  # reset delay

                    logging.info(f"Connected to {self.url}")

                    if self.on_connect:
                        try:
                            await _maybe_await(self.on_connect)
                        except Exception as cb_err:
                            print(f"on_connect handler error: {cb_err}")

                    # start heartbeat
                    self._heartbeat_task = asyncio.create_task(self._heartbeat())

                    # start processor
                    self._processor_task = asyncio.create_task(self._process())

                    # listen for incoming messages
                    await self._listen()
            except Exception as e:
                logging.warning(f"Connection error: {e}")
            finally:
                self.connected = False
                if self.on_disconnect:
                    try:
                        await _maybe_await(self.on_disconnect)
                    except Exception as cb_err:
                        logging.warning(f"on_disconnect handler error: {cb_err}")

                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                if self._processor_task:
                    self._processor_task.cancel()

                # add jitter to avoid thundering herd and align with server restarts
                jitter = random.uniform(0, self.reconnect_delay * 0.5)
                total_delay = self.reconnect_delay + jitter
                if self._stopped:
                    break
                logging.info(f"Reconnecting in {total_delay:.2f}s...")
                await asyncio.sleep(total_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    async def _listen(self):
        """Receive loop"""
        async for raw in self.ws:
            try:
                try:
                    msg = json.loads(raw)
                except Exception:
                    msg = raw  # fallback: not JSON
                # enqueue for downstream processing
                try:
                    if self._queue.full():
                        # drop oldest to apply backpressure
                        try:
                            _ = self._queue.get_nowait()
                            self._queue.task_done()
                        except Exception:
                            pass
                        logging.warning("processing queue full; dropping oldest message")
                    await self._queue.put(msg)
                except Exception as q_err:
                    logging.warning(f"failed to enqueue message: {q_err}")
            except Exception as loop_err:
                # Any unexpected error in listen loop should break to reconnect
                logging.warning(f"Listen loop error: {loop_err}")
                break

    async def _process(self):
        """Process messages from the internal queue"""
        while True:
            msg = await self._queue.get()
            try:
                if self.on_message:
                    await _maybe_await(self.on_message, msg)
            except Exception as cb_err:
                logging.warning(f"on_message handler error: {cb_err}")
            finally:
                self._queue.task_done()

    async def _heartbeat(self):
        """Send ping for liveness"""
        while True:
            try:
                if self.connected and self.ws:
                    payload = {
                        "type": "ping",
                        "ts": time.time()
                    }
                    await self.ws.send(json.dumps(payload))
            except Exception as e:
                logging.warning(f"Heartbeat failed: {e}")
                return
            await asyncio.sleep(self.heartbeat_interval)

    async def send(self, payload: dict):
        """Send a message"""
        if not self.connected or not self.ws:
            logging.warning("Not connected, message dropped")
            return
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as e:
            logging.warning(f"Send error: {e}")

    async def close(self):
        """Close connection"""
        self._stopped = True
        if self.ws:
            await self.ws.close()
        self.connected = False
        logging.info("Connection closed")
        if self._processor_task:
            self._processor_task.cancel()
