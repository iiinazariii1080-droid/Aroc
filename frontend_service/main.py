from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import httpx
import json
import websockets
import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from config import ALLOWED_ORIGINS, API_GATEWAY_URL, HOST, PORT

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="API Gateway Frontend",
    description="Frontend interface for the microservices API Gateway",
    version="1.0.0"
)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "Authorization", "X-Admin-Secret"],
)

DEPTH_MAP_DIR = Path(__file__).resolve().parent / "data" / "depth_map"
DEPTH_MAP_BIN_PATH = DEPTH_MAP_DIR / "latest.bin"
DEPTH_MAP_META_PATH = DEPTH_MAP_DIR / "latest.meta.json"
DEPTH_MAP_MAGIC = b"DMP1"
DEPTH_MAP_HEADER_BYTES = 12
DEPTH_MAP_RECORD_BYTES = 15


def _ensure_depth_map_dir() -> None:
    DEPTH_MAP_DIR.mkdir(parents=True, exist_ok=True)


def _parse_depth_map_header(payload: bytes) -> tuple[int, int]:
    if len(payload) < DEPTH_MAP_HEADER_BYTES:
        raise ValueError("payload too small")
    if payload[:4] != DEPTH_MAP_MAGIC:
        raise ValueError("invalid magic")
    version = int.from_bytes(payload[4:6], "little", signed=False)
    count = int.from_bytes(payload[8:12], "little", signed=False)
    expected_len = DEPTH_MAP_HEADER_BYTES + (count * DEPTH_MAP_RECORD_BYTES)
    if len(payload) != expected_len:
        raise ValueError(f"payload size mismatch: got={len(payload)} expected={expected_len}")
    return version, count


# ===============================================================
async def proxy_janus_path(request: Request, path: str):
    """Proxy every other request to Janus"""
    url = f"{JANUS2_URL}/janus2/{path}"
    if request.query_params:
        url = f"{url}?{request.query_params}"

    async with httpx.AsyncClient(        timeout=httpx.Timeout(
            connect=5.0,     # fast connect
            read=70.0,       # MUST stay above the Janus long-poll duration (~60s)
            write=30.0,
            pool=60.0
        ),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        headers={"Connection": "keep-alive"}) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=url,
                headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
                content=await request.body(),
            )
        except Exception as e:
            logging.error(f"Janus proxy error: {e}")
            raise HTTPException(status_code=502, detail=str(e))

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type")
    )

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect
import os, ssl, asyncio
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from websockets.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# Janus backends (separate per camera)
JANUS_WS_URL_COLOR = os.getenv("JANUS_WS_URL_COLOR", "ws://192.168.1.10:8188/janus-ws")
JANUS_WS_URL_DEPTH = os.getenv("JANUS_WS_URL_DEPTH", "ws://192.168.1.55:8900/janus-ws")
JANUS_WS_URL_XARM = os.getenv("JANUS_WS_URL_XARM", "ws://192.168.1.220:18333/ws")

ALLOW_INSECURE_TLS = os.getenv("ALLOW_INSECURE_TLS", "0") == "1"

def _ssl_ctx_for(url: str):
    if url.startswith("wss://"):
        ctx = ssl.create_default_context()
        if ALLOW_INSECURE_TLS:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None

async def _pump_client_to_upstream(client_ws: WebSocket, upstream_ws):
    try:
        while True:
            msg = await client_ws.receive()
            t = msg.get("type")
            if t == "websocket.receive":
                if "text" in msg and msg["text"] is not None:
                    await upstream_ws.send(msg["text"])
                elif "bytes" in msg and msg["bytes"] is not None:
                    await upstream_ws.send(msg["bytes"])
            elif t == "websocket.disconnect":
                try:
                    await upstream_ws.close()
                except Exception:
                    pass
                break
    except WebSocketDisconnect:
        try:
            await upstream_ws.close()
        except Exception:
            pass

async def _pump_upstream_to_client(client_ws: WebSocket, upstream_ws):
    try:
        async for message in upstream_ws:
            if isinstance(message, (bytes, bytearray)):
                await client_ws.send_bytes(message)
            else:
                await client_ws.send_text(message)
    except (ConnectionClosedOK, ConnectionClosedError):
        try:
            await client_ws.close()
        except Exception:
            pass

# --- WS proxy /janus-ws ---
from starlette.websockets import WebSocket
from websockets.client import connect as ws_connect

