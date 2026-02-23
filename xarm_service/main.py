import os
import sys
import uuid
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import WS_XARM_BACKEND_URL
from app.types import WebSocketInfo

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)


from app.state import init_state, shutdown_state  # noqa: E402
from app.routes import router as xarm_router  # noqa: E402
from app.health import health_router  # noqa: E402
from app.di import init_di, shutdown_di  # noqa: E402
from app.routes_v2 import router as v2_router  # noqa: E402
from app.metrics import get_metrics  # noqa: E402

logger = logging.getLogger(__name__)


class RequestCorrelationMiddleware(BaseHTTPMiddleware):
    """Add request_id and command_id to request state and logs for correlation."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        command_id = request.headers.get("X-Command-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.command_id = command_id
        logger.info("request_start request_id=%s command_id=%s path=%s", request_id, command_id, request.url.path)
        response = await call_next(request)
        logger.info("request_end request_id=%s command_id=%s path=%s status=%s", request_id, command_id, request.url.path, response.status_code)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Command-ID"] = command_id
        return response

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_di()
    from app.di import get_actor
    actor = get_actor()
    await actor.start()
    try:
        await init_state()
    except Exception as e:
        logger.warning("init_state failed: %s", e)
    try:
        yield
    finally:
        await actor.stop()
        await shutdown_state()
        shutdown_di()

app = FastAPI(title="xArm Microservice", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestCorrelationMiddleware)
app.include_router(xarm_router)
app.include_router(health_router)
app.include_router(v2_router)


@app.get("/metrics", tags=["Meta"])
async def metrics():
    """Simple metrics for observability."""
    return get_metrics()

@app.websocket("/ws")
async def proxy_ws(client_ws: WebSocket):
    # принимаем соединение клиента
    await client_ws.accept()
    query_params = client_ws.url.query
    target_url = f"{WS_XARM_BACKEND_URL}?{query_params}" if query_params else WS_XARM_BACKEND_URL

    server_ws = None
    try:
        server_ws = await websockets.connect(target_url)
        
        async def client_to_server():
            try:
                while True:
                    msg = await client_ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if "text" in msg:
                        await server_ws.send(msg["text"])
                    elif "bytes" in msg:
                        await server_ws.send(msg["bytes"])
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logging.error(f"Error in client_to_server: {e}")

        async def server_to_client():
            try:
                while True:
                    msg = await server_ws.recv()
                    if isinstance(msg, str):
                        await client_ws.send_text(msg)
                    else:
                        await client_ws.send_bytes(msg)
            except websockets.exceptions.ConnectionClosed:
                pass
            except Exception as e:
                logging.error(f"Error in server_to_client: {e}")

        await asyncio.gather(client_to_server(), server_to_client(), return_exceptions=True)

    except Exception as e:
        logging.error(f"WebSocket proxy error: {e}")
    finally:
        try:
            if server_ws:
                await server_ws.close()
        except:
            pass
        try:
            await client_ws.close()
        except:
            pass

@app.get("/ws/info", tags=["WebSocket"], summary="WebSocket tunnel usage", response_model=WebSocketInfo)
async def websocket_info():
    """Provide OpenAPI-visible instructions for the WebSocket proxy."""
    service_host = os.getenv("SERVICE_HOST", "127.0.0.1")
    service_port = os.getenv("SERVICE_PORT", "8102")
    return WebSocketInfo(
        endpoint="/ws",
        backend_url=WS_XARM_BACKEND_URL,
        description="Bidirectional WebSocket proxy that tunnels frames to the upstream xArm backend.",
        query_param_forwarding="All query parameters you include when connecting are appended to the upstream URL before the tunnel establishes.",
        example_command=f"wscat -c ws://example_host/ws?token=YOUR_TOKEN",
    )

@app.get("/healthz", tags=["Meta"])
async def healthz():
    return JSONResponse({"status": "ok"})

@app.get("/readyz", tags=["Meta"])
async def readyz():
    try:
        # We consider ready if manager can be created
        from app import state
        _ = state.get_manager()
        return JSONResponse({"ready": True})
    except Exception as e:
        return JSONResponse({"ready": False, "error": str(e)}, status_code=503)

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVICE_HOST", "127.0.0.1")
    port = int(os.getenv("SERVICE_PORT", "8102"))
    uvicorn.run("main:app", host=host, port=port, reload=False)


