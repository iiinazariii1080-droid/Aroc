/**
 * robot.ts — Domain model: single source of truth for robot state.
 *
 * Hierarchy (strict parent → child):
 *   Robot
 *     ├─ identity  (axis, deviceType, endEffector)
 *     ├─ mount     (tilt, rotation)
 *     ├─ joints    (angles[])
 *     ├─ lift      (motorUnits)
 *     └─ agvPose   (x, y, θ)
 *
 * One entry point: `updateStatus(status)`.
 * After updating internal state, emits typed change callbacks so that
 * the orchestrator (or any other consumer) can react without polling.
 *
 * Domain layer — no Three.js, no EventBus dependency.
 */

import type { ArmIdentity, ArmSpec, ArmVariant, MountDegrees } from '@/types/arm-state';
import type { AgvPose } from '@/types/transforms';
import type { RobotStatusUpdate } from '@/types/robot-status';
import { ARM_SPECS, resolveVariant } from '@/config/arm-specs';
import { normalizeJoints } from '@/domain/validators';

// ─── Change descriptor ─────────────────────────────

/** Flags indicating which parts of the state changed in one update cycle. */
export interface RobotStateChange {
  readonly identity: boolean;
  readonly mount: boolean;
  readonly joints: boolean;
  readonly lift: boolean;
  readonly agvPose: boolean;
}

/** Processed output for a joints update (normalized + ready for FK). */
export interface ProcessedJoints {
  readonly angles: readonly number[];
  readonly timestamp: number;
}

/** Processed output for a lift update. */
export interface ProcessedLift {
  readonly motorUnits: number;
  readonly timestamp: number;
}

// ─── Robot model ────────────────────────────────────

export class Robot {
  // ─── Identity ─────────────────
  private _variant: ArmVariant = '6-6';
  private _spec: ArmSpec = ARM_SPECS['6-6'];
  private _axisCount: 5 | 6 | 7 = 6;
  private _identity: ArmIdentity = { axis: 6, deviceType: 6, endEffector: '' };

  // ─── Mount ────────────────────
  private _mount: MountDegrees = { tilt: 0, rotation: 0 };

  // ─── Joints ───────────────────
  private _jointsDeg: number[] = [];
  private _jointsTimestamp = 0;

  // ─── Lift ─────────────────────
  private _liftMotorUnits = 0;
  private _liftTimestamp = 0;

  // ─── AGV ──────────────────────
  private _agvPose: AgvPose = { x_m: 0, y_m: 0, theta_deg: 0, map_id: 0 };

  // ─── Public read-only accessors ───────────────────

  get variant(): ArmVariant { return this._variant; }
  get spec(): ArmSpec { return this._spec; }
  get axisCount(): number { return this._axisCount; }
  get identity(): ArmIdentity { return this._identity; }
  get mount(): MountDegrees { return this._mount; }
  get jointsDeg(): readonly number[] { return this._jointsDeg; }
  get liftMotorUnits(): number { return this._liftMotorUnits; }
  get agvPose(): AgvPose { return this._agvPose; }

  // ─── Single entry point ───────────────────────────

  /**
   * Apply a partial status update. Returns a change descriptor indicating
   * which parts of the state actually changed, plus processed outputs
   * ready for downstream consumers.
   */
  updateStatus(status: RobotStatusUpdate): {
    change: RobotStateChange;
    processedJoints: ProcessedJoints | null;
    processedLift: ProcessedLift | null;
  } {
    const change: RobotStateChange = {
      identity: !!status.identity,
      mount: !!status.mount,
      joints: !!(status.rawJoints || status.joints),
      lift: !!(status.rawLift || status.lift),
      agvPose: !!status.agvPose,
    };

    let processedJoints: ProcessedJoints | null = null;
    let processedLift: ProcessedLift | null = null;

    // 1. Identity (must come first — affects joint normalization)
    if (status.identity) {
      this._identity = status.identity;
      this._variant = resolveVariant(status.identity.axis, status.identity.deviceType);
      this._spec = ARM_SPECS[this._variant];
      this._axisCount = status.identity.axis;
    }

    // 2. Mount
    if (status.mount) {
      this._mount = status.mount;
    }

    // 3. Joints (raw → normalize, or pre-normalized)
    if (status.rawJoints) {
      const normalized = normalizeJoints([...status.rawJoints.angles], this._axisCount);
      this._jointsDeg = normalized;
      this._jointsTimestamp = status.rawJoints.timestamp;
      processedJoints = { angles: normalized, timestamp: this._jointsTimestamp };
    } else if (status.joints) {
      this._jointsDeg = [...status.joints.angles];
      this._jointsTimestamp = status.joints.timestamp;
      processedJoints = { angles: this._jointsDeg, timestamp: this._jointsTimestamp };
    }

    // 4. Lift
    if (status.rawLift) {
      this._liftMotorUnits = status.rawLift.motorUnits;
      this._liftTimestamp = status.rawLift.timestamp;
      processedLift = { motorUnits: this._liftMotorUnits, timestamp: this._liftTimestamp };
    } else if (status.lift) {
      this._liftMotorUnits = status.lift.motorUnits;
      this._liftTimestamp = status.lift.timestamp;
      processedLift = { motorUnits: this._liftMotorUnits, timestamp: this._liftTimestamp };
    }

    // 5. AGV
    if (status.agvPose) {
      this._agvPose = status.agvPose;
    }

    return { change, processedJoints, processedLift };
  }
}
