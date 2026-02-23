/**
 * validators.ts — Input validation and normalization for arm data.
 *
 * Domain layer — no Three.js dependency.
 */

import type { ArmVariant } from '@/types/arm-state';
import { ARM_SPECS } from '@/config/arm-specs';

// ─── Angle normalization ──────────────────────────

const DEG2RAD = Math.PI / 180;

/**
 * Detect whether an array of joint values is in radians (typical |val| < 2π)
 * or degrees (typical |val| > 2π for at least one joint).
 *
 * Heuristic: if ALL absolute values are ≤ 2π, assume radians.
 * This works because xArm joint limits are ~±360° so valid degree values are usually > 6.28.
 */
export function detectAngleUnit(angles: readonly number[]): 'deg' | 'rad' {
  const TWO_PI = 2 * Math.PI;
  for (const a of angles) {
    if (Math.abs(a) > TWO_PI) return 'deg';
  }
  return 'rad';
}

/**
 * Normalize angles to degrees.
 * If input is in radians (auto-detected), converts to degrees.
 * If already degrees, returns as-is.
 */
export function normalizeAnglesToDeg(angles: readonly number[]): number[] {
  if (detectAngleUnit(angles) === 'rad') {
    return angles.map(a => a / DEG2RAD);
  }
  return [...angles];
}

/**
 * Normalize angles to radians.
 */
export function normalizeAnglesToRad(angles: readonly number[]): number[] {
  if (detectAngleUnit(angles) === 'deg') {
    return angles.map(a => a * DEG2RAD);
  }
  return [...angles];
}

// ─── Joint count validation ───────────────────────

/**
 * Ensure the joints array length matches the expected axis count.
 * Pads with zeros if too short, trims if too long.
 */
export function normalizeJoints(angles: readonly number[], axisCount: number): number[] {
  const result = normalizeAnglesToDeg(angles);
  while (result.length < axisCount) result.push(0);
  return result.slice(0, axisCount);
}

// ─── Variant validation ───────────────────────────

const VALID_VARIANTS = new Set(Object.keys(ARM_SPECS));

export function isValidVariant(key: string): key is ArmVariant {
  return VALID_VARIANTS.has(key);
}

// ─── Range checking ───────────────────────────────

/**
 * Clamp a number to [min, max].
 */
export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/**
 * Normalize an angle to [-180, 180] range (degrees).
 */
export function normalizeDeg180(deg: number): number {
  let d = deg % 360;
  if (d > 180) d -= 360;
  if (d < -180) d += 360;
  return d;
}

/**
 * Normalize an angle to [0, 360) range (degrees).
 */
export function normalizeDeg360(deg: number): number {
  let d = deg % 360;
  if (d < 0) d += 360;
  return d;
}

// ─── Timestamp validation ─────────────────────────

/**
 * Check if a timestamp is reasonable (within last 24h and not in the future).
 */
export function isReasonableTimestamp(ts: number): boolean {
  const now = Date.now();
  const dayMs = 86_400_000;
  return ts > now - dayMs && ts <= now + 5000;  // 5s tolerance for clock drift
}