@app.websocket_route("/api/v1/depth_camera/janus-ws")
async def depth_camera_ws_proxy(client_ws: WebSocket):
    upstream_url = JANUS_WS_URL_DEPTH

    # Capture subprotocols offered by the client (Janus.js usually sends janus-protocol)
    req_hdr = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [s.strip() for s in req_hdr.split(",") if s.strip()]
    use_sub = "janus-protocol" if "janus-protocol" in offered else None

    # Accept using the same subprotocol (or none if the client did not offer one)
    await client_ws.accept(subprotocol=use_sub)

    kwargs = dict(
        open_timeout=20, ping_interval=20, ping_timeout=20, close_timeout=10,
        max_size=2**20, compression=None,
        ssl=_ssl_ctx_for(upstream_url),
    )
    if use_sub:
        kwargs["subprotocols"] = [use_sub]

    async with ws_connect(upstream_url, **kwargs) as upstream_ws:
        await asyncio.gather(
            _pump_client_to_upstream(client_ws, upstream_ws),
            _pump_upstream_to_client(client_ws, upstream_ws),
        )

@app.websocket_route("/api/v1/color_camera/janus-ws")
async def color_camera_ws_proxy(client_ws: WebSocket):
    upstream_url = JANUS_WS_URL_COLOR

    # Capture subprotocols offered by the client (Janus.js usually sends janus-protocol)
    req_hdr = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [s.strip() for s in req_hdr.split(",") if s.strip()]
    use_sub = "janus-protocol" if "janus-protocol" in offered else None

    # Accept using the same subprotocol (or none if the client did not offer one)
    await client_ws.accept(subprotocol=use_sub)

    kwargs = dict(
        open_timeout=20, ping_interval=20, ping_timeout=20, close_timeout=10,
        max_size=2**20, compression=None,
        ssl=_ssl_ctx_for(upstream_url),
    )
    if use_sub:
        kwargs["subprotocols"] = [use_sub]

    async with ws_connect(upstream_url, **kwargs) as upstream_ws:
        await asyncio.gather(
            _pump_client_to_upstream(client_ws, upstream_ws),
            _pump_upstream_to_client(client_ws, upstream_ws),
        )

@app.websocket_route("/api/v1/xarm/ws")
async def xarm_ws_proxy(client_ws: WebSocket):
    upstream_url = JANUS_WS_URL_XARM

    # Capture subprotocols offered by the client (Janus.js usually sends janus-protocol)
    req_hdr = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [s.strip() for s in req_hdr.split(",") if s.strip()]
    use_sub = "janus-protocol" if "janus-protocol" in offered else None

    # Accept using the same subprotocol (or none if the client did not offer one)
    await client_ws.accept(subprotocol=use_sub)

    kwargs = dict(
        open_timeout=20, ping_interval=20, ping_timeout=20, close_timeout=10,
        max_size=2**20, compression=None,
        ssl=_ssl_ctx_for(upstream_url),
    )
    if use_sub:
        kwargs["subprotocols"] = [use_sub]

    async with ws_connect(upstream_url, **kwargs) as upstream_ws:
        await asyncio.gather(
            _pump_client_to_upstream(client_ws, upstream_ws),
            _pump_upstream_to_client(client_ws, upstream_ws),
        )
# ===============================================================

# ===============================================================

@app.get("/map_viewer")
async def serve_map_viewer():
    return FileResponse("map_viewer.html")

@app.get("/arm3d_viewer")
async def serve_arm3d_viewer():
    return FileResponse("arm3d_viewer.html")


