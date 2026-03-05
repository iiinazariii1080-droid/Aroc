/**
 * coord-utils.ts — Pure coordinate conversion functions.
 *
 * Domain-layer module. ZERO Three.js dependency.
 *
 * Conventions:
 *   ROS:   X-forward, Y-left, Z-up.   Meters. Radians.
 *   Three: X-right,   Y-up,  Z-toward-camera.  World units (1 wu = 50 mm).
 *
 * Axis mapping (ROS → Three.js):
 *   ROS X (forward)  → Three -Z
 *   ROS Y (left)     → Three -X
 *   ROS Z (up)       → Three +Y
 *
 * Scale: 1 meter = SCALE_FACTOR world units  (default 20).
 */

import type { RosPoint3, RosRPY, ThreePoint3 } from '@/types/coordinates';

/** Default scale factor: 1 meter = 20 world units. */
export const DEFAULT_SCALE_FACTOR = 20;
/** Default mm per world unit: 1 wu = 50 mm. */
export const DEFAULT_MM_PER_WU = 50;

// ─── Position conversions ─────────────────────────

/** ROS meters → Three.js world units. Pure axis swap + scale. */
export function rosToThree(p: RosPoint3, scale: number = DEFAULT_SCALE_FACTOR): ThreePoint3 {
  return {
    x: -p.y * scale,
    y:  p.z * scale,
    z: -p.x * scale,
  };
}

/** Three.js world units → ROS meters. Inverse of rosToThree. */
export function threeToRos(p: ThreePoint3, scale: number = DEFAULT_SCALE_FACTOR): RosPoint3 {
  const inv = 1 / scale;
  return {
    x: -p.z * inv,
    y: -p.x * inv,
    z:  p.y * inv,
  };
}

// ─── Scalar unit conversions ──────────────────────

/** Meters → world units. */
export function metersToWorld(m: number, scale: number = DEFAULT_SCALE_FACTOR): number {
  return m * scale;
}

/** World units → meters. */
export function worldToMeters(wu: number, scale: number = DEFAULT_SCALE_FACTOR): number {
  return wu / scale;
}

/** Millimeters → world units. */
export function mmToWorld(mm: number, mmPerWu: number = DEFAULT_MM_PER_WU): number {
  return mm / mmPerWu;
}

/** World units → millimeters. */
export function worldToMm(wu: number, mmPerWu: number = DEFAULT_MM_PER_WU): number {
  return wu * mmPerWu;
}

// ─── Combined position + scale shortcuts ──────────

/** ROS meters point → Three.js world-unit point (swap + scale). */
export function rosMetersToThreeWorld(p: RosPoint3, scale: number = DEFAULT_SCALE_FACTOR): ThreePoint3 {
  return rosToThree(p, scale);
}

/** Three.js world-unit point → ROS meters point (unswap + unscale). */
export function threeWorldToRosMeters(p: ThreePoint3, scale: number = DEFAULT_SCALE_FACTOR): RosPoint3 {
  return threeToRos(p, scale);
}

// ─── Rotation conversions ─────────────────────────

/**
 * ROS RPY (radians) → Three.js Euler angles (radians).
 *
 * ROS roll  = rotation about X-forward → Three rotation about -Z
 * ROS pitch = rotation about Y-left    → Three rotation about -X
 * ROS yaw   = rotation about Z-up      → Three rotation about +Y
 *
 * Returns { x, y, z } suitable for new Euler(x, y, z, 'YXZ') in Three.js.
 */
export function rosRPYToThree(rpy: RosRPY): { x: number; y: number; z: number } {
  return {
    x: -rpy.pitch,
    y:  rpy.yaw,
    z: -rpy.roll,
  };
}

/** Three.js Euler { x, y, z } (radians, YXZ) → ROS RPY (radians). */
export function threeToRosRPY(euler: { x: number; y: number; z: number }): RosRPY {
  return {
    roll:  -euler.z,
    pitch: -euler.x,
    yaw:    euler.y,
  };
}

// ─── Degree/radian helpers ────────────────────────

const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;

export function degToRad(deg: number): number {
  return deg * DEG2RAD;
}

export function radToDeg(rad: number): number {
  return rad * RAD2DEG;
}

// ─── Matrix helpers (column-major 4×4) ────────────

import type { Mat4 } from '@/types/coordinates';

/** Create identity 4×4. */
export function mat4Identity(): Mat4 {
  const m = new Float64Array(16);
  m[0] = 1; m[5] = 1; m[10] = 1; m[15] = 1;
  return m;
}

