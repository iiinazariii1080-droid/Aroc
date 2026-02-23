/**
 * validators.test.ts — Unit tests for input validators.
 */
import { describe, it, expect } from 'vitest';
import {
  detectAngleUnit,
  normalizeAnglesToDeg,
  normalizeAnglesToRad,
  normalizeJoints,
  isValidVariant,
  clamp,
  normalizeDeg180,
  normalizeDeg360,
} from '../domain/validators';

describe('detectAngleUnit', () => {
  it('detects degrees (typical xArm joints)', () => {
    expect(detectAngleUnit([0, -45, 90, 0, -120, 30])).toBe('deg');
  });

  it('detects radians (small values)', () => {
    expect(detectAngleUnit([0, -0.5, 1.2, 0, -1.8, 0.3])).toBe('rad');
  });

  it('edge case: all zeros → rad (conservative)', () => {
    expect(detectAngleUnit([0, 0, 0, 0, 0, 0])).toBe('rad');
  });
});

describe('normalizeAnglesToDeg', () => {
  it('passes through degree values unchanged', () => {
    const input = [0, -45, 90, 0, -120, 30];
    const result = normalizeAnglesToDeg(input);
    expect(result).toEqual(input);
  });

  it('converts radians to degrees', () => {
    const result = normalizeAnglesToDeg([Math.PI / 2, -Math.PI]);
    expect(Math.abs(result[0] - 90)).toBeLessThan(1e-10);
    expect(Math.abs(result[1] - (-180))).toBeLessThan(1e-10);
  });
});

describe('normalizeAnglesToRad', () => {
  it('converts degrees to radians', () => {
    const result = normalizeAnglesToRad([90, -180]);
    expect(Math.abs(result[0] - Math.PI / 2)).toBeLessThan(1e-10);
    expect(Math.abs(result[1] - (-Math.PI))).toBeLessThan(1e-10);
  });
});

describe('normalizeJoints', () => {
  it('pads short array with zeros', () => {
    const result = normalizeJoints([10, 20], 6);
    expect(result.length).toBe(6);
    expect(result[2]).toBe(0);
    expect(result[5]).toBe(0);
  });

  it('trims long array', () => {
    const result = normalizeJoints([1, 2, 3, 4, 5, 6, 7, 8], 6);
    expect(result.length).toBe(6);
  });

  it('passes correct length unchanged', () => {
    const result = normalizeJoints([10, 20, 30, 40, 50, 60], 6);
    expect(result.length).toBe(6);
    expect(result[0]).toBe(10);
  });
});

describe('isValidVariant', () => {
  it('accepts known variants', () => {
    expect(isValidVariant('6-6')).toBe(true);
    expect(isValidVariant('7-13')).toBe(true);
    expect(isValidVariant('5-5')).toBe(true);
  });

  it('rejects unknown variants', () => {
    expect(isValidVariant('6-99')).toBe(false);
    expect(isValidVariant('foo')).toBe(false);
  });
});

describe('clamp', () => {
  it('clamps below minimum', () => expect(clamp(-5, 0, 100)).toBe(0));
  it('clamps above maximum', () => expect(clamp(150, 0, 100)).toBe(100));
  it('preserves in-range', () => expect(clamp(50, 0, 100)).toBe(50));
});

describe('normalizeDeg180', () => {
  it('normalizes 270 → -90', () => expect(normalizeDeg180(270)).toBe(-90));
  it('normalizes -270 → 90', () => expect(normalizeDeg180(-270)).toBe(90));
  it('preserves 45', () => expect(normalizeDeg180(45)).toBe(45));
  it('normalizes 360 → 0', () => expect(normalizeDeg180(360)).toBe(0));
});

describe('normalizeDeg360', () => {
  it('normalizes -90 → 270', () => expect(normalizeDeg360(-90)).toBe(270));
  it('preserves 90', () => expect(normalizeDeg360(90)).toBe(90));
  it('normalizes 720 → 0', () => expect(normalizeDeg360(720)).toBe(0));
});
