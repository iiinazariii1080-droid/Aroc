"""
GraspAnalysisService — depth-frame analysis for optimal dual-cup grasp placement.

Algorithms:
  3a. Seed-based region growing segmentation
  3b. Surface normal estimation (global PCA + local per-cup)
  3c. Dual-cup placement with seal-ring scoring + yaw search
  3d. Collision corridor scan in 3D
  3e. IK feasibility placeholder
  3f. Compose GraspAnalysis result
"""
from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

import cv2
import numpy as np

from app.config import (
    CAMERA_CX,
    CAMERA_CY,
    CAMERA_FX,
    CAMERA_FY,
    CUP_GRID_STEP_MM,
    DEPTH_MIN_RANGE_MM,
    DEPTH_GROW_THRESHOLD_MM,
    DEPTH_SCALE,
    GRIPPER_CUP_DIAMETER_MM,
    GRIPPER_ENVELOPE_RADIUS_MM,
    GRIPPER_JAW_SPACING_MM,
    MASK_DILATE_PX,
    MAX_PITCH_CORRECTION_DEG,
    MAX_ROLL_CORRECTION_DEG,
    MAX_YAW_CORRECTION_DEG,
    MIN_MASK_POINTS,
    NORMAL_CONSISTENCY_THRESHOLD_DEG,
    SEAL_BREAK_DEPTH_JUMP_MM,
    SEAL_PERIMETER_POINTS,
    YAW_SEARCH_RANGE_DEG,
    YAW_SEARCH_STEP_DEG,
)
from models.grasp_types import (
    AnalysisStatus,
    CollisionZone,
    ConfidenceBreakdown,
    CupPlacement,
    DualCupResult,
    FrameContext,
    GraspAnalysis,
    PixelCoord,
    Point3D,
    SurfaceNormal,
    TransformInfo,
)
from services.depth_service import DepthService

logger = logging.getLogger(__name__)

_DEG = 180.0 / math.pi


