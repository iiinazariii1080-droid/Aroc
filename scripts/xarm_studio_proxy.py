"""
Reverse proxy for xArm Studio that rewrites ws:// → wss:// in JS responses.
This allows xArm Studio to work correctly behind Cloudflare HTTPS tunnel.

Usage: python3 xarm_studio_proxy.py
Listens on :18334, proxies to 192.168.1.220:18333
"""

import asyncio
import aiohttp
from aiohttp import web, WSMsgType

UPSTREAM = "http://192.168.1.220:18333"
UPSTREAM_WS = "ws://192.168.1.220:18333"
LISTEN_PORT = 18334


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)

    qs = request.query_string
    upstream_url = f"{UPSTREAM_WS}{request.path}"
    if qs:
        upstream_url += f"?{qs}"

    session = aiohttp.ClientSession()
    try:
        ws_client = await session.ws_connect(upstream_url)

        async def forward_to_client():
            async for msg in ws_client:
                if msg.type == WSMsgType.TEXT:
                    await ws_server.send_str(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await ws_server.send_bytes(msg.data)
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                    break

        async def forward_to_upstream():
            async for msg in ws_server:
                if msg.type == WSMsgType.TEXT:
                    await ws_client.send_str(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await ws_client.send_bytes(msg.data)
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                    break

        await asyncio.gather(forward_to_client(), forward_to_upstream())
    finally:
        await session.close()

    return ws_server


async def handle_http(request: web.Request) -> web.Response:
    upstream_url = f"{UPSTREAM}{request.path}"
    if request.query_string:
        upstream_url += f"?{request.query_string}"

    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            upstream_url,
            headers={k: v for k, v in request.headers.items()
                     if k.lower() not in ("host", "transfer-encoding")},
            data=await request.read(),
        ) as resp:
            body = await resp.read()
            content_type = resp.headers.get("Content-Type", "")

            # Rewrite ws: → wss: in JavaScript responses
            if "javascript" in content_type or request.path.endswith(".js"):
                text = body.decode("utf-8", errors="replace")
                # Replace the hardcoded ws: protocol with wss: for HTTPS compat
                text = text.replace('"ws:"+', '"wss:"+')
                text = text.replace('"ws://"+', '"wss://"+')
                # Also fix http:// upload URLs to https://
                text = text.replace('"http://"+', '"https://"+')
                body = text.encode("utf-8")

            headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
            }

            # Bust cache for JS files so browsers always get the rewritten version
            if "javascript" in content_type or request.path.endswith(".js"):
                headers.pop("ETag", None)
                headers.pop("etag", None)
                headers.pop("Last-Modified", None)
                headers.pop("last-modified", None)
                headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                headers["Content-Length"] = str(len(body))

            return web.Response(body=body, status=resp.status, headers=headers)


async def router(request: web.Request):
    if (
        request.headers.get("Upgrade", "").lower() == "websocket"
        or request.path == "/ws"
    ):
        return await handle_websocket(request)
    return await handle_http(request)


app = web.Application()
app.router.add_route("*", "/{path_info:.*}", router)

if __name__ == "__main__":
    print(f"xArm Studio proxy listening on :{LISTEN_PORT} → {UPSTREAM}")
    web.run_app(app, host="0.0.0.0", port=LISTEN_PORT)
