/**
 * coordinates.ts — Canonical coordinate types for the entire system.
 *
 * ALL spatial data in the domain layer uses ROS convention (Z-up, meters).
 * Three.js Y-up world-unit conversion happens ONLY in the rendering layer.
 *
 * Naming convention:
 *   - Ros*    → Z-up, meters, radians
 *   - Three*  → Y-up, world units (1 wu = 50 mm)
 *   - Camera* → Camera-local frame (X-right, Y-down, Z-depth), meters
 */

/** 3D point in ROS frame: X-forward, Y-left, Z-up. Meters. */
export interface RosPoint3 {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

/** Roll-pitch-yaw in ROS convention. Radians. */
export interface RosRPY {
  readonly roll: number;   // rotation around X (forward)
  readonly pitch: number;  // rotation around Y (left)
  readonly yaw: number;    // rotation around Z (up)
}

/** Full 6-DOF pose in ROS frame. Meters + radians. */
export interface RosPose {
  readonly position: RosPoint3;
  readonly orientation: RosRPY;
}

/** 3D point in Three.js frame: X-right, Y-up, Z-toward-camera. World units. */
export interface ThreePoint3 {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

/** 3D point in camera-local frame: X-right, Y-down, Z-depth. Meters. */
export interface CameraPoint3 {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

/** Homogeneous 4×4 matrix stored as column-major Float64Array (length 16). */
export type Mat4 = Float64Array;

/** Quaternion [x, y, z, w]. */
export type Quaternion = readonly [number, number, number, number];

/**
 * Static (known or placeholder) rigid-body transform.
 * Translation in meters, rotation in radians.
 */
export interface StaticTransform {
  readonly translation: RosPoint3;
  readonly rotation: RosRPY;
  readonly status: 'KNOWN' | 'PLACEHOLDER' | 'CALIBRATED';
  readonly note?: string;
}

/** Coordinate system identifier for type-safe conversions. */
export type CoordinateFrame =
  | 'ros_world'
  | 'ros_agv'
  | 'ros_arm_base'
  | 'ros_flange'
  | 'ros_camera'
  | 'three_scene';
