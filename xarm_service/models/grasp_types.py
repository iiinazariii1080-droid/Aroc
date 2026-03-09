"""Data models for the smart grasp pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


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


# ── Surface normal ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SurfaceNormal:
    """Unit normal vector in camera frame, oriented toward camera."""
    nx: float
    ny: float
    nz: float
    frame_id: str = "camera"
    oriented_toward_camera: bool = True


# ── Gripper footprint ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class GripperFootprint:
    cup_centers: Tuple[Point3D, ...]
    cup_radius_mm: float
    jaw_spacing_mm: float


# ── Cup placement ──────────────────────────────────────────────────────────
@dataclass
class CupPlacement:
    """Single suction cup placement evaluation."""
    center_px: PixelCoord
    position_mm: Point3D
    seal_score: float          # perimeter seal integrity [0..1]
    flatness_residual: float   # RMS residual to local fitted plane (mm)
    edge_distance_mm: float    # distance from cup edge to object edge
    lever_arm_mm: float        # distance from cup center to object centroid


# ── Dual cup result ────────────────────────────────────────────────────────
@dataclass
class DualCupResult:
    """Evaluation of a dual-cup placement at a specific pose (position + yaw)."""
    cup_a: CupPlacement
    cup_b: CupPlacement
    activation_mode: str       # "BOTH" | "CUP_A_ONLY" | "CUP_B_ONLY" | "NONE"
    combined_seal: float       # weighted mean of both cups' seal scores
    wrench_resistance: float   # f(normal angle, grip span, normal consistency)
    yaw_deg: float = 0.0      # yaw angle used for this placement


# ── Collision zone ─────────────────────────────────────────────────────────
@dataclass
class CollisionZone:
    """An obstacle detected in the approach corridor."""
    bbox_3d: Tuple[Point3D, Point3D]   # min/max corners in camera frame
    depth_range_mm: Tuple[float, float]
    perpendicular_dist_mm: float       # 3D distance to approach axis
    risk_level: str                    # "CLEAR" | "MARGINAL" | "BLOCKED"


# ── Confidence breakdown ───────────────────────────────────────────────────
@dataclass
class ConfidenceBreakdown:
    """Decomposed confidence scores per SuctionNet pattern."""
    seal_proxy: float        # weighted mean of cup seal scores
    stability_proxy: float   # f(wrench, flatness, lever_arm, normal_consistency)
    corridor_proxy: float    # 1.0=CLEAR, 0.7=MARGINAL, 0.0=BLOCKED
    reachability_proxy: float = 1.0  # 1.0=OK, 0.0=IK infeasible


# ── Frame context ──────────────────────────────────────────────────────────
@dataclass
class FrameContext:
    """Metadata about the depth frame used for analysis."""
    frame_id: str
    timestamp_ms: int
    intrinsics: Dict[str, float]  # {fx, fy, cx, cy}
    depth_scale: float
    resolution: Tuple[int, int]
    frame_reused: bool = True


# ── Transform info ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TransformInfo:
    """Ties results to calibration state for drift detection."""
    T_gripper_cam_version: str = "hardcoded_v1"


# ── Analysis status ────────────────────────────────────────────────────────
@dataclass
class AnalysisStatus:
    """Machine-readable analysis outcome."""
    status: str                  # "OK" | "DEGRADED" | "FAILED"
    reason_codes: List[str] = field(default_factory=list)


# ── Full grasp analysis result ─────────────────────────────────────────────
@dataclass
class GraspAnalysis:
    """Complete result of the grasp analysis pipeline."""
    target: PixelCoord
    surface_normal: SurfaceNormal
    roll_correction_deg: float
    pitch_correction_deg: float
    yaw_correction_deg: float
    xy_correction_mm: Tuple[float, float]  # (dx, dy) in camera frame
    dual_cup_result: Optional[DualCupResult]
    collision_zones: List[CollisionZone]
    collision_risk: str                     # "CLEAR" | "MARGINAL" | "BLOCKED"
    approach_clear: bool
    confidence: float
    confidence_breakdown: ConfidenceBreakdown
    frame_context: FrameContext
    transform_info: TransformInfo
    analysis_status: AnalysisStatus


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
    part_present: bool = False                 # PP setpoint achieved
    part_secured: bool = False                 # PS setpoint achieved
    energy_saving: bool = False                # ES setpoint achieved
    motor_stall: bool = False                  # motor stall detected
    pcb_temperature: int = 0                   # °C
    membrane_hours: int = 0                    # hours to membrane service
    membrane_warn: bool = False                # membrane service warning
    sensor_supported: bool = False             # True if PDI reads work
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
