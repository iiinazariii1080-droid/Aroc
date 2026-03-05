/**
 * lift-model.test.ts — Unit tests for lift model.
 */
import { describe, it, expect } from 'vitest';
import {
  liftDisplacementWu,
  liftDisplacementMeters,
  liftObliqueDisplacement_threeWu,
  group1AbsoluteY_threeWu,
  clampMotorUnits,
  type LiftConfig,
} from '../domain/lift-model';

const EPSILON = 1e-9;

/** Default test config matching scene-config LIFT values. */
const CFG: LiftConfig = {
  worldUnitsPerTenK: 1.55,
  directionPerL_threeYup: { dx: -1 / 3.5, dy: 1.0, dz: -1 / 2 },
  group1BaseY_wu: 0.267 * 20, // 5.34
  positionLimits: { min: 0, max: 120_000 },
  scaleFactor: 20,
};

describe('liftDisplacementWu', () => {
  it('0 motor units → 0 wu', () => {
    expect(liftDisplacementWu(0, CFG)).toBe(0);
  });

  it('10000 motor units → 1.55 wu', () => {
    expect(Math.abs(liftDisplacementWu(10_000, CFG) - 1.55)).toBeLessThan(EPSILON);
  });

  it('120000 motor units → 18.6 wu', () => {
    expect(Math.abs(liftDisplacementWu(120_000, CFG) - 18.6)).toBeLessThan(EPSILON);
  });
});

describe('liftDisplacementMeters', () => {
  it('10000 motor units → 0.0775 m', () => {
    expect(Math.abs(liftDisplacementMeters(10_000, CFG) - 0.0775)).toBeLessThan(EPSILON);
  });
});

describe('liftObliqueDisplacement_threeWu', () => {
  it('0 motor units → zero vector', () => {
    const d = liftObliqueDisplacement_threeWu(0, CFG);
    expect(d.dx).toBeCloseTo(0);
    expect(d.dy).toBeCloseTo(0);
    expect(d.dz).toBeCloseTo(0);
  });

  it('10000 motor units → oblique vector with correct ratios', () => {
    const d = liftObliqueDisplacement_threeWu(10_000, CFG);
    const L = 1.55;
    expect(Math.abs(d.dx - L * (-1 / 3.5))).toBeLessThan(EPSILON);
    expect(Math.abs(d.dy - L * 1.0)).toBeLessThan(EPSILON);
    expect(Math.abs(d.dz - L * (-1 / 2))).toBeLessThan(EPSILON);
  });

  it('dy is always the largest component (primary vertical)', () => {
    const d = liftObliqueDisplacement_threeWu(50_000, CFG);
    expect(Math.abs(d.dy)).toBeGreaterThan(Math.abs(d.dx));
    expect(Math.abs(d.dy)).toBeGreaterThan(Math.abs(d.dz));
  });
});

describe('group1AbsoluteY_threeWu', () => {
  it('0 motor units → 5.34 wu (NOT legacy 5.0)', () => {
    const y = group1AbsoluteY_threeWu(0, CFG);
    expect(Math.abs(y - 5.34)).toBeLessThan(EPSILON);
  });

  it('increases with motor units', () => {
    const y0 = group1AbsoluteY_threeWu(0, CFG);
    const y1 = group1AbsoluteY_threeWu(50_000, CFG);
    expect(y1).toBeGreaterThan(y0);
  });
});

describe('clampMotorUnits', () => {
  it('clamps below 0', () => expect(clampMotorUnits(-100, CFG)).toBe(0));
  it('clamps above 120000', () => expect(clampMotorUnits(200_000, CFG)).toBe(120_000));
  it('preserves in-range values', () => expect(clampMotorUnits(60_000, CFG)).toBe(60_000));
});
