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
import numpy as np
from config import ALLOWED_ORIGINS, API_GATEWAY_URL, HOST, PORT

CALIBRATION_AVAILABLE = True
CALIBRATION_DISABLED_DETAIL = "Calibration Python package is disabled (reset mode)"

try:
    from calibration.config import (
        API_JOINTS_POS,
        API_ROBOT_STATUS,
        BASE_URL as CALIB_BASE_URL,
        FLANGE_TO_CAMERA_DEFAULT as CALIB_FTC_DEFAULT,
    )
    from calibration.depth import fetch_depth_frame_with_retry, unproject_frame
    from calibration.fk import flange_to_camera_matrix, camera_world_matrix, tcp_status_pose_to_world_matrix
except Exception:
    CALIBRATION_AVAILABLE = False
    API_JOINTS_POS = "/api/v1/xarm/joints_position"
    API_ROBOT_STATUS = "/api/v1/robot/status"
    CALIB_BASE_URL = "http://localhost:8401"
    CALIB_FTC_DEFAULT = {
        "tx": 0.0,
        "ty": 0.0,
        "tz": 0.0,
        "roll": 0.0,
        "pitch": 0.0,
        "yaw": 0.0,
    }
    fetch_depth_frame_with_retry = None
    unproject_frame = None
    flange_to_camera_matrix = None
    camera_world_matrix = None
    tcp_status_pose_to_world_matrix = None

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="API Gateway Frontend",
    description="Frontend interface for the microservices API Gateway",
    version="1.0.0"
)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── arm3d viewer v2 (Vite build) ────────────────
_VIEWER_DIST = Path(__file__).resolve().parent / "viewer" / "dist"
if _VIEWER_DIST.is_dir():
    app.mount("/arm3d_v2/assets", StaticFiles(directory=str(_VIEWER_DIST / "assets")), name="arm3d_v2_assets")

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
SINGLE_POSE_DATA_DIR = Path(__file__).resolve().parent / "calibration" / "data"
SINGLE_POSE_HTML_PATH = SINGLE_POSE_DATA_DIR / "single_pose_scene.html"
SINGLE_POSE_JSON_PATH = SINGLE_POSE_DATA_DIR / "single_pose_scene.json"
SINGLE_POSE_NPZ_PATH = SINGLE_POSE_DATA_DIR / "single_pose_scene.npz"


def _get_current_joints_live(base_url: str) -> list[float] | None:
    url = f"{base_url.rstrip('/')}{API_JOINTS_POS}"
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
        body = r.json()
        data = body.get("joints", body)
        return [
            float(data["j1"]),
            float(data["j2"]),
            float(data["j3"]),
            float(data["j4"]),
            float(data["j5"]),
            float(data["j6"]),
        ]
    except Exception:
        return None


def _get_mount_and_tcp_live(base_url: str) -> tuple[list[float], list[float] | None, str | None]:
    url = f"{base_url.rstrip('/')}{API_ROBOT_STATUS}"
    mount = [0.0, 0.0]
    tcp = None
    tcp_source = None
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
        payload = r.json()
        xarm = payload.get("xarm") or {}
        summary = xarm.get("summary") or {}
        data = xarm.get("data") or []
        if isinstance(data, list) and len(data) > 51 and isinstance(data[51], list) and len(data[51]) >= 2:
            mount = [float(data[51][0]), float(data[51][1])]

        if isinstance(data, list) and len(data) > 17 and isinstance(data[17], list) and len(data[17]) >= 6:
            tcp = [float(v) for v in data[17][:6]]
            tcp_source = "xarm.data[17]"
        elif isinstance(data, list) and len(data) > 19 and isinstance(data[19], list) and len(data[19]) >= 6:
            tcp = [float(v) for v in data[19][:6]]
            tcp_source = "xarm.data[19]"

        for src, cand in [
            ("payload.tcp", payload.get("tcp")),
            ("payload.pose", payload.get("pose")),
            ("summary.tcp", summary.get("tcp")),
            ("summary.pose", summary.get("pose")),
            ("xarm.tcp", xarm.get("tcp")),
            ("xarm.pose", xarm.get("pose")),
        ]:
            if tcp is None and isinstance(cand, list) and len(cand) >= 6:
                tcp = [float(v) for v in cand[:6]]
                tcp_source = src
                break
    except Exception:
        pass
    return mount, tcp, tcp_source


def _trim_extreme_depth(
    points_cam: np.ndarray,
    colors: np.ndarray,
    low_q: float = 1.0,
    high_q: float = 99.0,
) -> tuple[np.ndarray, np.ndarray]:
    if points_cam.shape[0] == 0:
        return points_cam, colors
    z = points_cam[:, 2]
    finite = np.isfinite(z)
    if not np.any(finite):
        return points_cam[:0], colors[:0]
    z_f = z[finite]
    lo = float(np.percentile(z_f, low_q))
    hi = float(np.percentile(z_f, high_q))
    keep = finite & (z >= lo) & (z <= hi)
    return points_cam[keep], colors[keep]


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


@app.get("/arm3d_v2")
async def serve_arm3d_v2():
    """Serve the v2 Three.js viewer (Vite build)."""
    v2_index = Path(__file__).resolve().parent / "viewer" / "dist" / "index.html"
    if not v2_index.exists():
        return JSONResponse(status_code=503, content={"detail": "v2 viewer not built. Run: cd viewer && npm run build"})
    return FileResponse(str(v2_index))


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


