"""
Grasp verification — multi‑channel post‑grip check.

Uses three independent signals (no F/T sensor required):
  1. xArm SDK ``get_vacuum_gripper()`` → −1/0/1
  2. Joint torque delta before/after lift → object mass > 0
  3. Depth re‑scan → object disappeared from ROI
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

from app.config import TORQUE_GRIP_THRESHOLD
from models.grasp_types import ObjectDetection, VerificationResult

logger = logging.getLogger(__name__)


class GraspVerifier:
    """Stateless helper — instantiate per‑grasp attempt."""

    def __init__(
        self,
        torque_threshold: float = TORQUE_GRIP_THRESHOLD,
    ):
        self._torque_threshold = torque_threshold

    # ── Vacuum check ───────────────────────────────────────────────────────

    @staticmethod
    def check_vacuum(sdk_state: int) -> bool:
        """``get_vacuum_gripper()`` returns 1 when object is picked."""
        return sdk_state == 1

    # ── Torque‑delta check ─────────────────────────────────────────────────

    def check_torque_delta(
        self,
        baseline: List[float],
        current: List[float],
    ) -> Tuple[bool, List[float]]:
        """
        Compare joint torques before grip and after lift.

        A positive delta on the last 2–3 joints (wrist) indicates
        the arm is supporting additional weight.
        """
        if len(baseline) < 6 or len(current) < 6:
            return False, []
        deltas = [abs(c - b) for b, c in zip(baseline, current)]
        # Check wrist joints (J4, J5, J6 — indices 3‑5)
        wrist_delta = max(deltas[3:6])
        ok = wrist_delta > self._torque_threshold
        logger.debug(
            "Torque deltas: %s  wrist_max=%.3f  threshold=%.3f  ok=%s",
            [f"{d:.3f}" for d in deltas], wrist_delta, self._torque_threshold, ok,
        )
        return ok, deltas

    # ── Depth disappearance check ──────────────────────────────────────────

    @staticmethod
    def check_depth_vanished(
        before: np.ndarray,
        after: np.ndarray,
        detection: ObjectDetection,
        vanish_ratio: float = 0.5,
    ) -> bool:
        """
        After lifting, the object should no longer be visible in the ROI.

        If ≥ ``vanish_ratio`` of the ROI pixels changed significantly
        (became deeper or zero), we consider the object gone.
        """
        bx, by, bw, bh = detection.bbox
        roi_before = before[by: by + bh, bx: bx + bw].astype(np.float32)
        roi_after = after[by: by + bh, bx: bx + bw].astype(np.float32)

        if roi_before.size == 0:
            return False

        # Pixels where depth increased by > 15 mm or went to 0
        diff = roi_after - roi_before
        changed = (diff > 15) | (roi_after == 0)
        ratio = float(np.count_nonzero(changed)) / roi_before.size
        ok = ratio >= vanish_ratio
        logger.debug("Depth vanish ratio=%.2f  threshold=%.2f  ok=%s", ratio, vanish_ratio, ok)
        return ok

    # ── Combined verification ──────────────────────────────────────────────

    def verify(
        self,
        vacuum_sdk_state: int,
        torque_baseline: Optional[List[float]],
        torque_current: Optional[List[float]],
        depth_before: Optional[np.ndarray] = None,
        depth_after: Optional[np.ndarray] = None,
        detection: Optional[ObjectDetection] = None,
    ) -> VerificationResult:
        """
        Run all available checks and return an aggregated result.

        ``object_held`` is True when **at least 2 of 3** channels confirm.
        """
        vacuum_ok = self.check_vacuum(vacuum_sdk_state)

        torque_ok = False
        deltas: List[float] = []
        if torque_baseline and torque_current:
            torque_ok, deltas = self.check_torque_delta(torque_baseline, torque_current)

        depth_ok = False
        if depth_before is not None and depth_after is not None and detection is not None:
            depth_ok = self.check_depth_vanished(depth_before, depth_after, detection)

        votes = sum([vacuum_ok, torque_ok, depth_ok])
        held = votes >= 2  # majority wins

        detail_parts = []
        if vacuum_ok:
            detail_parts.append("vacuum=OK")
        else:
            detail_parts.append("vacuum=FAIL")
        if torque_ok:
            detail_parts.append("torque=OK")
        else:
            detail_parts.append("torque=FAIL")
        if depth_ok:
            detail_parts.append("depth=OK")
        else:
            detail_parts.append("depth=FAIL")
        detail_parts.append(f"votes={votes}/3")

        return VerificationResult(
            object_held=held,
            vacuum_ok=vacuum_ok,
            torque_delta_ok=torque_ok,
            depth_vanished=depth_ok,
            torque_deltas=deltas or None,
            detail=", ".join(detail_parts),
        )
