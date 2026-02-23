import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.logging_cfg import setup_logging

setup_logging()

from app.core.http_client import lifespan
from app.core.config import ALLOWED_ORIGINS
from app.core.openapi_agg import setup_custom_openapi

from app.routers import proxy_http, proxy_ws, services_meta, health, hub

app = FastAPI(title="API Gateway", lifespan=lifespan)


# --------------- X-Request-ID middleware ---------------
# Pure ASGI middleware — does NOT buffer response bodies,
# so StreamingResponse from the proxy layer streams correctly.
class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract or generate request ID from headers
        headers = dict(scope.get("headers", []))
        request_id = (headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4()))

        # Stash in scope for downstream access (e.g. request.state)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers", []))
                raw_headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": raw_headers}
            await send(message)

        await self.app(scope, receive, send_with_request_id)


app.add_middleware(RequestIDMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(hub.router)
app.include_router(proxy_ws.router)
app.include_router(proxy_http.router)  # catch-all — must be last
app.include_router(services_meta.router)

setup_custom_openapi(app)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8201")),
        workers=int(os.getenv("UVICORN_WORKERS", "1")),
    )

