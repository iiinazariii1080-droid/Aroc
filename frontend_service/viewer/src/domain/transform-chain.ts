/**
 * transform-chain.ts — Rigid-body transform chain computation.
 *
 * Utility rigid-body chain helpers for domain tests and matrix composition.
 *
 * Chain order (ROS Z-up, meters):
 *   T_world → T_agv → T_arm_base → T_lift → FK(J1..Jn)
 *     → T_flange → T_camera | T_gripper
 *
 * Domain layer — no Three.js dependency.
 * Uses 4×4 column-major matrices internally (same layout as WebGL / Three.js).
 */

import type { RosPose, StaticTransform, Mat4 } from '@/types/coordinates';
import type { TransformChainState, ResolvedPoses } from '@/types/transforms';
import {
  mat4Multiply,
  mat4FromPose,
  mat4GetTranslation,
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
  const Tmount = mat4FromPose(
    0, 0, 0,
    state.mountOrientation.rotation * Math.PI / 180,  // rx (roll)
    state.mountOrientation.tilt * Math.PI / 180,       // ry (pitch)
    0,                                                  // rz
  );
  const Tab = staticTransformToMat4(state.agvToArmBase);
  const Tlift = poseToMat4(state.liftDisplacement);
  const Tfk = state.armBaseToFlange;  // already a Mat4 from FK
  const Tfc = staticTransformToMat4(state.flangeToCamera);
  const Tfg = staticTransformToMat4(state.flangeToGripper);

  // Build chain step by step
  const TworldToMount = mat4Multiply(Twa, Tmount);
  const TworldToArmBase = mat4Multiply(TworldToMount, Tab);
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
      orientation: { roll: 0, pitch: 0, yaw: 0 },  // Position-only contract for now
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
