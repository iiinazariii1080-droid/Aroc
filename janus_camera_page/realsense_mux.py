
import os
import sys
import time
import stat
from dataclasses import dataclass
from typing import List, Dict, Optional
import threading
import signal
import argparse
import os
import sys
import time
import stat
from dataclasses import dataclass
from typing import List, Dict, Optional
import threading
import signal
import json

import pyrealsense2 as rs
import numpy as np

# ----- Вспомогательные повороты -----

def rotate_img(arr: np.ndarray, mode: str) -> np.ndarray:
    if mode == "cw":
        return np.rot90(arr, 3)
    if mode == "ccw":
        return np.rot90(arr, 1)
    if mode == "flip":
        return np.flipud(arr)
    return arr


@dataclass
class ModeInfo:
    stream: str
    stream_type: rs.stream
    stream_index: int
    width: int
    height: int
    fps: int
    format: rs.format

    def human(self) -> str:
        return f"{self.width}x{self.height} @{self.fps}fps format={self.format.name} stream_index={self.stream_index}"


# --- HTTP (опционально) ---
try:
    from fastapi import FastAPI, APIRouter, Depends
    from pydantic import BaseModel
    import uvicorn
    FASTAPI_AVAILABLE = True
except Exception:
    FASTAPI_AVAILABLE = False


class CameraService:
    """
    Хранит *уже повернутый* depth-кадр (float32, метры) и даёт глубину по нормализованным координатам [0..1].
    Также хранит последний цветной кадр (RGB24, uint8) для colour-overlay.
    """
    def __init__(self, rotate="none", flip_x=False, flip_y=False, depth_flip180=False):
        self.rotate = rotate
        self.flip_x = flip_x
        self.flip_y = flip_y
        self.depth_flip180 = depth_flip180
        self._lock = threading.Lock()
        self._depth_m = None
        self._shape = None
        self._updated_at = 0.0
        # Real RGB colour frame from RealSense color sensor
        self._color_rgb: Optional[np.ndarray] = None
        self._color_shape: Optional[tuple] = None
        self._color_updated_at: float = 0.0

    def update_depth_from_z16(self, z16: np.ndarray, scale_m_per_unit: float):
        depth_m = z16.astype(np.float32) * scale_m_per_unit
        depth_m = rotate_img(depth_m, self.rotate)
        if self.depth_flip180:
            depth_m = np.rot90(depth_m, 2)  # 180° = hflip + vflip
        with self._lock:
            self._depth_m = depth_m
            self._shape = depth_m.shape[:2]
            self._updated_at = time.time()

    def update_color_rgb(self, rgb: np.ndarray):
        """Store the latest colour frame (uint8 HxWx3, already rotated)."""
        with self._lock:
            self._color_rgb = rgb
            self._color_shape = rgb.shape[:2]
            self._color_updated_at = time.time()

    def get_color_frame(self) -> Optional[np.ndarray]:
        """Return the latest colour frame or None."""
        with self._lock:
            return self._color_rgb.copy() if self._color_rgb is not None else None

    def get_color_timestamp(self) -> float:
        with self._lock:
            return self._color_updated_at

    def get_depth_map(self) -> Optional[dict]:
        """Return the full depth frame (float32, metres) with metadata, or None."""
        with self._lock:
            if self._depth_m is None:
                return None
            arr = self._depth_m.copy()
            ts = self._updated_at
        h, w = arr.shape[:2]
        return {"array": arr, "width": w, "height": h, "timestamp": ts}

    def get_depth(self, x_norm: float, y_norm: float):
        with self._lock:
            arr = self._depth_m
            shape = self._shape
        if arr is None or shape is None:
            raise RuntimeError("No depth frame yet")

        H, W = shape
        xn = min(max(x_norm, 0.0), 1.0)
        yn = min(max(y_norm, 0.0), 1.0)
        if self.flip_x:
            xn = 1.0 - xn
        if self.flip_y:
            yn = 1.0 - yn
        j = int(round(xn * (W - 1)))   # колонка
        i = int(round(yn * (H - 1)))   # строка

        depth_val = float(arr[i, j])
        return depth_val, i, j, W, H, self._updated_at


