"""
GraspPlanner — full‑auto pick pipeline orchestrator.

Coordinates DepthService, GripperController, RobotActor and GraspVerifier
to execute a complete detect → align → approach → grip → verify → lift cycle.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.config import (
    GRASP_MAX_RETRIES,
    GRASP_APPROACH_SPEED_PCT,
    GRASP_MOVE_SPEED_PCT,
    GRASP_LIFT_HEIGHT_MM,
    GRASP_RETRACT_MM,
    VACUUM_SETTLE_TIME_S,
    VACUUM_POST_LIFT_VERIFY_S,
    TORQUE_CONTACT_THRESHOLD,
)
from db.trajectory import get_trajectory
from drivers.xarm_driver.actor.commands import (
    Command,
    CommandType,
    ExecutionPolicy,
    ResultStatus,
)
from drivers.xarm_driver.usecases.grasp_verify import GraspVerifier
from models.grasp_types import (
    GraspOutcome,
    GraspPoint,
    GraspResult,
    ObjectDetection,
    PixelCoord,
    VerificationResult,
)
from services.depth_service import DepthService

logger = logging.getLogger(__name__)


class GraspPlanner:
    """
    Full‑auto pick orchestrator.

    Dependencies injected:
      • ``actor_enqueue`` — async callable that sends a ``Command`` to ``RobotActor``
      • ``depth_service``  — ``DepthService`` instance
      • ``gripper_status_fn`` — callable returning current vacuum SDK state (int)
      • ``torque_fn``        — callable returning joint torques list or None
      • ``get_joints_fn``   — callable returning current joints dict or None
    """

    def __init__(
        self,
        actor_enqueue,
        depth_service: DepthService,
        gripper_status_fn,
        torque_fn,
        get_joints_fn=None,
        max_retries: int = GRASP_MAX_RETRIES,
    ):
        self._enqueue = actor_enqueue
        self._depth = depth_service
        self._vacuum_state = gripper_status_fn
        self._torque = torque_fn
        self._get_joints = get_joints_fn
        self._max_retries = max_retries
        self._verifier = GraspVerifier()

    # ── Public entry point ─────────────────────────────────────────────────

    async def execute_grasp(
        self,
        target_px: Optional[PixelCoord] = None,
    ) -> GraspResult:
        """
        Run the full smart‑grasp pipeline.

        Parameters
        ----------
        target_px : optional
            If given, pick the object nearest this pixel.
            Otherwise pick the largest detected object.
        """
        t0 = time.monotonic()

        # ── 0. Load trajectory config & save return position ─────────────
        traj = get_trajectory() or {}
        prefix  = traj.get("prefix",   {"active": False})
        base_mv = traj.get("baseMove", {"active": True, "posX": 0, "posY": 0, "posZ": 0, "speed": GRASP_APPROACH_SPEED_PCT})
        postfix = traj.get("postfix",  {"active": False})
        do_return = traj.get("return",  {"active": False}).get("active", False)
        saved_joints = self._get_joints() if (do_return and self._get_joints) else None
        logger.info("Trajectory config: prefix=%s  baseMove=%s  postfix=%s  return=%s",
                    prefix.get('active'), base_mv.get('active'), postfix.get('active'), do_return)

        # ── 1. Scan ────────────────────────────────────────────────────────
        frame_before = await self._depth.get_depth_frame()
        if target_px is not None:
            det = self._depth.detect_at_point(frame_before, target_px.x, target_px.y)
            if det is None:
                # Fallback: try auto‑detect and pick closest to target
                detections = self._depth.detect_objects(frame_before)
                det = self._nearest(detections, target_px)
        else:
            detections = self._depth.detect_objects(frame_before)
            det = detections[0] if detections else None

        if det is None:
            return GraspResult(
                outcome=GraspOutcome.NO_OBJECT_DETECTED,
                elapsed_s=time.monotonic() - t0,
            )
        logger.info(
            "Object detected: center=%s depth=%.1fmm size=%.1f×%.1fmm area=%d",
            det.center_px, det.depth_mm, det.size_mm[0], det.size_mm[1],
            int(det.contour_area_px),
        )

        # ── 2. Align (centre camera over object) ──────────────────────────
        grasp_pt = self._depth.compute_grasp_point(det)
        tool_dx, tool_dy = self._depth.calculate_camera_shift(det.center_px, det.depth_mm)
        if abs(tool_dx) > 1.0 or abs(tool_dy) > 1.0:
            align_ok = await self._move_tool(tool_dx, tool_dy, 0.0, GRASP_MOVE_SPEED_PCT)
            if not align_ok:
                return GraspResult(
                    outcome=GraspOutcome.ALIGNMENT_FAILED,
                    detection=det,
                    elapsed_s=time.monotonic() - t0,
                )
            # Re‑scan after alignment
            await asyncio.sleep(0.3)
            frame_before = await self._depth.get_depth_frame()
            det2 = self._depth.detect_at_point(
                frame_before, self._depth._intrinsics.width // 2,
                self._depth._intrinsics.height // 2,
            )
            if det2 is not None:
                det = det2
                grasp_pt = self._depth.compute_grasp_point(det)

        # ── 3. Prefix — camera→gripper static offset ──────────────────
        if prefix.get("active"):
            pfx_x = prefix.get("posX", 0)
            pfx_y = prefix.get("posY", 0)
            pfx_z = prefix.get("posZ", 0)
            pfx_spd = prefix.get("speed", GRASP_MOVE_SPEED_PCT)
            if abs(pfx_x) > 0.1 or abs(pfx_y) > 0.1 or abs(pfx_z) > 0.1:
                logger.info("Prefix offset: dx=%.1f dy=%.1f dz=%.1f mm", pfx_x, pfx_y, pfx_z)
                pfx_ok = await self._move_tool(pfx_x, pfx_y, pfx_z, pfx_spd)
                if not pfx_ok:
                    return GraspResult(
                        outcome=GraspOutcome.ALIGNMENT_FAILED,
                        detection=det,
                        error_message="Prefix offset move failed",
                        elapsed_s=time.monotonic() - t0,
                    )

        # ── 4. Approach + Grip + Verify (with retries) ─────────────────────
        raw_depth_z = DepthService.calibrate_distance(det.depth_mm)
        base_z_offset = base_mv.get("posZ", 0) if base_mv.get("active") else 0
        approach_z = raw_depth_z + base_z_offset
        approach_speed = base_mv.get("speed", GRASP_APPROACH_SPEED_PCT) if base_mv.get("active") else GRASP_APPROACH_SPEED_PCT
        last_verification: Optional[VerificationResult] = None

        for attempt in range(1, self._max_retries + 1):
            logger.info("Grasp attempt %d/%d", attempt, self._max_retries)

            # Record torque baseline
            torque_baseline = self._torque()

            # Approach — descend in Z (tool frame)
            approach_ok = await self._move_tool(
                0.0, 0.0, approach_z, approach_speed,
            )
            if not approach_ok:
                return GraspResult(
                    outcome=GraspOutcome.APPROACH_FAILED,
                    attempts=attempt,
                    detection=det,
                    elapsed_s=time.monotonic() - t0,
                )

            # Grip
            grip_ok = await self._grip_close()
            if not grip_ok:
                await self._grip_open()
                await self._move_tool(0.0, 0.0, -GRASP_RETRACT_MM, GRASP_APPROACH_SPEED_PCT)
                last_verification = VerificationResult(
                    object_held=False, detail="grip_close command failed",
                )
                continue

            # Wait for vacuum to settle
            await asyncio.sleep(VACUUM_SETTLE_TIME_S)

            # Lift
            lift_ok = await self._move_tool(
                0.0, 0.0, -GRASP_LIFT_HEIGHT_MM, GRASP_APPROACH_SPEED_PCT,
            )
            if not lift_ok:
                await self._grip_open()
                return GraspResult(
                    outcome=GraspOutcome.APPROACH_FAILED,
                    attempts=attempt,
                    detection=det,
                    error_message="Lift failed",
                    elapsed_s=time.monotonic() - t0,
                )

            # Post‑lift settle
            await asyncio.sleep(VACUUM_POST_LIFT_VERIFY_S)

            # Read verification channels
            vacuum_state = self._vacuum_state()
            torque_current = self._torque()
            frame_after = await self._depth.get_depth_frame()

            last_verification = self._verifier.verify(
                vacuum_sdk_state=vacuum_state,
                torque_baseline=torque_baseline,
                torque_current=torque_current,
                depth_before=frame_before,
                depth_after=frame_after,
                detection=det,
            )
            logger.info(
                "Verification (attempt %d): held=%s  %s",
                attempt, last_verification.object_held, last_verification.detail,
            )

            if last_verification.object_held:
                # ── 5. Postfix move (optional) ────────────────────────────
                if postfix.get("active"):
                    pf_x = postfix.get("posX", 0)
                    pf_y = postfix.get("posY", 0)
                    pf_z = postfix.get("posZ", 0)
                    pf_spd = postfix.get("speed", GRASP_MOVE_SPEED_PCT)
                    if abs(pf_x) > 0.1 or abs(pf_y) > 0.1 or abs(pf_z) > 0.1:
                        logger.info("Postfix move: dx=%.1f dy=%.1f dz=%.1f mm", pf_x, pf_y, pf_z)
                        await self._move_tool(pf_x, pf_y, pf_z, pf_spd)

                # ── 6. Return to saved joint position (optional) ───────────
                if do_return and saved_joints:
                    logger.info("Returning to saved joint position")
                    await self._move_joints(saved_joints, approach_speed)

                return GraspResult(
                    outcome=GraspOutcome.SUCCESS,
                    attempts=attempt,
                    detection=det,
                    verification=last_verification,
                    elapsed_s=time.monotonic() - t0,
                )

            # Verify failed → release, retract, retry
            await self._grip_open()
            await self._move_tool(0.0, 0.0, -GRASP_RETRACT_MM, GRASP_APPROACH_SPEED_PCT)
            # Re‑approach on next iteration uses same approach_z
            await asyncio.sleep(0.2)

        # All retries exhausted
        await self._grip_open()
        return GraspResult(
            outcome=GraspOutcome.VERIFY_FAILED,
            attempts=self._max_retries,
            detection=det,
            verification=last_verification,
            error_message=f"All {self._max_retries} attempts failed verification",
            elapsed_s=time.monotonic() - t0,
        )

    # ── Private helpers ────────────────────────────────────────────────────

    async def _move_tool(
        self, x: float, y: float, z: float, speed_pct: float,
    ) -> bool:
        cmd = Command(
            command_id=f"grasp-move-{time.monotonic_ns()}",
            type=CommandType.MOVE_TOOL_POSITION,
            params={
                "x_offset_mm": x,
                "y_offset_mm": y,
                "z_offset_mm": z,
                "velocity_percent": speed_pct,
            },
            policy=ExecutionPolicy.QUEUE,
        )
        result = await self._enqueue(cmd)
        if result.status != ResultStatus.SUCCEEDED:
            logger.warning("_move_tool(%.1f,%.1f,%.1f) FAILED: %s %s",
                           x, y, z, result.status, result.error_message or '')
        return result.status == ResultStatus.SUCCEEDED

    async def _grip_close(self) -> bool:
        cmd = Command(
            command_id=f"grasp-grip-{time.monotonic_ns()}",
            type=CommandType.GRIP_CLOSE,
            params={},
            policy=ExecutionPolicy.QUEUE,
        )
        result = await self._enqueue(cmd)
        return result.status == ResultStatus.SUCCEEDED

    async def _grip_open(self) -> bool:
        cmd = Command(
            command_id=f"grasp-release-{time.monotonic_ns()}",
            type=CommandType.GRIP_OPEN,
            params={},
            policy=ExecutionPolicy.QUEUE,
        )
        result = await self._enqueue(cmd)
        return result.status == ResultStatus.SUCCEEDED

    async def _move_joints(
        self, joints: Dict[str, Any], speed_pct: float,
    ) -> bool:
        cmd = Command(
            command_id=f"grasp-return-{time.monotonic_ns()}",
            type=CommandType.MOVE_JOINTS,
            params={
                "j1": joints.get("j1", 0),
                "j2": joints.get("j2", 0),
                "j3": joints.get("j3", 0),
                "j4": joints.get("j4", 0),
                "j5": joints.get("j5", 0),
                "j6": joints.get("j6", 0),
                "velocity_percent": speed_pct,
            },
            policy=ExecutionPolicy.QUEUE,
        )
        result = await self._enqueue(cmd)
        return result.status == ResultStatus.SUCCEEDED

    @staticmethod
    def _nearest(
        detections: List[ObjectDetection], target: PixelCoord,
    ) -> Optional[ObjectDetection]:
        if not detections:
            return None
        return min(
            detections,
            key=lambda d: (d.center_px.x - target.x) ** 2
            + (d.center_px.y - target.y) ** 2,
        )
