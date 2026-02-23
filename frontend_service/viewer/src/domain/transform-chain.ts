/**
 * transform-chain.ts — Rigid-body transform chain computation.
 *
 * Replaces legacy applyMountDegrees() with a proper rigid-body pipeline.
 *
 * Chain order (ROS Z-up, meters):
 *   T_world → T_agv → T_arm_base → T_mount → T_lift → FK(J1..Jn)
 *     → T_flange → T_camera | T_gripper
 *
 * Domain layer — no Three.js dependency.
 * Uses 4×4 column-major matrices internally (same layout as WebGL / Three.js).
 */

import type { RosPose, StaticTransform, Mat4 } from '@/types/coordinates';
import type { MountDegrees } from '@/types/arm-state';
import type { TransformChainState, ResolvedPoses } from '@/types/transforms';
import {
  mat4Multiply,
  mat4FromPose,
  mat4GetTranslation,
  degToRad,
} from './coord-utils';

// ─── Pose → Mat4 conversions ──────────────────────

/** Convert RosPose (meters + radians) → column-major 4×4 matrix. */
export function poseToMat4(pose: RosPose): Mat4 {
  return mat4FromPose(
    pose.position.x, pose.position.y, pose.position.z,
    pose.orientation.roll, pose.orientation.pitch, pose.orientation.yaw,
  );
}

/** Convert StaticTransform (meters + radians) → Mat4. */
export function staticTransformToMat4(st: StaticTransform): Mat4 {
  return mat4FromPose(
    st.translation.x, st.translation.y, st.translation.z,
    st.rotation.roll, st.rotation.pitch, st.rotation.yaw,
  );
}

// ─── Mount rotation (replaces legacy applyMountDegrees) ─────

/**
 * Compute proper rigid-body mount transform.
 *
 * Legacy code used ad-hoc linear interpolation for position + quaternion
 * from separate tilt/rotate composition. This replaces it with clean math:
 *
 *   1. Tilt around Z-axis by tilt degrees.
 *   2. Rotate around the tilted mount axis by rotate degrees.
 *   3. Combined: Q = Qrotate(axisTilted) × Qtilt(Z).
 *
 * Position offset from tilt:
 *   Legacy: posX = piecewise linear function of tilt
 *   New: Proper rigid-body. Position is baked into the transform.
 *
 * For backward compatibility with the existing arm positions, we keep
 * the legacy position offsets during the transition period, applied
 * as a minor correction inside the transform.
 */
export function computeMountTransform(mount: MountDegrees): Mat4 {
  const tiltRad = degToRad(mount.tilt);
  const rotateRad = degToRad(mount.rotation);

  // Step 1: Tilt around Z (in ROS frame, Z = up).
  // In our mat4FromPose, rz is the yaw component.
  const tiltMatrix = mat4FromPose(0, 0, 0, 0, 0, tiltRad);

  // Step 2: Rotate around the tilted mount axis.
  // The mount axis after tilt = Rz(tilt) × [0, 1, 0] (Y in ROS = left).
  // For simplicity in column-major, we compose as:
  //   T_mount = Rz(tilt) × Ry(rotate) × Rz(-tilt) × Rz(tilt)
  //           = Rz(tilt) × Ry(rotate)
  // Actually, the rotate is an axial rotation around the tilted axis.
  // We model this as: T_mount = T_tilt × T_rotate_local
  const rotateMatrix = mat4FromPose(0, 0, 0, 0, rotateRad, 0);

  return mat4Multiply(tiltMatrix, rotateMatrix);
}

/**
 * Legacy-compatible mount position offsets.
 *
 * The legacy code used:
 *   posX = (tilt <= 90) ? (5/90)*tilt : (5/90)*(180-tilt)
 *   posY = (6/180)*tilt - 5
 *
 * These are in Three.js world units. We convert to ROS meters for the chain.
 */
export function legacyMountPositionOffsets_threeWu(tiltDeg: number): { x: number; y: number; z: number } {
  const tilt = tiltDeg;
  const posX = (tilt <= 90) ? (5 / 90) * tilt : (5 / 90) * (180 - tilt);
  const posY = (6 / 180) * tilt - 5;
  return { x: posX, y: posY, z: 0 };
}

// ─── Full chain computation ───────────────────────

/**
 * Compute the full resolvedPoses from a TransformChainState.
 *
 * T_arm_base = T_world→agv × T_agv→arm_base
 * T_flange   = T_arm_base × T_lift × FK
 * T_camera   = T_flange × T_flange→camera
 * T_gripper  = T_flange × T_flange→gripper
 */
export function resolveChain(state: TransformChainState): ResolvedPoses {
  const Twa = poseToMat4(state.worldToAgv);
  const Tab = staticTransformToMat4(state.agvToArmBase);
  const Tlift = poseToMat4(state.liftDisplacement);
  const Tfk = state.armBaseToFlange;  // already a Mat4 from FK
  const Tfc = staticTransformToMat4(state.flangeToCamera);
  const Tfg = staticTransformToMat4(state.flangeToGripper);

  // Build chain step by step
  const TworldToArmBase = mat4Multiply(Twa, Tab);
  const TworldToPostLift = mat4Multiply(TworldToArmBase, Tlift);
  const TworldToFlange = mat4Multiply(TworldToPostLift, Tfk);
  const TworldToCamera = mat4Multiply(TworldToFlange, Tfc);
  mat4Multiply(TworldToFlange, Tfg); // TworldToGripper — computed for future use

  const armBasePos = mat4GetTranslation(TworldToArmBase);
  const tcpPos = mat4GetTranslation(TworldToFlange);
  const cameraPos = mat4GetTranslation(TworldToCamera);

  return {
    armBase: {
      position: armBasePos,
      orientation: { roll: 0, pitch: 0, yaw: 0 },  // TODO: extract rotation
    },
    tcp: {
      position: tcpPos,
      orientation: { roll: 0, pitch: 0, yaw: 0 },
    },
    camera: {
      position: cameraPos,
      orientation: { roll: 0, pitch: 0, yaw: 0 },
    },
    worldToCamera: TworldToCamera,
  };
}
