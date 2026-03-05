/**
 * lift-model.ts — Pure-math model for the Igus linear actuator.
 *
 * The lift does NOT move purely vertically. It follows an oblique path
 * parameterized by the direction vector passed as configuration.
 *
 * Domain layer — no Three.js dependency. No config imports.
 * All inputs/outputs in ROS convention (meters, Z-up) unless noted.
 */

// ─── Configuration interface ─────────────────────

export interface LiftConfig {
  /** World units per 10,000 motor units. */
  readonly worldUnitsPerTenK: number;
  /** Oblique direction vector per 1 world-unit of lift travel (Three.js Y-up). */
  readonly directionPerL_threeYup: { readonly dx: number; readonly dy: number; readonly dz: number };
  /** groups[1] geometric base Y position in world units. */
  readonly group1BaseY_wu: number;
  /** Motor unit range. */
  readonly positionLimits: { readonly min: number; readonly max: number };
  /** 1 meter = this many world units. */
  readonly scaleFactor: number;
}

/**
 * Convert motor units → displacement in world units (scalar distance along lift axis).
 * Formula: wu = (motorUnits / 10 000) × worldUnitsPerTenK
 */
export function liftDisplacementWu(motorUnits: number, cfg: LiftConfig): number {
  return (motorUnits / 10_000) * cfg.worldUnitsPerTenK;
}

/**
 * Convert motor units → displacement in meters (scalar).
 */
export function liftDisplacementMeters(motorUnits: number, cfg: LiftConfig): number {
  return liftDisplacementWu(motorUnits, cfg) / cfg.scaleFactor;
}

/**
 * Oblique displacement vector in Three.js Y-up world units.
 *
 * The lift travels along a tilted axis. For L world-units of motor travel:
 *   dx = L × directionPerL_threeYup.dx
 *   dy = L × directionPerL_threeYup.dy
 *   dz = L × directionPerL_threeYup.dz
 *
 * Returns {dx, dy, dz} in Three.js world units (Y-up frame).
 */
export function liftObliqueDisplacement_threeWu(motorUnits: number, cfg: LiftConfig): { dx: number; dy: number; dz: number } {
  const L = liftDisplacementWu(motorUnits, cfg);
  return {
    dx: L * cfg.directionPerL_threeYup.dx,
    dy: L * cfg.directionPerL_threeYup.dy,
    dz: L * cfg.directionPerL_threeYup.dz,
  };
}

/**
 * Compute the absolute Y position of groups[1] in Three.js world units.
 * This is the geometrically correct value = group1BaseY_wu + liftDisplacement.
 *
 * NOTE: Legacy code used 5.0 instead of 5.34 (17 mm error). We use the geometric value.
 */
export function group1AbsoluteY_threeWu(motorUnits: number, cfg: LiftConfig): number {
  return cfg.group1BaseY_wu + liftDisplacementWu(motorUnits, cfg);
}

/**
 * Clamp motor units to valid range.
 */
export function clampMotorUnits(value: number, cfg: LiftConfig): number {
  return Math.max(cfg.positionLimits.min, Math.min(cfg.positionLimits.max, value));
}