class GraspAnalysisService:
    """Analyses a depth frame to find optimal dual-cup grasp placement."""

    def __init__(self, depth_service: DepthService) -> None:
        self._ds = depth_service
        # Overridable algorithm params (set via set_overrides before analyze)
        self._yaw_search_step: float = YAW_SEARCH_STEP_DEG
        self._collision_envelope: float = GRIPPER_ENVELOPE_RADIUS_MM
        self._seal_perimeter_points: int = SEAL_PERIMETER_POINTS
        self._cup_grid_step: float = CUP_GRID_STEP_MM

    def set_overrides(
        self,
        yaw_search_step_deg: Optional[float] = None,
        collision_envelope_mm: Optional[float] = None,
        seal_perimeter_points: Optional[int] = None,
        cup_grid_step_mm: Optional[float] = None,
    ) -> None:
        """Override algorithm parameters from trajectory config."""
        if yaw_search_step_deg is not None:
            self._yaw_search_step = yaw_search_step_deg
        if collision_envelope_mm is not None:
            self._collision_envelope = collision_envelope_mm
        if seal_perimeter_points is not None:
            self._seal_perimeter_points = seal_perimeter_points
        if cup_grid_step_mm is not None:
            self._cup_grid_step = cup_grid_step_mm

    # ────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ────────────────────────────────────────────────────────────────────────

    async def analyze(
        self,
        frame: np.ndarray,
        target_x_norm: float,
        target_y_norm: float,
        frame_context: FrameContext,
    ) -> GraspAnalysis:
        """Run full grasp analysis on *frame* targeting the normalised point."""
        ci = self._ds.intrinsics
        target_px = PixelCoord(
            x=int(target_x_norm / 100.0 * ci.width),
            y=int(target_y_norm / 100.0 * ci.height),
        )
        reason_codes: List[str] = []

        # 3a — Segmentation
        mask, median_depth = self._segment_object(frame, target_px)
        if mask is None or median_depth <= 0:
            return self._failed_result(target_px, frame_context, ["SEGMENTATION_FAILED"])
        mask_count = int(np.count_nonzero(mask))
        if mask_count < MIN_MASK_POINTS:
            reason_codes.append("SEGMENTATION_TOO_SMALL")

        # 3b — Global surface normal
        normal, eigenvalue_ratio = self._estimate_global_normal(frame, mask, median_depth)
        if normal is None:
            reason_codes.append("NORMAL_DEGENERATE")
            normal = SurfaceNormal(0.0, 0.0, 1.0)

        roll_deg, pitch_deg = self._normal_to_roll_pitch(normal)

        # 3c — Dual-cup placement with yaw search
        dual_cup, yaw_deg, xy_corr = self._find_best_dual_cup(
            frame, mask, median_depth, target_px,
        )
        if dual_cup is None:
            reason_codes.append("NO_VALID_PLACEMENT")
            yaw_deg = 0.0
            xy_corr = (0.0, 0.0)

        # 3d — Collision corridor scan
        collision_zones, collision_risk = self._scan_corridor(
            frame, mask, median_depth, target_px,
        )
        approach_clear = collision_risk == "CLEAR"

        # 3b+ — Local normal consistency for winner
        normal_consistency_deg = 0.0
        if dual_cup is not None:
            ci = self._ds.intrinsics
            cup_radius_px = max(1, int(round(
                (GRIPPER_CUP_DIAMETER_MM / 2.0) * ci.fx / median_depth
            )))
            n_a, _ = self._estimate_local_normal(
                frame, mask, dual_cup.cup_a.center_px, cup_radius_px,
            )
            n_b, _ = self._estimate_local_normal(
                frame, mask, dual_cup.cup_b.center_px, cup_radius_px,
            )
            if n_a is not None and n_b is not None:
                cos_angle = float(np.clip(np.dot(n_a, n_b), -1.0, 1.0))
                normal_consistency_deg = math.acos(cos_angle) * _DEG
                if normal_consistency_deg > NORMAL_CONSISTENCY_THRESHOLD_DEG:
                    reason_codes.append("NORMAL_INCONSISTENT")
                    logger.info(
                        "Normal consistency: %.1f° between cup normals (threshold %.1f°)",
                        normal_consistency_deg, NORMAL_CONSISTENCY_THRESHOLD_DEG,
                    )

        # 3f — Compose result
        seal_proxy = dual_cup.combined_seal if dual_cup else 0.0
        # Stability: wrench × flatness factor × normal consistency factor
        if dual_cup is not None:
            wrench = min(1.0, dual_cup.wrench_resistance)
            avg_flatness = (dual_cup.cup_a.flatness_residual + dual_cup.cup_b.flatness_residual) / 2.0
            flatness_factor = max(0.0, 1.0 - avg_flatness / 5.0)  # degrade above 5mm RMS
            consistency_factor = max(0.0, 1.0 - normal_consistency_deg / 30.0)  # full penalty at 30°
            stability_proxy = wrench * flatness_factor * consistency_factor
        else:
            stability_proxy = 0.0
        corridor_proxy = {"CLEAR": 1.0, "MARGINAL": 0.7, "BLOCKED": 0.0}.get(collision_risk, 0.0)
        breakdown = ConfidenceBreakdown(
            seal_proxy=seal_proxy,
            stability_proxy=stability_proxy,
            corridor_proxy=corridor_proxy,
            reachability_proxy=1.0,
        )
        confidence = seal_proxy * stability_proxy * corridor_proxy

        status = "OK"
        if reason_codes:
            status = "DEGRADED" if dual_cup is not None else "FAILED"

        return GraspAnalysis(
            target=target_px,
            surface_normal=normal,
            roll_correction_deg=roll_deg,
            pitch_correction_deg=pitch_deg,
            yaw_correction_deg=yaw_deg,
            xy_correction_mm=xy_corr,
            dual_cup_result=dual_cup,
            collision_zones=collision_zones,
            collision_risk=collision_risk,
            approach_clear=approach_clear,
            confidence=confidence,
            confidence_breakdown=breakdown,
            frame_context=frame_context,
            transform_info=TransformInfo(),
            analysis_status=AnalysisStatus(status=status, reason_codes=reason_codes),
        )

    # ────────────────────────────────────────────────────────────────────────
    # 3a. Object segmentation — seed-based region growing
    # ────────────────────────────────────────────────────────────────────────

    def _segment_object(
        self, frame: np.ndarray, target: PixelCoord,
    ) -> Tuple[Optional[np.ndarray], float]:
        """Return (binary mask uint8, median_depth_mm) or (None, 0)."""
        h, w = frame.shape[:2]
        tx, ty = target.x, target.y
        if tx < 0 or tx >= w or ty < 0 or ty >= h:
            return None, 0.0
        seed_depth = int(frame[ty, tx])
        if seed_depth <= DEPTH_MIN_RANGE_MM:
            return None, 0.0

        threshold = DEPTH_GROW_THRESHOLD_MM
        visited = np.zeros((h, w), dtype=np.uint8)
        mask = np.zeros((h, w), dtype=np.uint8)
        queue: List[Tuple[int, int]] = [(tx, ty)]
        visited[ty, tx] = 1
        mask[ty, tx] = 255

        while queue:
            cx, cy = queue.pop()
            cur_depth = int(frame[cy, cx])
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < w and 0 <= ny < h and not visited[ny, nx]:
                    visited[ny, nx] = 1
                    nd = int(frame[ny, nx])
                    if nd > DEPTH_MIN_RANGE_MM and abs(nd - cur_depth) <= threshold:
                        mask[ny, nx] = 255
                        queue.append((nx, ny))

        # Morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        count = int(np.count_nonzero(mask))
        if count < MIN_MASK_POINTS:
            # Fallback to threshold-band approach
            lo = max(0, seed_depth - threshold)
            hi = seed_depth + threshold
            mask = cv2.inRange(frame, int(lo), int(hi))
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            count = int(np.count_nonzero(mask))
            if count < MIN_MASK_POINTS:
                return None, 0.0

        region = frame[mask > 0]
        region = region[region > DEPTH_MIN_RANGE_MM]
        if len(region) == 0:
            return None, 0.0
        median_depth = float(np.median(region)) * DEPTH_SCALE
        return mask, median_depth

    # ────────────────────────────────────────────────────────────────────────
    # 3b. Surface normal estimation — global PCA/SVD
    # ────────────────────────────────────────────────────────────────────────

    def _estimate_global_normal(
        self, frame: np.ndarray, mask: np.ndarray, median_depth: float,
    ) -> Tuple[Optional[SurfaceNormal], float]:
        """PCA on all mask points → (normal, eigenvalue_ratio)."""
        points_3d = self._unproject_mask(frame, mask)
        if len(points_3d) < MIN_MASK_POINTS:
            return None, 0.0

        centroid = points_3d.mean(axis=0)
        centered = points_3d - centroid
        # SVD on the covariance-like matrix
        _, s, Vt = np.linalg.svd(centered, full_matrices=False)
        normal_vec = Vt[2]  # smallest singular value → normal direction

        # Sign: orient toward camera (dot(n, [0,0,-1]) > 0 ⇒ nz < 0 in camera frame)
        if normal_vec[2] > 0:
            normal_vec = -normal_vec

        eigenvalue_ratio = float(s[2] / s[1]) if s[1] > 1e-12 else 0.0

        return SurfaceNormal(
            nx=float(normal_vec[0]),
            ny=float(normal_vec[1]),
            nz=float(normal_vec[2]),
        ), eigenvalue_ratio

    def _estimate_local_normal(
        self, frame: np.ndarray, mask: np.ndarray,
        center_px: PixelCoord, radius_px: int,
    ) -> Tuple[Optional[np.ndarray], float]:
        """Fit a plane to points within a circle on the mask. Return (normal_vec, rms_residual)."""
        h, w = frame.shape[:2]
        # Create circular ROI mask
        circle_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(circle_mask, (center_px.x, center_px.y), radius_px, 255, -1)
        combined = cv2.bitwise_and(mask, circle_mask)

        points_3d = self._unproject_mask(frame, combined)
        if len(points_3d) < 10:
            return None, 0.0

        centroid = points_3d.mean(axis=0)
        centered = points_3d - centroid
        _, s, Vt = np.linalg.svd(centered, full_matrices=False)
        normal_vec = Vt[2]
        if normal_vec[2] > 0:
            normal_vec = -normal_vec

        # RMS residual = distances to the fitted plane
        residuals = np.abs(centered @ normal_vec)
        rms = float(np.sqrt(np.mean(residuals ** 2)))
        return normal_vec, rms

    def _unproject_mask(self, frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Unproject masked depth pixels to 3D points (Nx3, mm)."""
        ci = self._ds.intrinsics
        ys, xs = np.where(mask > 0)
        if len(ys) == 0:
            return np.empty((0, 3), dtype=np.float32)
        depths = frame[ys, xs].astype(np.float32) * ci.depth_scale
        valid = depths > DEPTH_MIN_RANGE_MM * ci.depth_scale
        xs, ys, depths = xs[valid], ys[valid], depths[valid]
        if len(xs) == 0:
            return np.empty((0, 3), dtype=np.float32)
        X = (xs.astype(np.float32) - ci.cx) * depths / ci.fx
        Y = (ys.astype(np.float32) - ci.cy) * depths / ci.fy
        Z = depths
        return np.stack([X, Y, Z], axis=1)

    def _normal_to_roll_pitch(self, n: SurfaceNormal) -> Tuple[float, float]:
        """Convert surface normal to roll/pitch corrections (degrees), clamped."""
        # For a surface with normal (nx, ny, nz) pointing toward camera:
        # pitch corresponds to tilt around camera X-axis → related to ny/nz
        # roll corresponds to tilt around camera Y-axis → related to nx/nz
        nz = n.nz if abs(n.nz) > 1e-6 else -1e-6
        roll_deg = math.atan2(n.nx, -nz) * _DEG
        pitch_deg = math.atan2(n.ny, -nz) * _DEG
        roll_deg = max(-MAX_ROLL_CORRECTION_DEG, min(MAX_ROLL_CORRECTION_DEG, roll_deg))
        pitch_deg = max(-MAX_PITCH_CORRECTION_DEG, min(MAX_PITCH_CORRECTION_DEG, pitch_deg))
        return roll_deg, pitch_deg

    # ────────────────────────────────────────────────────────────────────────
    # 3c. Dual-cup placement — seal-ring + wrench + yaw search
    # ────────────────────────────────────────────────────────────────────────

    def _find_best_dual_cup(
        self,
        frame: np.ndarray,
        mask: np.ndarray,
        median_depth: float,
        target: PixelCoord,
    ) -> Tuple[Optional[DualCupResult], float, Tuple[float, float]]:
        """Search for best dual-cup placement. Returns (result, yaw_deg, xy_correction_mm)."""
        ci = self._ds.intrinsics
        cup_radius_mm = GRIPPER_CUP_DIAMETER_MM / 2.0
        jaw_half_mm = GRIPPER_JAW_SPACING_MM / 2.0

        # Convert mm to pixels at object depth
        cup_radius_px = max(1, int(round(cup_radius_mm * ci.fx / median_depth)))
        jaw_half_px = max(1, int(round(jaw_half_mm * ci.fx / median_depth)))
        grid_step_px = max(1, int(round(self._cup_grid_step * ci.fx / median_depth)))

        # Distance transform for edge distance queries
        dist_transform = cv2.distanceTransform(mask, cv2.DIST_L2, 5)

        # Erode mask by cup radius to get valid center positions
        erode_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cup_radius_px * 2, cup_radius_px * 2))
        eroded = cv2.erode(mask, erode_kernel, iterations=1)

        # Object centroid for lever_arm calculation
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None, 0.0, (0.0, 0.0)
        centroid_x = float(np.mean(xs))
        centroid_y = float(np.mean(ys))

        # Valid candidate positions from eroded mask (down-sampled by grid step)
        cand_ys, cand_xs = np.where(eroded > 0)
        if len(cand_xs) == 0:
            # If erosion removes everything, try original mask
            cand_ys, cand_xs = np.where(mask > 0)
            if len(cand_xs) == 0:
                return None, 0.0, (0.0, 0.0)

        # Sub-sample candidates to grid
        if grid_step_px > 1:
            keep = ((cand_xs % grid_step_px == 0) & (cand_ys % grid_step_px == 0))
            cand_xs = cand_xs[keep]
            cand_ys = cand_ys[keep]
            if len(cand_xs) == 0:
                return None, 0.0, (0.0, 0.0)

        # Yaw search
        yaw_min = -YAW_SEARCH_RANGE_DEG
        yaw_max = YAW_SEARCH_RANGE_DEG
        yaw_step = self._yaw_search_step
        yaw_angles = np.arange(yaw_min, yaw_max + yaw_step / 2, yaw_step)

        best_score = -1.0
        best_result: Optional[DualCupResult] = None
        best_yaw = 0.0
        best_xy = (0.0, 0.0)

        h, w = frame.shape[:2]

        for yaw_deg in yaw_angles:
            rad = math.radians(yaw_deg)
            cos_y, sin_y = math.cos(rad), math.sin(rad)
            # Cup offset vectors in pixel space for this yaw
            da_x = int(round(-jaw_half_px * cos_y))
            da_y = int(round(-jaw_half_px * sin_y))
            db_x = int(round(jaw_half_px * cos_y))
            db_y = int(round(jaw_half_px * sin_y))

            for cx, cy in zip(cand_xs, cand_ys):
                ax, ay = cx + da_x, cy + da_y
                bx, by = cx + db_x, cy + db_y
                # Bounds check
                if not (cup_radius_px <= ax < w - cup_radius_px and
                        cup_radius_px <= ay < h - cup_radius_px and
                        cup_radius_px <= bx < w - cup_radius_px and
                        cup_radius_px <= by < h - cup_radius_px):
                    continue

                # Quick mask check: both cups must land on object
                if mask[ay, ax] == 0 or mask[by, bx] == 0:
                    continue

                # Seal scores
                seal_a = self._compute_seal_score(frame, mask, ax, ay, cup_radius_px, median_depth)
                seal_b = self._compute_seal_score(frame, mask, bx, by, cup_radius_px, median_depth)

                # Edge distances from distance transform
                edge_dist_a_px = float(dist_transform[ay, ax]) - cup_radius_px
                edge_dist_b_px = float(dist_transform[by, bx]) - cup_radius_px
                edge_a_mm = edge_dist_a_px * median_depth / ci.fx
                edge_b_mm = edge_dist_b_px * median_depth / ci.fx

                # Lever arm
                lever_a = math.hypot(ax - centroid_x, ay - centroid_y) * median_depth / ci.fx
                lever_b = math.hypot(bx - centroid_x, by - centroid_y) * median_depth / ci.fx

                # Activation mode
                activation = "BOTH"
                if seal_a < 0.5 and seal_b < 0.5:
                    activation = "NONE"
                elif seal_a < 0.5:
                    activation = "CUP_B_ONLY"
                elif seal_b < 0.5:
                    activation = "CUP_A_ONLY"

                if activation == "NONE":
                    continue

                combined_seal = (seal_a + seal_b) / 2.0
                # Wrench resistance: higher for normal-consistent, large-span grips near center
                wrench = combined_seal * max(0.0, 1.0 - (lever_a + lever_b) / (2.0 * 200.0))

                # Overall score
                score = (
                    0.50 * combined_seal
                    + 0.20 * wrench
                    + 0.15 * min(1.0, min(edge_a_mm, edge_b_mm) / 10.0)
                    + 0.15 * (1.0 if activation == "BOTH" else 0.5)
                )

                if score > best_score:
                    best_score = score
                    mid_x = (ax + bx) / 2.0
                    mid_y = (ay + by) / 2.0
                    # XY correction: how far from target to optimal placement
                    dx_px = mid_x - target.x
                    dy_px = mid_y - target.y
                    dx_mm = dx_px * median_depth / ci.fx
                    dy_mm = dy_px * median_depth / ci.fy

                    cup_a_pos = Point3D(
                        x=(ax - ci.cx) * median_depth / ci.fx,
                        y=(ay - ci.cy) * median_depth / ci.fy,
                        z=median_depth,
                    )
                    cup_b_pos = Point3D(
                        x=(bx - ci.cx) * median_depth / ci.fx,
                        y=(by - ci.cy) * median_depth / ci.fy,
                        z=median_depth,
                    )

                    best_result = DualCupResult(
                        cup_a=CupPlacement(
                            center_px=PixelCoord(ax, ay),
                            position_mm=cup_a_pos,
                            seal_score=seal_a,
                            flatness_residual=0.0,  # computed below for winner
                            edge_distance_mm=edge_a_mm,
                            lever_arm_mm=lever_a,
                        ),
                        cup_b=CupPlacement(
                            center_px=PixelCoord(bx, by),
                            position_mm=cup_b_pos,
                            seal_score=seal_b,
                            flatness_residual=0.0,
                            edge_distance_mm=edge_b_mm,
                            lever_arm_mm=lever_b,
                        ),
                        activation_mode=activation,
                        combined_seal=combined_seal,
                        wrench_resistance=wrench,
                        yaw_deg=float(yaw_deg),
                    )
                    best_yaw = float(yaw_deg)
                    best_xy = (dx_mm, dy_mm)

        # Compute local flatness residuals for winner
        if best_result is not None:
            _, res_a = self._estimate_local_normal(
                frame, mask, best_result.cup_a.center_px, cup_radius_px,
            )
            _, res_b = self._estimate_local_normal(
                frame, mask, best_result.cup_b.center_px, cup_radius_px,
            )
            best_result.cup_a.flatness_residual = res_a
            best_result.cup_b.flatness_residual = res_b

        # Clamp yaw
        best_yaw = max(-MAX_YAW_CORRECTION_DEG, min(MAX_YAW_CORRECTION_DEG, best_yaw))

        return best_result, best_yaw, best_xy

    def _compute_seal_score(
        self,
        frame: np.ndarray,
        mask: np.ndarray,
        cx: int, cy: int,
        radius_px: int,
        median_depth: float,
    ) -> float:
        """Compute perimeter seal score for a cup at (cx, cy)."""
        n_points = self._seal_perimeter_points
        h, w = frame.shape[:2]
        valid = 0

        for i in range(n_points):
            angle = 2.0 * math.pi * i / n_points
            px = int(round(cx + radius_px * math.cos(angle)))
            py = int(round(cy + radius_px * math.sin(angle)))
            if px < 0 or px >= w or py < 0 or py >= h:
                continue
            # Check: on mask AND depth is continuous
            if mask[py, px] == 0:
                continue
            d = float(frame[py, px]) * DEPTH_SCALE
            if d <= 0 or abs(d - median_depth) > SEAL_BREAK_DEPTH_JUMP_MM:
                continue
            valid += 1

        return valid / n_points if n_points > 0 else 0.0

    # ────────────────────────────────────────────────────────────────────────
    # 3d. Collision corridor scan
    # ────────────────────────────────────────────────────────────────────────

    def _scan_corridor(
        self,
        frame: np.ndarray,
        object_mask: np.ndarray,
        median_depth: float,
        target: PixelCoord,
    ) -> Tuple[List[CollisionZone], str]:
        """Scan approach corridor for obstacles (non-target depth points)."""
        ci = self._ds.intrinsics
        envelope_r = self._collision_envelope

        # Dilate the object mask to exclude boundary mixed pixels
        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MASK_DILATE_PX * 2 + 1, MASK_DILATE_PX * 2 + 1))
        excluded = cv2.dilate(object_mask, dilate_kernel, iterations=1)

        # Corridor ROI in pixels: constrained by envelope radius at object depth
        roi_half_px = max(1, int(round(envelope_r * ci.fx / median_depth)))
        h, w = frame.shape[:2]
        x0 = max(0, target.x - roi_half_px)
        x1 = min(w, target.x + roi_half_px)
        y0 = max(0, target.y - roi_half_px)
        y1 = min(h, target.y + roi_half_px)

        # Extract non-target pixels in corridor
        roi_frame = frame[y0:y1, x0:x1].astype(np.float32) * DEPTH_SCALE
        roi_excluded = excluded[y0:y1, x0:x1]

        # Non-target, valid-depth pixels
        corridor_mask = (roi_frame > DEPTH_MIN_RANGE_MM * DEPTH_SCALE) & (roi_excluded == 0)
        # Only pixels between tool and object
        corridor_mask &= (roi_frame < median_depth)

        ys_local, xs_local = np.where(corridor_mask)
        if len(xs_local) == 0:
            return [], "CLEAR"

        # Unproject to 3D
        xs_abs = xs_local + x0
        ys_abs = ys_local + y0
        depths = roi_frame[ys_local, xs_local]
        X = (xs_abs.astype(np.float32) - ci.cx) * depths / ci.fx
        Y = (ys_abs.astype(np.float32) - ci.cy) * depths / ci.fy
        Z = depths

        # Approach axis: from tool to target center (straight down in camera Z)
        target_X = (target.x - ci.cx) * median_depth / ci.fx
        target_Y = (target.y - ci.cy) * median_depth / ci.fy

        # Perpendicular distance to approach axis
        perp_dist = np.sqrt((X - target_X) ** 2 + (Y - target_Y) ** 2)

        in_cylinder = perp_dist < envelope_r
        if not np.any(in_cylinder):
            return [], "CLEAR"

        obstacle_count = int(np.count_nonzero(in_cylinder))
        obstacle_depths = depths[in_cylinder]
        obstacle_perp = perp_dist[in_cylinder]

        min_perp = float(np.min(obstacle_perp))
        zone = CollisionZone(
            bbox_3d=(
                Point3D(float(np.min(X[in_cylinder])), float(np.min(Y[in_cylinder])), float(np.min(Z[in_cylinder]))),
                Point3D(float(np.max(X[in_cylinder])), float(np.max(Y[in_cylinder])), float(np.max(Z[in_cylinder]))),
            ),
            depth_range_mm=(float(np.min(obstacle_depths)), float(np.max(obstacle_depths))),
            perpendicular_dist_mm=min_perp,
            risk_level="BLOCKED" if min_perp < envelope_r * 0.5 else "MARGINAL",
        )

        risk = zone.risk_level
        return [zone], risk

    # ────────────────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────────────────

    def _failed_result(
        self,
        target: PixelCoord,
        frame_context: FrameContext,
        reason_codes: List[str],
    ) -> GraspAnalysis:
        """Return a FAILED GraspAnalysis with zero corrections."""
        return GraspAnalysis(
            target=target,
            surface_normal=SurfaceNormal(0.0, 0.0, 1.0),
            roll_correction_deg=0.0,
            pitch_correction_deg=0.0,
            yaw_correction_deg=0.0,
            xy_correction_mm=(0.0, 0.0),
            dual_cup_result=None,
            collision_zones=[],
            collision_risk="CLEAR",
            approach_clear=True,
            confidence=0.0,
            confidence_breakdown=ConfidenceBreakdown(0.0, 0.0, 1.0, 1.0),
            frame_context=frame_context,
            transform_info=TransformInfo(),
            analysis_status=AnalysisStatus(status="FAILED", reason_codes=reason_codes),
        )
