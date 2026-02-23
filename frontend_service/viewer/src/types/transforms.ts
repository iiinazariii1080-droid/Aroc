/**
 * transforms.ts — Types for the full kinematic transform chain.
 */

import type { RosPose, StaticTransform, Mat4 } from './coordinates';

/**
 * Complete transform chain state.
 *
 * Chain order:
 *   T_world → T_agv → T_arm_base → T_lift → FK(J1..Jn) → T_flange → T_tool | T_camera
 *
 * All transforms stored as 4×4 column-major matrices in ROS convention (Z-up, meters).
 */
export interface TransformChainState {
  /** AGV pose in world (map) frame. Dynamic — from navigation. */
  readonly worldToAgv: RosPose;
  /** Arm base offset on AGV chassis. Static — from mounting. */
  readonly agvToArmBase: StaticTransform;
  /** Lift displacement from arm base. Dynamic — from motor units. */
  readonly liftDisplacement: RosPose;
  /** FK result: flange pose relative to arm base + lift. Dynamic — from joints. */
  readonly armBaseToFlange: Mat4;
  /** Camera offset from flange. Static — from calibration. */
  readonly flangeToCamera: StaticTransform;
  /** Gripper offset from flange. Static — from spec. */
  readonly flangeToGripper: StaticTransform;
}

/** Resolved world-frame poses for key points. */
export interface ResolvedPoses {
  /** Arm base in world frame (meters). */
  readonly armBase: RosPose;
  /** Tool (flange) in world frame (meters). */
  readonly tcp: RosPose;
  /** Camera in world frame (meters). */
  readonly camera: RosPose;
  /** Full transform: world → camera as 4×4 matrix. */
  readonly worldToCamera: Mat4;
}

/** AGV pose received from Symovo API. */
export interface AgvPose {
  readonly x_m: number;
  readonly y_m: number;
  readonly theta_deg: number;
  readonly map_id: number;
}

/** AGV map metadata. */
export interface AgvMapMeta {
  readonly id: number;
  readonly widthPx: number;
  readonly heightPx: number;
  readonly resolution: number;   // meters per pixel
  readonly offsetX: number;      // meters
  readonly offsetY: number;      // meters
  readonly imageUrl: string;
}