@app.get("/api/v1/depth_map/info")
async def depth_map_info():
    if not DEPTH_MAP_BIN_PATH.exists() or not DEPTH_MAP_META_PATH.exists():
        return JSONResponse(status_code=404, content={"detail": "No saved depth map"})
    try:
        with open(DEPTH_MAP_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if "bytes" not in meta:
            meta["bytes"] = DEPTH_MAP_BIN_PATH.stat().st_size
        return JSONResponse(status_code=200, content=meta)
    except Exception as exc:
        logging.error(f"depth_map_info failed: {exc}")
        return JSONResponse(status_code=500, content={"detail": "Failed to read depth map metadata"})


@app.get("/api/v1/depth_map/load")
async def depth_map_load():
    if not DEPTH_MAP_BIN_PATH.exists():
        return JSONResponse(status_code=404, content={"detail": "No saved depth map"})
    try:
        payload = DEPTH_MAP_BIN_PATH.read_bytes()
        version, count = _parse_depth_map_header(payload)
        headers = {
            "X-Depth-Map-Version": str(version),
            "X-Depth-Map-Point-Count": str(count),
        }
        if DEPTH_MAP_META_PATH.exists():
            try:
                meta = json.loads(DEPTH_MAP_META_PATH.read_text(encoding="utf-8"))
                headers["X-Depth-Map-Saved-At"] = str(meta.get("saved_at", ""))
            except Exception:
                pass
        return Response(content=payload, media_type="application/octet-stream", headers=headers)
    except ValueError as exc:
        logging.error(f"depth_map_load invalid payload: {exc}")
        return JSONResponse(status_code=500, content={"detail": "Saved depth map is corrupted"})
    except Exception as exc:
        logging.error(f"depth_map_load failed: {exc}")
        return JSONResponse(status_code=500, content={"detail": "Failed to load depth map"})


@app.post("/api/v1/depth_map/save")
async def depth_map_save(request: Request):
    try:
        payload = await request.body()
        if not payload:
            return JSONResponse(status_code=400, content={"detail": "Empty payload"})

        version, count = _parse_depth_map_header(payload)
        voxel_size_mm_raw = request.headers.get("x-depth-map-voxel-size-mm")
        voxel_size_mm = float(voxel_size_mm_raw) if voxel_size_mm_raw is not None else None

        _ensure_depth_map_dir()
        tmp_bin = DEPTH_MAP_BIN_PATH.with_suffix(".bin.tmp")
        tmp_meta = DEPTH_MAP_META_PATH.with_suffix(".json.tmp")

        tmp_bin.write_bytes(payload)
        meta = {
            "format": "dmp1",
            "version": version,
            "units": "world_units",
            "point_count": count,
            "bytes": len(payload),
            "voxel_size_mm": voxel_size_mm,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        tmp_bin.replace(DEPTH_MAP_BIN_PATH)
        tmp_meta.replace(DEPTH_MAP_META_PATH)
        return JSONResponse(status_code=200, content=meta)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": f"Invalid depth map payload: {exc}"})
    except Exception as exc:
        logging.error(f"depth_map_save failed: {exc}")
        return JSONResponse(status_code=500, content={"detail": "Failed to save depth map"})


@app.delete("/api/v1/depth_map")
async def depth_map_delete():
    removed = False
    try:
        if DEPTH_MAP_BIN_PATH.exists():
            DEPTH_MAP_BIN_PATH.unlink()
            removed = True
        if DEPTH_MAP_META_PATH.exists():
            DEPTH_MAP_META_PATH.unlink()
            removed = True
        return JSONResponse(status_code=200, content={"removed": removed})
    except Exception as exc:
        logging.error(f"depth_map_delete failed: {exc}")
        return JSONResponse(status_code=500, content={"detail": "Failed to delete depth map"})
    
@app.get("/")
async def serve_frontend():
    return FileResponse("index.html")

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_api(request: Request, path: str):
    # Build URL with query parameters
    url = f"{API_GATEWAY_URL.rstrip('/')}/api/{path}"
    if request.query_params:
        query_string = str(request.query_params)
        url = f"{url}?{query_string}"

    # Configure timeout settings
    timeout = httpx.Timeout(connect=10.0, read=95.0, write=30.0, pool=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            request.method,
            url,
            headers={
                k: v for k, v in request.headers.items()
                if k.lower() not in ("host",)
            } | {
                "X-Forwarded-For": request.client.host,
                "X-Forwarded-Proto": request.url.scheme,
            },
            content=await request.body(),
            follow_redirects=True,
        )
    
    # async with httpx.AsyncClient(timeout=timeout) as client:
    #     try:
    #         resp = await client.request(
    #             request.method,
    #             url,
    #             headers={k: v for k, v in request.headers.items() if k != "host"},
    #             content=await request.body(),
    #             follow_redirects=True  # Automatically follow redirects
    #         )
    #     except httpx.TimeoutException:
    #         return JSONResponse(
    #             status_code=504,
    #             content={"error": "Gateway timeout", "message": "The upstream server did not respond in time"}
    #         )
    #     except httpx.ConnectError:
    #         return JSONResponse(
    #             status_code=502,
    #             content={"error": "Bad gateway", "message": "Unable to connect to upstream server"}
    #         )
    #     except Exception as e:
    #         logging.error(f"API proxy error: {e}")
    #         return JSONResponse(
    #             status_code=500,
    #             content={"error": "Internal server error", "message": str(e)}
    #         )

    # Get content type and handle different response types
    content_type = resp.headers.get("content-type", "").lower()
    
    # For HTML responses, return as HTML
    if "text/html" in content_type:
        return HTMLResponse(
            content=resp.text,
            status_code=resp.status_code,
            headers=dict(resp.headers)
        )
    
    # For JSON responses
    elif "application/json" in content_type and resp.content:
        try:
            return JSONResponse(
                status_code=resp.status_code,
                content=resp.json(),
                headers=dict(resp.headers)
            )
        except json.JSONDecodeError:
            # If JSON parsing fails, return the raw content
            return JSONResponse(
                status_code=resp.status_code,
                content={"error": "Invalid JSON response", "raw_content": resp.text}
            )
    
    # For other content types, return as raw response
    else:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=content_type if content_type else "text/plain"
        )

@app.on_event("startup")
async def list_routes():
    import inspect
    logging.info("=== ROUTES LOADED ===")
    for r in app.routes:
        logging.info(f"{r.path}  methods={getattr(r,'methods',None)}  name={r.name}")
    logging.info("=====================")


@app.get("/health")
async def health_check():
    """Проверка здоровья сервера"""
    return {"status": "healthy", "service": "frontend"}


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("static/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return FileResponse("index.html")


if __name__ == "__main__":
    # Запуск сервера
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=False,  # Автоперезагрузка в режиме разработки
        log_level="info"
    )

