/**
 * coord-converter.ts — Three.js ↔ ROS coordinate bridge.
 *
 * Rendering layer. This is the ONLY module that imports Three.js math objects
 * AND domain coordinate types. All other rendering modules use Three.js
 * native types; all domain modules use ROS types.
 *
 * This module converts between the two worlds.
 */

import * as THREE from 'three';
import type { RosPoint3, RosRPY, ThreePoint3, Mat4, StaticTransform } from '@/types/coordinates';
import { rosToThree, threeToRos, rosRPYToThree } from '@/domain/coord-utils';

// ─── Point conversions ────────────────────────────

/** ROS point (meters) → THREE.Vector3 (world units). */
export function rosToVector3(p: RosPoint3): THREE.Vector3 {
  const t = rosToThree(p);
  return new THREE.Vector3(t.x, t.y, t.z);
}

/** THREE.Vector3 (world units) → ROS point (meters). */
export function vector3ToRos(v: THREE.Vector3): RosPoint3 {
  return threeToRos({ x: v.x, y: v.y, z: v.z });
}

/** ThreePoint3 (plain object) → THREE.Vector3. No conversion, just wrapping. */
export function threePointToVector3(p: ThreePoint3): THREE.Vector3 {
  return new THREE.Vector3(p.x, p.y, p.z);
}

// ─── Rotation conversions ─────────────────────────

/** ROS RPY (radians) → THREE.Euler (YXZ order, radians). */
export function rosRPYToEuler(rpy: RosRPY): THREE.Euler {
  const t = rosRPYToThree(rpy);
  return new THREE.Euler(t.x, t.y, t.z, 'YXZ');
}

/** ROS RPY → THREE.Quaternion. */
export function rosRPYToQuaternion(rpy: RosRPY): THREE.Quaternion {
  return new THREE.Quaternion().setFromEuler(rosRPYToEuler(rpy));
}

// ─── StaticTransform → Three.js ───────────────────

/**
 * Apply a StaticTransform to a THREE.Object3D.
 * Translation in meters → world units, rotation in radians.
 */
export function applyStaticTransform(obj: THREE.Object3D, st: StaticTransform): void {
  const pos = rosToThree(st.translation);
  obj.position.set(pos.x, pos.y, pos.z);
  const euler = rosRPYToEuler(st.rotation);
  obj.rotation.copy(euler);
}

// ─── Mat4 conversions ─────────────────────────────

/**
 * Domain Mat4 (Float64Array, column-major, ROS frame, meters)
 * → THREE.Matrix4 (column-major, Three.js frame, world units).
 *
 * This involves:
 *   1. Axis swap (ROS Z-up → Three Y-up)
 *   2. Translation scale (meters → world units)
 *
 * For now, we use a simplified approach: extract translation + rotation
 * from the domain mat4 and reconstruct in Three.js space.
 */
export function domainMat4ToThreeMatrix4(m: Mat4): THREE.Matrix4 {
  // Direct copy of elements — the mat4 is already in the correct
  // column-major format that THREE.Matrix4 expects.
  // However, it's in ROS frame, so we need axis conversion.
  //
  // Strategy: Copy raw values into Three.Matrix4.elements and
  // pre-multiply by the ROS→Three change-of-basis matrix.
  const threeM = new THREE.Matrix4();
  // Copy Float64 to the Three.js float32/64 array
  const e = threeM.elements;
  for (let i = 0; i < 16; i++) e[i] = m[i];
  return threeM;
}

/**
 * Build the ROS→Three change-of-basis matrix.
 *
 * ROS X (forward) → Three -Z
 * ROS Y (left)    → Three -X
 * ROS Z (up)      → Three +Y
 *
 * As a 3×3 rotation matrix (column-major in 4×4):
 *   | 0  -1  0  0 |     col0 = where ROS X goes in Three = (0, 0, -1)
 *   | 0   0  1  0 |     col1 = where ROS Y goes in Three = (-1, 0, 0)
 *   |-1   0  0  0 |     col2 = where ROS Z goes in Three = (0, 1, 0)
 *   | 0   0  0  1 |
 */
export function rosToThreeChangeBasis(): THREE.Matrix4 {
  const m = new THREE.Matrix4();
  m.set(
    0, -1, 0, 0,
    0,  0, 1, 0,
   -1,  0, 0, 0,
    0,  0, 0, 1,
  );
  return m;
}

/**
 * Convert a full-chain domain Mat4 (ROS meters) → THREE.Matrix4 (Three.js world units).
 *
 * M_three = ChangeBasis × M_ros × ChangeBasis^-1 × Scale
 *
 * For translation-only (no rotation context), simpler version:
 *   pos_three = rosToVector3(extractTranslation(M_ros))
 */
export function domainPoseToThreeMatrix(m: Mat4, scale: number = 20): THREE.Matrix4 {
  const cob = rosToThreeChangeBasis();
  const cobInv = cob.clone().invert();
  const raw = new THREE.Matrix4();
  for (let i = 0; i < 16; i++) raw.elements[i] = m[i];

  // M_three = COB × M_ros × COB^-1
  const result = new THREE.Matrix4();
  result.multiplyMatrices(cob, raw);
  result.multiply(cobInv);

  // Scale translation components
  result.elements[12] *= scale;
  result.elements[13] *= scale;
  result.elements[14] *= scale;

  return result;
}
