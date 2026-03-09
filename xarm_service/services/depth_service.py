"""
DepthService — async depth camera client (HTTP).

Connects to the realsense‑mux HTTP service for depth data:
  • ``GET /depth/frame``   — full raw uint16 frame (614 400 bytes)
  • ``GET /depth?x=&y=``   — single‑pixel depth query
  • object detection via depth‑based segmentation
  • grasp‑point computation for vacuum grippers
  • frame capture & TTL cache for grasp analysis
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import cv2
import numpy as np

from app.config import (
    DEPTH_BASE_URL,
    DEPTH_FRAME_WIDTH,
    DEPTH_FRAME_HEIGHT,
    DEPTH_FRAME_BYTES,
    DEPTH_HTTP_TIMEOUT_S,
    CAMERA_FX,
    CAMERA_FY,
    CAMERA_CX,
    CAMERA_CY,
    DEPTH_SCALE,
    DEPTH_SEGMENT_THRESHOLD_MM,
    DEPTH_MIN_RANGE_MM,
    DEPTH_MIN_CONTOUR_AREA_PX,
    DEPTH_AVG_FRAMES,
    DEPTH_CALIB_A,
    DEPTH_CALIB_B,
    FRAME_TTL_S,
)
from models.grasp_types import (
    CameraIntrinsics,
    FrameContext,
    GraspPoint,
    ObjectDetection,
    PixelCoord,
    Point3D,
)

logger = logging.getLogger(__name__)


class DepthService:
    """Async depth camera service — singleton created via ``get_depth_service()``."""

    def __init__(self, base_url: str = DEPTH_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._intrinsics = CameraIntrinsics(
            fx=CAMERA_FX, fy=CAMERA_FY,
            cx=CAMERA_CX, cy=CAMERA_CY,
            width=DEPTH_FRAME_WIDTH, height=DEPTH_FRAME_HEIGHT,
            depth_scale=DEPTH_SCALE,
        )
        # Frame TTL cache: {frame_id: (np.ndarray, timestamp_s)}
        self._frame_cache: Dict[str, Tuple[np.ndarray, float]] = {}

    @property
    def intrinsics(self) -> CameraIntrinsics:
        return self._intrinsics

    # ── Frame capture ──────────────────────────────────────────────────────

    async def get_depth_frame(self) -> np.ndarray:
        """Capture a full depth frame (uint16, shape H×W) via HTTP.

        Uses ``GET /depth/frame?format=json`` which returns JSON with
        base64-encoded float32 depth data in metres.  The result is
        converted to uint16 raw values compatible with ``DEPTH_SCALE``
        (i.e. ``depth_mm ≈ raw * DEPTH_SCALE``).

        Raises ``ConnectionError`` when the camera is not reachable.
        """
        import base64

        import httpx

        url = f"{self._base_url}/depth/frame"
        try:
            async with httpx.AsyncClient(timeout=DEPTH_HTTP_TIMEOUT_S) as client:
                resp = await client.get(url, params={"format": "json"})
                resp.raise_for_status()
        except Exception as exc:
            raise ConnectionError(
                f"Depth camera not reachable at {url}: {exc}"
            ) from exc

        body = resp.json()
        raw = base64.b64decode(body["data"])
        arr_f32 = np.frombuffer(raw, dtype=np.float32)

        expected_pixels = DEPTH_FRAME_WIDTH * DEPTH_FRAME_HEIGHT
        if arr_f32.size != expected_pixels:
            raise ConnectionError(
                f"Depth frame pixel count mismatch: got {arr_f32.size}, "
                f"expected {expected_pixels}"
            )

        arr_f32 = arr_f32.reshape((DEPTH_FRAME_HEIGHT, DEPTH_FRAME_WIDTH))

        # Convert float32 metres → uint16 raw values.
        # Pipeline convention: depth_mm = raw_uint16 * DEPTH_SCALE
        arr_raw = (arr_f32 * 1000.0) / DEPTH_SCALE
        return np.clip(arr_raw, 0, 65535).astype(np.uint16)

    # ── Frame capture & TTL cache ──────────────────────────────────────────

    async def capture_frame(self) -> Tuple[str, np.ndarray, FrameContext]:
        """Capture a depth frame, cache it with a TTL, and return (frame_id, frame, context).

        The cached frame can be retrieved later via ``get_cached_frame(frame_id)``
        within the TTL window (default 5 s).
        """
        frame = await self.get_depth_frame()
        frame_id = uuid.uuid4().hex[:12]
        ts_ms = int(time.time() * 1000)
        self._frame_cache[frame_id] = (frame, time.time())
        self._evict_expired()

        ci = self._intrinsics
        ctx = FrameContext(
            frame_id=frame_id,
            timestamp_ms=ts_ms,
            intrinsics={"fx": ci.fx, "fy": ci.fy, "cx": ci.cx, "cy": ci.cy},
            depth_scale=ci.depth_scale,
            resolution=(ci.width, ci.height),
            frame_reused=True,
        )
        return frame_id, frame, ctx

    def get_cached_frame(self, frame_id: str) -> Optional[np.ndarray]:
        """Return a cached frame if it exists and is within TTL, else None."""
        entry = self._frame_cache.get(frame_id)
        if entry is None:
            return None
        frame, cached_at = entry
        if (time.time() - cached_at) > FRAME_TTL_S:
            del self._frame_cache[frame_id]
            return None
        return frame

    def _evict_expired(self) -> None:
        """Remove frames older than TTL."""
        now = time.time()
        expired = [fid for fid, (_, ts) in self._frame_cache.items() if (now - ts) > FRAME_TTL_S]
        for fid in expired:
            del self._frame_cache[fid]

    async def get_single_depth(self, x: int, y: int) -> float:
        """Query depth at a single pixel via ``GET /depth?x=&y=``."""
        import httpx

        url = f"{self._base_url}/depth"
        try:
            async with httpx.AsyncClient(timeout=DEPTH_HTTP_TIMEOUT_S) as client:
                resp = await client.get(url, params={"x": x, "y": y})
                resp.raise_for_status()
                data = resp.json()
                return float(data.get("depth", 0.0))
        except Exception as exc:
            logger.warning("get_single_depth(%d, %d) failed: %s", x, y, exc)
            return 0.0

    async def get_averaged_depth(
        self, x: int, y: int, n_frames: int = DEPTH_AVG_FRAMES,
    ) -> float:
        """Return averaged depth at pixel (x, y) over *n_frames* full frames."""
        samples: List[float] = []
        for _ in range(n_frames):
            try:
                frame = await self.get_depth_frame()
                val = float(frame[y, x])
                if val > 0:
                    samples.append(val)
            except ConnectionError:
                break
        if not samples:
            return 0.0
        return self._robust_mean(samples)

    # ── Object detection ───────────────────────────────────────────────────

    def detect_objects(
        self, frame: np.ndarray, *, max_objects: int = 10,
    ) -> List[ObjectDetection]:
        """
        Segment foreground objects from a depth frame.

        Strategy: histogram‑based floor detection → foreground mask → contours.
        """
        ci = self._intrinsics
        # Eliminate invalid pixels: zero (no data) and below min range (blind zone)
        valid_mask = frame > DEPTH_MIN_RANGE_MM
        if not np.any(valid_mask):
            return []

        # Find the dominant depth (surface/floor) via histogram peaks
        valid_depths = frame[valid_mask].astype(np.float32)
        median_depth = float(np.median(valid_depths))

        # Foreground = significantly closer than the dominant depth
        threshold = DEPTH_SEGMENT_THRESHOLD_MM
        fg_mask = np.zeros_like(frame, dtype=np.uint8)
        fg_mask[(frame > DEPTH_MIN_RANGE_MM) & (frame < median_depth - threshold)] = 255

        # Morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: List[ObjectDetection] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < DEPTH_MIN_CONTOUR_AREA_PX:
                continue
            bx, by, bw, bh = cv2.boundingRect(cnt)
            # Average depth inside contour
            cnt_mask = np.zeros_like(frame, dtype=np.uint8)
            cv2.drawContours(cnt_mask, [cnt], -1, 255, -1)
            region = frame[(cnt_mask == 255) & (frame > DEPTH_MIN_RANGE_MM)]
            if len(region) == 0:
                continue
            obj_depth = float(np.median(region)) * ci.depth_scale

            # Real‑world size
            w_mm = (bw * obj_depth) / ci.fx
            h_mm = (bh * obj_depth) / ci.fy

            cx_px = bx + bw // 2
            cy_px = by + bh // 2

            detections.append(ObjectDetection(
                center_px=PixelCoord(cx_px, cy_px),
                bbox=(bx, by, bw, bh),
                depth_mm=obj_depth,
                size_mm=(w_mm, h_mm),
                contour_area_px=area,
            ))

        # Sort by area descending (largest = most graspable by vacuum)
        detections.sort(key=lambda d: d.contour_area_px, reverse=True)
        return detections[:max_objects]

    def detect_at_point(
        self, frame: np.ndarray, x: int, y: int,
    ) -> Optional[ObjectDetection]:
        """
        Detect the object under pixel (x, y) using depth‑threshold segmentation.

        This mirrors the original ``mouse_callback`` logic but is cleaner.
        """
        ci = self._intrinsics
        if y < 0 or y >= ci.height or x < 0 or x >= ci.width:
            return None
        depth_val = int(frame[y, x])
        if depth_val == 0 or depth_val < DEPTH_MIN_RANGE_MM:
            return None

        threshold = DEPTH_SEGMENT_THRESHOLD_MM
        lo = max(0, depth_val - threshold)
        hi = depth_val + threshold

        mask = cv2.inRange(frame, int(lo), int(hi))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            if cv2.pointPolygonTest(cnt, (float(x), float(y)), False) < 0:
                continue
            area = cv2.contourArea(cnt)
            if area < DEPTH_MIN_CONTOUR_AREA_PX:
                continue
            bx, by, bw, bh = cv2.boundingRect(cnt)
            obj_depth = float(depth_val) * ci.depth_scale
            w_mm = (bw * obj_depth) / ci.fx
            h_mm = (bh * obj_depth) / ci.fy
            cx_px = bx + bw // 2
            cy_px = by + bh // 2
            return ObjectDetection(
                center_px=PixelCoord(cx_px, cy_px),
                bbox=(bx, by, bw, bh),
                depth_mm=obj_depth,
                size_mm=(w_mm, h_mm),
                contour_area_px=area,
            )
        return None

    # ── Grasp‑point selection ──────────────────────────────────────────────

    def compute_grasp_point(self, det: ObjectDetection) -> GraspPoint:
        """
        For a vacuum gripper the optimal grasp point is the geometric centre
        of the object (maximises contact area with the suction cup).
        """
        ci = self._intrinsics
        px = det.center_px

        # Camera‑frame offset from optical centre (mm)
        dx_mm = ((px.x - ci.cx) * det.depth_mm) / ci.fx
        dy_mm = ((px.y - ci.cy) * det.depth_mm) / ci.fy

        return GraspPoint(
            pixel=px,
            position_mm=Point3D(x=dx_mm, y=dy_mm, z=det.depth_mm),
            approach_depth_mm=det.depth_mm,
            object=det,
        )

    # ── Camera shift (alignment) ───────────────────────────────────────────

    def calculate_camera_shift(
        self, object_px: PixelCoord, depth_mm: float,
    ) -> Tuple[float, float]:
        """
        How many mm to move the camera (tool) so the object centre
        aligns with the optical centre.

        Returns (delta_x_mm, delta_y_mm) in tool‑frame convention
        (may need axis swap depending on mount — currently: cam_x → tool_-y,
        cam_y → tool_x  matching the legacy measure_xy.py convention).
        """
        ci = self._intrinsics
        dx_px = object_px.x - ci.cx
        dy_px = object_px.y - ci.cy
        dx_mm = (dx_px * depth_mm) / ci.fx
        dy_mm = (dy_px * depth_mm) / ci.fy
        # Legacy axis mapping: tool_x = -dy_mm, tool_y = dx_mm
        return (-dy_mm, dx_mm)

    # ── Depth calibration ──────────────────────────────────────────────────

    @staticmethod
    def calibrate_distance(raw_distance: float) -> float:
        """Apply linear calibration: true ≈ a * measured + b."""
        return DEPTH_CALIB_A * raw_distance + DEPTH_CALIB_B

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _robust_mean(values: List[float]) -> float:
        """Mean after removing min/max outliers (when ≥4 samples)."""
        if len(values) <= 3:
            return float(np.mean(values))
        arr = sorted(values)
        trimmed = arr[1:-1]
        return float(np.mean(trimmed))


# ── Singleton accessor ─────────────────────────────────────────────────────
_depth_service: Optional[DepthService] = None


def get_depth_service() -> DepthService:
    global _depth_service
    if _depth_service is None:
        _depth_service = DepthService()
    return _depth_service