def make_fastapi(service: CameraService):
    app = FastAPI(title="DepthService")
    router = APIRouter()

    class DepthResponse(BaseModel):
        type: str = "depth"
        x: float
        y: float
        depth: float

    def get_camera_service():
        return service

    @router.get("/depth", response_model=DepthResponse)
    async def get_depth(
        x: float,
        y: float,
        service: CameraService = Depends(get_camera_service),
    ):
        # Клиент даёт 0..100 → нормализуем в 0..1
        x_norm = x / 100.0
        y_norm = y / 100.0

        # ВАЖНО: depth-массив уже повернут так же, как видео,
        # поэтому больше НИЧЕГО не крутим, просто семплим.
        depth_m, i, j, W, H, ts = service.get_depth(x_norm, y_norm)
        return DepthResponse(type="depth", x=x, y=y, depth=depth_m)

    @router.get("/color_frame")
    async def get_color_frame(
        format: str = "json",
        service: CameraService = Depends(get_camera_service),
    ):
        """Return latest D435 color frame as base64 RGB24 JSON."""
        import base64 as b64mod
        frame = service.get_color_frame()
        if frame is None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={"detail": "no color frame yet"})
        h, w = frame.shape[:2]
        raw_bytes = frame.tobytes()
        if format == "raw":
            return Response(content=raw_bytes, media_type="application/octet-stream",
                            headers={"X-Width": str(w), "X-Height": str(h), "X-Dtype": "uint8-rgb24"})
        encoded = b64mod.b64encode(raw_bytes).decode("ascii")
        return {"width": w, "height": h, "dtype": "uint8-rgb24",
                "timestamp": service.get_color_timestamp(), "data": encoded}

    @router.get("/depth_map")
    async def get_depth_map(
        format: str = "json",
        service: CameraService = Depends(get_camera_service),
    ):
        """Return the full depth frame (float32, metres) as base64 JSON or raw bytes."""
        import base64 as b64mod
        dm = service.get_depth_map()
        if dm is None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={"detail": "no depth frame yet"})
        arr = dm["array"]
        w, h = dm["width"], dm["height"]
        raw_bytes = arr.tobytes()
        if format == "raw":
            return Response(
                content=raw_bytes,
                media_type="application/octet-stream",
                headers={
                    "X-Width": str(w),
                    "X-Height": str(h),
                    "X-Dtype": "float32",
                    "X-Timestamp": str(dm["timestamp"]),
                },
            )
        encoded = b64mod.b64encode(raw_bytes).decode("ascii")
        return {
            "width": w,
            "height": h,
            "dtype": "float32",
            "timestamp": dm["timestamp"],
            "data": encoded,
        }

    app.include_router(router)
    return app


# ----- RealSense режимы -----

def probe_modes() -> Dict[str, List[ModeInfo]]:
    ctx = rs.context()
    devices = ctx.query_devices()
    if len(devices) == 0:
        print("No RealSense devices found", file=sys.stderr)
        sys.exit(1)

    dev = devices[0]
    modes: Dict[str, List[ModeInfo]] = {"color": [], "depth": [], "ir": []}

    for sensor in dev.query_sensors():
        profiles = sensor.get_stream_profiles()
        for p in profiles:
            try:
                vp = p.as_video_stream_profile()
            except Exception:
                continue

            st = vp.stream_type()
            fmt = vp.format()
            w = vp.width()
            h = vp.height()
            fps = vp.fps()
            idx = vp.stream_index()

            if st == rs.stream.color:
                modes["color"].append(ModeInfo("color", st, idx, w, h, fps, fmt))
            elif st == rs.stream.depth:
                modes["depth"].append(ModeInfo("depth", st, idx, w, h, fps, fmt))
            elif st == rs.stream.infrared:
                modes["ir"].append(ModeInfo("ir", st, idx, w, h, fps, fmt))

    for key in modes:
        modes[key].sort(key=lambda m: (m.stream_index, m.width, m.height, m.fps))
    return modes


