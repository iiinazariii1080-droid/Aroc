from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse
from typing import Dict, Any
import logging
import os
import os
import httpx
from fastapi import Request
from fastapi.responses import Response
from urllib.parse import urlparse

# куда пересылать запросы глубины
DEPTH_UPSTREAM = os.getenv("DEPTH_UPSTREAM", "http://127.0.0.1:9000/depth_camera").rstrip("/")
# куда проксировать Janus REST API
JANUS_UPSTREAM = os.getenv("JANUS_UPSTREAM", "http://127.0.0.1:8088/janus").rstrip("/")

from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
BASE_DIR = Path(__file__).resolve().parent
proxy_prefix = os.getenv("BASE_PATH", "/depth_camera").strip()
if proxy_prefix and not proxy_prefix.startswith("/"):
    proxy_prefix = "/" + proxy_prefix

 


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Proxy Camera Microservice", version="1.0.0", lifespan=lifespan)



 

# Mount camera API router (handles /depth_camera/overlay_offer and /depth_camera/depth)
from routes.camera import router as camera_router
app.include_router(camera_router)

@app.get("/depth_camera", response_class=HTMLResponse)
def depth_camera():
    html_path = BASE_DIR / "templates" / "depth_camera.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

@app.get("/depth_camera/", response_class=HTMLResponse)
def depth_camera_slash():
    html_path = BASE_DIR / "templates" / "depth_camera.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

@app.get("/favicon.ico")
def favicon():
    fav = BASE_DIR / "static" / "favicon.ico"
    if fav.exists():
        return FileResponse(fav, headers={"Cache-Control": "no-cache"})
    return HTMLResponse(status_code=204)

@app.get("/depth_camera/healthz")
def healthz():
    return {"ok": True}

# Локальный статус, не проксируется, но проверяет доступность DEPTH_UPSTREAM
@app.get("/depth_camera/status")
async def status_check():
    # Prefer the actual camera connectivity via service
    try:
        from routes.camera import get_camera_service  # lazy import to avoid cycles at import time
        service = await get_camera_service()
        status = await service.get_status()
        return {
            "connected": bool(status.connected),
            "ip": status.ip,
            "port": status.port,
            "active_streams": status.active_streams,
        }
    except Exception:
        # Fallback to legacy DEPTH_UPSTREAM probe
        parsed = urlparse(DEPTH_UPSTREAM)
        host = (parsed.hostname or "127.0.0.1")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        connected = False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                try:
                    r = await client.get(f"{DEPTH_UPSTREAM}/healthz")
                    if r.status_code == 200:
                        connected = True
                except Exception:
                    pass
        except Exception:
            connected = False
        return {"connected": connected, "ip": host, "port": port, "active_streams": 0}

# Подключаем статические файлы
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
    # Также монтируем статические файлы с префиксом прокси, если он задан
    if proxy_prefix:
        app.mount(f"{proxy_prefix}/static", StaticFiles(directory="static"), name="static_proxy")



@app.api_route("/janus", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])
@app.api_route("/janus/{subpath:path}", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])
async def janus_proxy(request: Request, subpath: str = ""):
    # финальный URL Janus
    url = JANUS_UPSTREAM if not subpath else f"{JANUS_UPSTREAM}/{subpath}"
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    try:
        req_timeout = 60.0 if request.method in {"POST", "PUT", "PATCH"} else 30.0
        async with httpx.AsyncClient(timeout=req_timeout) as client:
            resp = await client.request(
                request.method,
                url,
                params=request.query_params,
                content=body,
                headers=headers,
            )
    except httpx.ReadTimeout:
        return Response(content=b"Janus upstream timeout", status_code=504, media_type="text/plain")
    except httpx.ConnectError:
        return Response(content=b"Janus upstream connection failed", status_code=502, media_type="text/plain")

    excluded = {"content-encoding", "transfer-encoding", "content-length", "connection"}
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
    return Response(content=resp.content,
                    status_code=resp.status_code,
                    headers=out_headers,
                    media_type=resp.headers.get("content-type"))


@app.api_route(f"{proxy_prefix}/{{subpath:path}}", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])
@app.api_route("/depth_camera/{subpath:path}", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])
async def depth_proxy(subpath: str, request: Request):
    # если запрошен корень, отдаем страницу
    if not subpath:
        html_path = BASE_DIR / "templates" / "depth_camera.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    # финальный URL на локальный микросервис
    url = f"{DEPTH_UPSTREAM}/{subpath}"
    # защита от самопроксирования
    parsed = urlparse(DEPTH_UPSTREAM)
    upstream_host = (parsed.hostname or "127.0.0.1").lower()
    upstream_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    local_port = int(os.getenv("SERVICE_PORT", "9000"))
    if upstream_host in {"127.0.0.1", "localhost", "0.0.0.0"} and upstream_port == local_port:
        # DEPTH_UPSTREAM указывает на этот же сервис → бесконечный прокси-луп
        raise HTTPException(status_code=500, detail="Misconfigured DEPTH_UPSTREAM: points to this service (self-proxy). Set DEPTH_UPSTREAM to the actual upstream, e.g. http://127.0.0.1:9001/depth_camera")
    # тело и заголовки входящего запроса
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    try:
        req_timeout = 60.0 if request.method in {"POST", "PUT", "PATCH"} else 30.0
        async with httpx.AsyncClient(timeout=req_timeout) as client:
            resp = await client.request(
                request.method,
                url,
                params=request.query_params,
                content=body,
                headers=headers,
            )
    except httpx.ReadTimeout:
        return Response(content=b"Upstream timeout", status_code=504, media_type="text/plain")
    except httpx.ConnectError:
        return Response(content=b"Upstream connection failed", status_code=502, media_type="text/plain")

    # пробрасываем ответ назад браузеру
    excluded = {"content-encoding", "transfer-encoding", "content-length", "connection"}
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
    return Response(content=resp.content,
                    status_code=resp.status_code,
                    headers=out_headers,
                    media_type=resp.headers.get("content-type"))
if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVICE_HOST", "127.0.0.1")
    port = int(os.getenv("SERVICE_PORT", "9000"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