/** Multiply two 4×4 column-major matrices: result = A × B. */
export function mat4Multiply(a: Mat4, b: Mat4): Mat4 {
  const r = new Float64Array(16);
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 4; row++) {
      let sum = 0;
      for (let k = 0; k < 4; k++) {
        sum += a[k * 4 + row] * b[col * 4 + k];
      }
      r[col * 4 + row] = sum;
    }
  }
  return r;
}

/**
 * Build a 4×4 transform from translation (meters) and ZYX Euler rotation (radians).
 * Column-major layout.
 */
export function mat4FromPose(tx: number, ty: number, tz: number, rx: number, ry: number, rz: number): Mat4 {
  const cx = Math.cos(rx), sx = Math.sin(rx);
  const cy = Math.cos(ry), sy = Math.sin(ry);
  const cz = Math.cos(rz), sz = Math.sin(rz);

  const m = new Float64Array(16);
  // Column 0
  m[0]  = cy * cz;
  m[1]  = cy * sz;
  m[2]  = -sy;
  // Column 1
  m[4]  = sx * sy * cz - cx * sz;
  m[5]  = sx * sy * sz + cx * cz;
  m[6]  = sx * cy;
  // Column 2
  m[8]  = cx * sy * cz + sx * sz;
  m[9]  = cx * sy * sz - sx * cz;
  m[10] = cx * cy;
  // Column 3
  m[12] = tx;
  m[13] = ty;
  m[14] = tz;
  m[15] = 1;
  return m;
}

/**
 * Extract translation from a 4×4 column-major matrix.
 */
export function mat4GetTranslation(m: Mat4): RosPoint3 {
  return { x: m[12], y: m[13], z: m[14] };
}

/**
 * Transform a point by a 4×4 matrix.
 */
export function mat4TransformPoint(m: Mat4, p: RosPoint3): RosPoint3 {
  return {
    x: m[0] * p.x + m[4] * p.y + m[8]  * p.z + m[12],
    y: m[1] * p.x + m[5] * p.y + m[9]  * p.z + m[13],
    z: m[2] * p.x + m[6] * p.y + m[10] * p.z + m[14],
  };
}

// ─── COB (Change-of-Basis) matrices ────────────

/**
 * ROS→Three change-of-basis (column-major).
 *   ROS X → Three -Z,  ROS Y → Three -X,  ROS Z → Three +Y
 */
const _cobRosToThree: Mat4 = (() => {
  const m = new Float64Array(16);
  m[0] =  0; m[1] =  0; m[2] = -1; m[3] = 0;
  m[4] = -1; m[5] =  0; m[6] =  0; m[7] = 0;
  m[8] =  0; m[9] =  1; m[10]=  0; m[11]= 0;
  m[12]=  0; m[13]=  0; m[14]=  0; m[15]= 1;
  return m;
})();

/**
 * Three→ROS change-of-basis (column-major) = transpose of ROS→Three.
 *   Three X → ROS -Y,  Three Y → ROS +Z,  Three Z → ROS -X
 */
const _cobThreeToRos: Mat4 = (() => {
  const m = new Float64Array(16);
  m[0] =  0; m[1] = -1; m[2] =  0; m[3] = 0;
  m[4] =  0; m[5] =  0; m[6] =  1; m[7] = 0;
  m[8] = -1; m[9] =  0; m[10]=  0; m[11]= 0;
  m[12]=  0; m[13]=  0; m[14]=  0; m[15]= 1;
  return m;
})();

/**
 * Convert a 4×4 matrix from Three.js frame to ROS frame.
 *   M_ros = COB_inv × M_three × COB
 *
 * Use this to convert FK matrices computed in Three.js space
 * (using groupsPosition / jointAxes) into ROS convention for TransformAuthority.
 */
export function mat4ThreeToRos(mThree: Mat4): Mat4 {
  return mat4Multiply(mat4Multiply(_cobThreeToRos, mThree), _cobRosToThree);
}

/**
 * Invert a rigid-body 4×4 matrix (rotation + translation only).
 * For rigid transforms: R^-1 = R^T, and t' = -R^T × t.
 */
export function mat4InvertRigid(m: Mat4): Mat4 {
  const r = new Float64Array(16);
  // Transpose 3×3 rotation
  r[0] = m[0]; r[1] = m[4]; r[2]  = m[8];
  r[4] = m[1]; r[5] = m[5]; r[6]  = m[9];
  r[8] = m[2]; r[9] = m[6]; r[10] = m[10];
  // New translation: -R^T × t
  r[12] = -(r[0] * m[12] + r[4] * m[13] + r[8]  * m[14]);
  r[13] = -(r[1] * m[12] + r[5] * m[13] + r[9]  * m[14]);
  r[14] = -(r[2] * m[12] + r[6] * m[13] + r[10] * m[14]);
  r[15] = 1;
  return r;
}
