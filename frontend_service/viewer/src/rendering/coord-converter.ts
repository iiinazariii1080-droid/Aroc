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
import type { RosPoint3, RosRPY, ThreePoint3 } from '@/types/coordinates';
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
