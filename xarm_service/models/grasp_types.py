"""Data models for the smart grasp pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


# ── Pixel / 3‑D coordinates ────────────────────────────────────────────────
@dataclass(frozen=True)
class PixelCoord:
    """Column (x) / row (y) in image space."""
    x: int
    y: int


@dataclass(frozen=True)
class Point3D:
    """Metric point in camera (or base) frame, millimetres."""
    x: float
    y: float
    z: float


# ── Camera intrinsics ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class CameraIntrinsics:
    fx: float = 380.4253845214844
    fy: float = 380.4253845214844
    cx: float = 320.0
    cy: float = 240.0
    width: int = 640
    height: int = 480
    depth_scale: float = 0.9  # raw‑unit → mm multiplier


# ── Object detection result ────────────────────────────────────────────────
@dataclass
class ObjectDetection:
    """Single object extracted from a depth frame."""
    center_px: PixelCoord
    bbox: Tuple[int, int, int, int]          # x, y, w, h in pixels
    depth_mm: float                           # averaged depth at object surface
    size_mm: Tuple[float, float]              # (width_mm, height_mm) real‑world
    contour_area_px: float                    # contour area in pixels
    confidence: float = 1.0                   # 0‑1 (reserved for future ML scorer)


# ── Grasp point ────────────────────────────────────────────────────────────
@dataclass
class GraspPoint:
    """Where and how to grasp."""
    pixel: PixelCoord                         # target in image
    position_mm: Point3D                      # offset in camera/tool frame
    approach_depth_mm: float                  # how far to descend from current Z
    object: ObjectDetection                   # the parent detection


# ── Gripper feedback ───────────────────────────────────────────────────────
class GripperFeedback(str, Enum):
    """Aggregated vacuum gripper state."""
    UNKNOWN = "UNKNOWN"
    OFF = "OFF"
    VACUUM_NO_PART = "VACUUM_NO_PART"
    PART_GRIPPED = "PART_GRIPPED"
    ERROR = "ERROR"


@dataclass
class GripperStatus:
    """Full gripper status snapshot."""
    active: bool = False
    feedback: GripperFeedback = GripperFeedback.UNKNOWN
    vacuum_level: Optional[float] = None      # kPa, if readable
    modbus_raw: Optional[List[int]] = None     # last raw response


# ── Grasp verification ─────────────────────────────────────────────────────
@dataclass
class VerificationResult:
    """Outcome of the post‑grip check."""
    object_held: bool
    vacuum_ok: bool = False
    torque_delta_ok: bool = False
    depth_vanished: bool = False
    torque_deltas: Optional[List[float]] = None
    detail: str = ""


# ── Grasp pipeline result ──────────────────────────────────────────────────
class GraspOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    NO_OBJECT_DETECTED = "NO_OBJECT_DETECTED"
    ALIGNMENT_FAILED = "ALIGNMENT_FAILED"
    APPROACH_FAILED = "APPROACH_FAILED"
    GRIP_FAILED = "GRIP_FAILED"
    VERIFY_FAILED = "VERIFY_FAILED"
    ABORTED = "ABORTED"


@dataclass
class GraspResult:
    """Final result returned by the GraspPlanner pipeline."""
    outcome: GraspOutcome
    attempts: int = 0
    detection: Optional[ObjectDetection] = None
    verification: Optional[VerificationResult] = None
    error_message: Optional[str] = None
    elapsed_s: float = 0.0
