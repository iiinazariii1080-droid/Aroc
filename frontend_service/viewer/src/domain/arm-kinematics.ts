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
import type { Mat4 } from '@/types/coordinates';
import {
  degToRad,
  mat4Identity,
  mat4Multiply,
  mat4FromPose,
  mat4ThreeToRos,
} from './coord-utils';

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

// ─── Cumulative FK matrix ─────────────────────────

/**
 * Compute the cumulative FK matrix: armBase → flange, in ROS convention.
 *
 * Chains all group translations and joint rotations in Three.js space
 * (matching the scene-graph structure used by arm-visual.ts), then converts
 * to ROS convention via COB⁻¹ × M_three × COB.
 *
 * Layout (for 6-axis):
 *   groups[0] = base carrier (no joint rotation)
 *   groups[1..6] = joints 0..5 (each has a rotation)
 *
 * M_three = T(gp[0]) × [T(gp[1])×R(j0)] × [T(gp[2])×R(j1)] × ... × [T(gp[N])×R(j[N-1])]
 *
 * @param jointsDeg - Joint angles in degrees (length = axisCount).
 * @param spec - ArmSpec for the variant.
 * @returns 4×4 column-major Mat4 in ROS frame (meters).
 */
export function computeFKMatrix(jointsDeg: readonly number[], spec: ArmSpec): Mat4 {
  const nGroups = spec.groupsPosition.length;
  const nJoints = spec.jointAxes.length;

  let M = mat4Identity();

  for (let i = 0; i < nGroups; i++) {
    // Translation from groupsPosition (Three.js meters, pre-scale)
    const [px, py, pz] = spec.groupsPosition[i];
    const T = mat4FromPose(px, py, pz, 0, 0, 0);
    M = mat4Multiply(M, T);

    // Joint rotation: groups[1..nJoints] → joints[0..nJoints-1]
    const jIdx = i - 1;
    if (jIdx >= 0 && jIdx < nJoints) {
      const rawDeg = (jIdx < jointsDeg.length ? jointsDeg[jIdx] : 0);
      const offsetDeg = spec.jointOffsetsDeg[jIdx] ?? 0;
      const sign = spec.jointSigns[jIdx] ?? 1;
      const angle = degToRad((rawDeg + offsetDeg) * sign);
      const axis = spec.jointAxes[jIdx].toLowerCase();

      // Single-axis rotation: mat4FromPose with only one Euler angle
      const R = axis === 'x'
        ? mat4FromPose(0, 0, 0, angle, 0, 0)
        : mat4FromPose(0, 0, 0, 0, angle, 0);
      M = mat4Multiply(M, R);
    }
  }

  // Convert Three.js → ROS: M_ros = COB⁻¹ × M_three × COB
  return mat4ThreeToRos(M);
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
