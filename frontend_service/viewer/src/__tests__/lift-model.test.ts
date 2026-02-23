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
} from '../domain/lift-model';

const EPSILON = 1e-9;

describe('liftDisplacementWu', () => {
  it('0 motor units → 0 wu', () => {
    expect(liftDisplacementWu(0)).toBe(0);
  });

  it('10000 motor units → 1.55 wu', () => {
    expect(Math.abs(liftDisplacementWu(10_000) - 1.55)).toBeLessThan(EPSILON);
  });

  it('120000 motor units → 18.6 wu', () => {
    expect(Math.abs(liftDisplacementWu(120_000) - 18.6)).toBeLessThan(EPSILON);
  });
});

describe('liftDisplacementMeters', () => {
  it('10000 motor units → 0.0775 m', () => {
    expect(Math.abs(liftDisplacementMeters(10_000) - 0.0775)).toBeLessThan(EPSILON);
  });
});

describe('liftObliqueDisplacement_threeWu', () => {
  it('0 motor units → zero vector', () => {
    const d = liftObliqueDisplacement_threeWu(0);
    expect(d.dx).toBeCloseTo(0);
    expect(d.dy).toBeCloseTo(0);
    expect(d.dz).toBeCloseTo(0);
  });

  it('10000 motor units → oblique vector with correct ratios', () => {
    const d = liftObliqueDisplacement_threeWu(10_000);
    const L = 1.55;
    expect(Math.abs(d.dx - L * (-1 / 3.5))).toBeLessThan(EPSILON);
    expect(Math.abs(d.dy - L * 1.0)).toBeLessThan(EPSILON);
    expect(Math.abs(d.dz - L * (-1 / 2))).toBeLessThan(EPSILON);
  });

  it('dy is always the largest component (primary vertical)', () => {
    const d = liftObliqueDisplacement_threeWu(50_000);
    expect(Math.abs(d.dy)).toBeGreaterThan(Math.abs(d.dx));
    expect(Math.abs(d.dy)).toBeGreaterThan(Math.abs(d.dz));
  });
});

describe('group1AbsoluteY_threeWu', () => {
  it('0 motor units → 5.34 wu (NOT legacy 5.0)', () => {
    const y = group1AbsoluteY_threeWu(0);
    expect(Math.abs(y - 5.34)).toBeLessThan(EPSILON);
  });

  it('increases with motor units', () => {
    const y0 = group1AbsoluteY_threeWu(0);
    const y1 = group1AbsoluteY_threeWu(50_000);
    expect(y1).toBeGreaterThan(y0);
  });
});

describe('clampMotorUnits', () => {
  it('clamps below 0', () => expect(clampMotorUnits(-100)).toBe(0));
  it('clamps above 120000', () => expect(clampMotorUnits(200_000)).toBe(120_000));
  it('preserves in-range values', () => expect(clampMotorUnits(60_000)).toBe(60_000));
});