def print_modes(modes: Dict[str, List[ModeInfo]]) -> None:
    if modes["color"]:
        print("COLOR modes:")
        for i, m in enumerate(modes["color"]):
            print(f"  [{i}] {m.human()}")
        print()
    else:
        print("COLOR modes: none\n")

    if modes["depth"]:
        print("DEPTH modes:")
        for i, m in enumerate(modes["depth"]):
            print(f"  [{i}] {m.human()}")
        print()
    else:
        print("DEPTH modes: none\n")

    if modes["ir"]:
        print("IR modes:")
        for i, m in enumerate(modes["ir"]):
            print(f"  [{i}] {m.human()}")
        print()
    else:
        print("IR modes: none\n")


def select_mode(modes: List[ModeInfo], index: Optional[int]) -> Optional[ModeInfo]:
    if index is None or index < 0:
        return None
    if index >= len(modes):
        print(f"Requested index {index}, but only {len(modes)} modes available", file=sys.stderr)
        sys.exit(2)
    return modes[index]


def ensure_fifo(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

    if os.path.exists(path):
        st = os.stat(path)
        if not stat.S_ISFIFO(st.st_mode):
            raise RuntimeError(f"{path} exists but is not a FIFO")
    else:
        os.mkfifo(path, 0o666)
        print(f"[fifo] created {path}", flush=True)


def open_fifo_writer_blocking(path: str):
    print(f"[fifo] waiting for reader on {path} ...", flush=True)
    fd = os.open(path, os.O_WRONLY)
    print(f"[fifo] writer opened {path}", flush=True)
    return os.fdopen(fd, "wb", buffering=0)


def run_pipeline(
    color_idx: int,
    depth_idx: int,
    ir_idx: int,
    color_fifo: Optional[str],
    depth_fifo: Optional[str],
    ir_fifo: Optional[str],
    rotate: str,
    depth_flip180: bool = False,
) -> None:
    modes = probe_modes()

    color_mode = select_mode(modes["color"], color_idx) if color_idx >= 0 else None
    depth_mode = select_mode(modes["depth"], depth_idx) if depth_idx >= 0 else None
    ir_mode    = select_mode(modes["ir"],    ir_idx)    if ir_idx    >= 0 else None

    service = CameraService(rotate=rotate, flip_x=False, flip_y=False, depth_flip180=depth_flip180)

    if not color_mode and not depth_mode and not ir_mode:
        print("No streams selected", file=sys.stderr)
        sys.exit(1)

    print("Selected modes:")
    if color_mode:
        print("  COLOR:", color_mode.human())
    if depth_mode:
        print("  DEPTH:", depth_mode.human())
    if ir_mode:
        print("  IR   :", ir_mode.human())
    print()

    color_writer = depth_writer = ir_writer = None

    if color_mode and color_fifo:
        ensure_fifo(color_fifo)
        color_writer = open_fifo_writer_blocking(color_fifo)
    if depth_mode and depth_fifo:
        ensure_fifo(depth_fifo)
        depth_writer = open_fifo_writer_blocking(depth_fifo)
    if ir_mode and ir_fifo:
        ensure_fifo(ir_fifo)
        ir_writer = open_fifo_writer_blocking(ir_fifo)

    pipeline = rs.pipeline()
    config = rs.config()

    def enable(m: ModeInfo):
        config.enable_stream(m.stream_type, m.stream_index, m.width, m.height, m.format, m.fps)

    if color_mode:
        enable(color_mode)
    if depth_mode:
        enable(depth_mode)
    if ir_mode:
        enable(ir_mode)

    profile = pipeline.start(config)

    depth_scale = None
    if depth_mode:
        depth_sensor = profile.get_device().first_depth_sensor()
        depth_scale = float(depth_sensor.get_depth_scale())

    colorizer = rs.colorizer()

    if FASTAPI_AVAILABLE:
        app = make_fastapi(service)

        def _run_http():
            uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")

        http_thread = threading.Thread(target=_run_http, daemon=True)
        http_thread.start()
    else:
        print("[http] FastAPI/uvicorn not available, HTTP API disabled", flush=True)

    running = True

    def handle_sig(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    print("Pipeline started. Streaming frames... (Ctrl+C to stop)", flush=True)

    color_count = depth_count = ir_count = 0
    last_report = time.time()

    def _safe_write(writer, data, fifo_path, label):
        """Write to FIFO, recovering from BrokenPipeError if the reader (ffmpeg) died."""
        nonlocal color_writer, depth_writer, ir_writer
        try:
            writer.write(data)
            return writer
        except (BrokenPipeError, OSError) as exc:
            print(f"[fifo] {label} writer broken ({exc}), reopening {fifo_path} ...", flush=True)
            try:
                writer.close()
            except Exception:
                pass
            try:
                new_writer = open_fifo_writer_blocking(fifo_path)
                new_writer.write(data)
                print(f"[fifo] {label} writer recovered", flush=True)
                return new_writer
            except Exception as reopen_exc:
                print(f"[fifo] {label} reopen failed: {reopen_exc}", flush=True)
                return None

    try:
        while running:
            frames = pipeline.wait_for_frames()

            if color_mode:
                cf = frames.get_color_frame()
                if cf:
                    color_count += 1
                    img = np.asanyarray(cf.get_data())
                    img = rotate_img(img, rotate)
                    img = np.ascontiguousarray(img)
                    # Store latest colour frame for HTTP endpoint
                    service.update_color_rgb(img)
                    if color_writer:
                        color_writer = _safe_write(color_writer, img.tobytes(), color_fifo, 'color')

            if depth_mode:
                df = frames.get_depth_frame()
                if df and depth_scale is not None:
                    z16 = np.asanyarray(df.get_data())
                    service.update_depth_from_z16(z16, depth_scale)
                    depth_count += 1
                    if depth_writer:
                        c = colorizer.process(df)
                        img = np.asanyarray(c.get_data()).astype(np.uint8)  # HxWx3
                        img = rotate_img(img, rotate)
                        if service.depth_flip180:
                            img = np.rot90(img, 2)  # 180° to match color sensor orientation
                        img = np.ascontiguousarray(img)
                        depth_writer = _safe_write(depth_writer, img.tobytes(), depth_fifo, 'depth')

            if ir_mode:
                irf = frames.get_infrared_frame(ir_mode.stream_index)
                if irf:
                    ir_count += 1
                    if ir_writer:
                        ir = np.asanyarray(irf.get_data())  # HxW, uint8
                        ir = rotate_img(ir, rotate)
                        ir = np.ascontiguousarray(ir)
                        ir_writer = _safe_write(ir_writer, ir.tobytes(), ir_fifo, 'ir')

            now = time.time()
            if now - last_report >= 2.0:
                print(
                    f"[stats] color={color_count} depth={depth_count} ir={ir_count} "
                    f"in last {now - last_report:.1f}s",
                    flush=True,
                )
                color_count = depth_count = ir_count = 0
                last_report = now

    finally:
        pipeline.stop()
        if color_writer:
            color_writer.close()
        if depth_writer:
            depth_writer.close()
        if ir_writer:
            ir_writer.close()
        print("Pipeline stopped.")


def main():
    parser = argparse.ArgumentParser(description="RealSense FIFO streamer")
    parser.add_argument("--list-modes", action="store_true")

    # эти аргументы всё ещё можно использовать вручную, но мы их хардкодим ниже
    parser.add_argument("--color-index", type=int, default=-1)
    parser.add_argument("--depth-index", type=int, default=-1)
    parser.add_argument("--ir-index", type=int, default=-1)
    parser.add_argument("--color-fifo", type=str, default="/run/realsense/color.fifo")
    parser.add_argument("--depth-fifo", type=str, default="/run/realsense/depth.fifo")
    parser.add_argument("--ir-fifo", type=str, default="/run/realsense/ir.fifo")

    args = parser.parse_args()

    if args.list_modes:
        modes = probe_modes()
        print_modes(modes)
        return

    rotate = "cw"  # наш фиксированный поворот

    run_pipeline(
        color_idx=90,                    # как у тебя было
        depth_idx=18,
        # ir_idx=19,
        ir_idx=-1,
        color_fifo="/run/realsense/color.fifo",
        depth_fifo="/run/realsense/depth.fifo",
        # ir_fifo="/run/realsense/ir.fifo",
        ir_fifo=None,
        rotate=rotate,
        depth_flip180=False,             # color и depth sensor одинаково ориентированы после cw-поворота
    )


if __name__ == "__main__":
    main()