@app.post("/api/v1/single_pose/shot")
async def single_pose_live_shot(request: Request):
    if not CALIBRATION_AVAILABLE:
        return JSONResponse(status_code=503, content={"detail": CALIBRATION_DISABLED_DETAIL})

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    ftc = payload.get("ftc") or {}
    api_gateway_base = API_GATEWAY_URL
    if api_gateway_base.endswith("/api"):
        api_gateway_base = api_gateway_base[:-4]

    default_base_url = api_gateway_base or CALIB_BASE_URL
    base_url = str(payload.get("base_url") or default_base_url)
    stride = int(payload.get("stride") or 4)
    max_points = int(payload.get("max_points") or 80000)

    tx = float(ftc.get("tx", CALIB_FTC_DEFAULT["tx"]))
    ty = float(ftc.get("ty", CALIB_FTC_DEFAULT["ty"]))
    tz = float(ftc.get("tz", CALIB_FTC_DEFAULT["tz"]))
    roll = float(ftc.get("roll", CALIB_FTC_DEFAULT["roll"]))
    pitch = float(ftc.get("pitch", CALIB_FTC_DEFAULT["pitch"]))
    yaw = float(ftc.get("yaw", CALIB_FTC_DEFAULT["yaw"]))

    _, tcp_pose, tcp_source = _get_mount_and_tcp_live(base_url)

    if tcp_pose is None:
        return JSONResponse(status_code=502, content={"detail": "TCP-only mode: tcp pose unavailable from robot status", "tcp_source": tcp_source})

    try:
        base_matrix = tcp_status_pose_to_world_matrix(tcp_pose)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"detail": f"TCP-only mode: tcp pose parse failed: {exc}"})

    base_source = "tcp"

    try:
        depth_u16, rgb_u8, w, h = fetch_depth_frame_with_retry(base_url)
    except Exception as e:
        return JSONResponse(status_code=502, content={"detail": f"Depth fetch failed: {e}"})

    pts_cam, cols = unproject_frame(depth_u16, rgb_u8, w, h, stride=stride)
    if cols is None:
        cols = np.full((pts_cam.shape[0], 3), [0.45, 0.75, 0.95], dtype=np.float32)

    pts_cam, cols = _trim_extreme_depth(pts_cam, cols.astype(np.float32), low_q=1.0, high_q=99.0)

    t_fc = flange_to_camera_matrix(tx, ty, tz, roll, pitch, yaw)
    t_wc = camera_world_matrix(base_matrix, t_fc)

    n = pts_cam.shape[0]
    ones = np.ones((n, 1), dtype=np.float32)
    pts_h = np.hstack([pts_cam.astype(np.float32), ones])
    pts_world = (t_wc @ pts_h.T).T[:, :3].astype(np.float32)

    cols_f32 = cols.astype(np.float32)
    zero_rgb_mask = np.all(cols_f32 <= 0.0, axis=1)
    zero_point_mask = np.all(np.abs(pts_cam) <= 1e-9, axis=1) | (pts_cam[:, 2] <= 1e-9)
    keep_mask = (~zero_rgb_mask) & (~zero_point_mask)

    pts_cam = pts_cam[keep_mask]
    pts_world = pts_world[keep_mask]
    cols_f32 = cols_f32[keep_mask]

    if pts_world.shape[0] > max_points:
        rng = np.random.default_rng(42)
        idx = rng.choice(pts_world.shape[0], max_points, replace=False)
        pts_cam = pts_cam[idx]
        pts_world = pts_world[idx]
        cols_f32 = cols_f32[idx]

    return {
        "cloud_cam": pts_cam.astype(float).tolist(),
        "cloud_world": pts_world.astype(float).tolist(),
        "cloud_color": cols_f32.astype(float).tolist(),
        "base_matrix": base_matrix.astype(float).reshape(-1).tolist(),
        "camera_world": {"position": t_wc[:3, 3].astype(float).tolist()},
        "base_source": base_source,
        "tcp_source": tcp_source,
        "tcp_pose": tcp_pose,
        "stats": {"points": int(pts_world.shape[0])},
        "frame": {"width": int(w), "height": int(h), "stride": int(stride)},
    }


@app.post("/single_pose/shot")
async def single_pose_live_shot_local(request: Request):
    return await single_pose_live_shot(request)


@app.get("/single_pose_scene.html")
async def serve_single_pose_scene_html():
    if not SINGLE_POSE_HTML_PATH.exists():
        return JSONResponse(status_code=404, content={"detail": "single_pose_scene.html not found. Run calibration.single_pose_debug_scene first."})
    return FileResponse(
        str(SINGLE_POSE_HTML_PATH),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/single_pose_scene.json")
async def serve_single_pose_scene_json():
    if not SINGLE_POSE_JSON_PATH.exists():
        return JSONResponse(status_code=404, content={"detail": "single_pose_scene.json not found. Run calibration.single_pose_debug_scene first."})
    return FileResponse(
        str(SINGLE_POSE_JSON_PATH),
        media_type="application/json",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/single_pose_scene.npz")
async def serve_single_pose_scene_npz():
    if not SINGLE_POSE_NPZ_PATH.exists():
        return JSONResponse(status_code=404, content={"detail": "single_pose_scene.npz not found. Run calibration.single_pose_debug_scene first."})
    return FileResponse(
        str(SINGLE_POSE_NPZ_PATH),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/single_pos1e_scene.html")
async def single_pose_typo_alias():
    return RedirectResponse(url="/single_pose_scene.html", status_code=307)


@app.get("/products_admin")
@app.get("/products_admin.html")
async def serve_products_admin():
    return FileResponse("static/pages/products_admin.html")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("static/") or ".." in full_path:
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

