/**
 * arm-kinematics.ts — Forward kinematics for xArm robot.
 *
 * Domain layer — NO Three.js dependency.
 *
 * This module computes per-group rotation updates that the rendering layer
 * applies to the Three.js scene graph. The data flow:
 *
 *   joints (degrees) + ArmSpec → FKResult (per-group axis + angle)
 *
 * The rendering layer then iterates FKResult, setting:
 *   group[i].rotation[axis] = angle
 *
 * This separates the pure math (domain) from the scene graph mutation (rendering).
 */

import type { ArmSpec, ArmVariant } from '@/types/arm-state';
import { ARM_SPECS, resolveVariant } from '@/config/arm-specs';
import { degToRad } from './coord-utils';

// ─── FK result ────────────────────────────────────

export interface JointRotation {
  /** Group index in the scene graph (1-based: group[1] is the first joint). */
  readonly groupIndex: number;
  /** Three.js axis to rotate around: 'x' or 'y'. */
  readonly axis: 'x' | 'y';
  /** Angle in radians. */
  readonly angle: number;
}

export interface FKResult {
  /** Per-joint rotation commands. */
  readonly rotations: readonly JointRotation[];
}

// ─── FK computation ───────────────────────────────

/**
 * Compute forward kinematics rotation commands from joint angles.
 *
 * @param jointsDeg - Joint angles in degrees (length = axisCount).
 * @param spec - ArmSpec from arm-specs.ts.
 * @returns FKResult with per-group rotation assignments.
 *
 * How it works:
 *   For each joint i (0-based):
 *     angle = (jointsDeg[i] + jointOffsetsDeg[i]) * jointSigns[i]
 *     axis  = jointAxes[i].toLowerCase()
 *     group = i + 1  (group[0] is the base/lift carrier, joints start at group[1])
 *
 * The result contains the final angle in radians for each group.
 */
export function computeFK(jointsDeg: readonly number[], spec: ArmSpec): FKResult {
  const n = spec.jointAxes.length;
  const rotations: JointRotation[] = [];

  for (let i = 0; i < n; i++) {
    const rawDeg = (i < jointsDeg.length ? jointsDeg[i] : 0);
    const offsetDeg = spec.jointOffsetsDeg[i] ?? 0;
    const sign = spec.jointSigns[i] ?? 1;
    const angleDeg = (rawDeg + offsetDeg) * sign;
    const axis = spec.jointAxes[i].toLowerCase() as 'x' | 'y';

    rotations.push({
      groupIndex: i + 1,
      axis,
      angle: degToRad(angleDeg),
    });
  }

  return { rotations };
}

/**
 * Convenience: compute FK from variant key + joint degrees.
 */
export function computeFKForVariant(
  axis: number,
  deviceType: number,
  jointsDeg: readonly number[],
): FKResult {
  const variant = resolveVariant(axis, deviceType);
  const spec = ARM_SPECS[variant];
  return computeFK(jointsDeg, spec);
}

// ─── Group setup data ─────────────────────────────

export interface GroupSetupData {
  /** Group index (0 = base, 1..n = joints, n+1 = tool). */
  readonly index: number;
  /** Position in world units. */
  readonly position: readonly [number, number, number];
  /** Mesh rotation in degrees (meshsRotation[i] + meshRotationBaseDeg). */
  readonly meshRotationDeg: readonly [number, number, number];
}

/**
 * Compute initial group setup data (positions + mesh rotations) for building the arm.
 *
 * @param spec - ArmSpec for the variant.
 * @param meshRotationBaseDeg - Base mesh rotation offset (typically [-90, 0, -90]).
 * @param scaleFactor - World units per meter (typically 20).
 */
export function computeGroupSetup(
  spec: ArmSpec,
  meshRotationBaseDeg: readonly [number, number, number] = [-90, 0, -90],
  scaleFactor: number = 20,
): GroupSetupData[] {
  const result: GroupSetupData[] = [];

  for (let i = 0; i < spec.groupsPosition.length; i++) {
    const [gx, gy, gz] = spec.groupsPosition[i];
    const [mx, my, mz] = spec.meshsRotation[i];

    result.push({
      index: i,
      position: [gx * scaleFactor, gy * scaleFactor, gz * scaleFactor],
      meshRotationDeg: [
        mx + meshRotationBaseDeg[0],
        my + meshRotationBaseDeg[1],
        mz + meshRotationBaseDeg[2],
      ],
    });
  }

  return result;
}

/**
 * Get which axis count (5, 6, 7) a variant belongs to.
 */
export function getAxisCount(variant: ArmVariant): number {
  return parseInt(variant.split('-')[0], 10);
}
