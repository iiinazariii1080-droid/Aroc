/**
 * lift-model.ts — Pure-math model for the Igus linear actuator.
 *
 * The lift does NOT move purely vertically. It follows an oblique path
 * parameterized by the direction vector in scene-config LIFT.
 *
 * Domain layer — no Three.js dependency.
 * All inputs/outputs in ROS convention (meters, Z-up) unless noted.
 */

import { LIFT, WORLD } from '@/config/scene-config';

/**
 * Convert motor units → displacement in world units (scalar distance along lift axis).
 * Formula: wu = (motorUnits / 10 000) × worldUnitsPerTenK
 */
export function liftDisplacementWu(motorUnits: number): number {
  return (motorUnits / 10_000) * LIFT.worldUnitsPerTenK;
}

/**
 * Convert motor units → displacement in meters (scalar).
 */
export function liftDisplacementMeters(motorUnits: number): number {
  return liftDisplacementWu(motorUnits) / WORLD.SCALE_FACTOR;
}

/**
 * Oblique displacement vector in Three.js Y-up world units.
 *
 * The lift travels along a tilted axis. For L world-units of motor travel:
 *   dx = L × (-1/3.5)    // slight X shift
 *   dy = L × 1.0          // primary vertical
 *   dz = L × (-1/2)       // slight Z shift
 *
 * Returns {dx, dy, dz} in Three.js world units (Y-up frame).
 */
export function liftObliqueDisplacement_threeWu(motorUnits: number): { dx: number; dy: number; dz: number } {
  const L = liftDisplacementWu(motorUnits);
  return {
    dx: L * LIFT.directionPerL_threeYup.dx,
    dy: L * LIFT.directionPerL_threeYup.dy,
    dz: L * LIFT.directionPerL_threeYup.dz,
  };
}

/**
 * Compute the absolute Y position of groups[1] in Three.js world units.
 * This is the geometrically correct value = group1BaseY_wu + liftDisplacement.
 *
 * NOTE: Legacy code used 5.0 instead of 5.34 (17 mm error). We use the geometric value.
 */
export function group1AbsoluteY_threeWu(motorUnits: number): number {
  return LIFT.group1BaseY_wu + liftDisplacementWu(motorUnits);
}

/**
 * Clamp motor units to valid range.
 */
export function clampMotorUnits(value: number): number {
  return Math.max(LIFT.positionLimits.min, Math.min(LIFT.positionLimits.max, value));
}
