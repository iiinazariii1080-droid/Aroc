/**
 * arm-kinematics.test.ts — Unit tests for FK computation.
 */
import { describe, it, expect } from 'vitest';
import { computeFK, computeFKForVariant, computeGroupSetup, getAxisCount } from '../domain/arm-kinematics';
import { ARM_SPECS } from '../config/arm-specs';

const D = Math.PI / 180;
const EPSILON = 1e-10;

describe('computeFK', () => {
  it('6-6: all zeros → J1 offset = -180° only', () => {
    const spec = ARM_SPECS['6-6'];
    const fk = computeFK([0, 0, 0, 0, 0, 0], spec);
    expect(fk.rotations.length).toBe(6);

    // Joint 1 (group 1): (0 + (-180)) * 1 = -180°, axis Y
    expect(fk.rotations[0].groupIndex).toBe(1);
    expect(fk.rotations[0].axis).toBe('y');
    expect(Math.abs(fk.rotations[0].angle - (-180 * D))).toBeLessThan(EPSILON);

    // Joint 2 (group 2): (0 + 0) * -1 = 0°, axis X
    expect(fk.rotations[1].groupIndex).toBe(2);
    expect(fk.rotations[1].axis).toBe('x');
    expect(Math.abs(fk.rotations[1].angle)).toBeLessThan(EPSILON);
  });

  it('6-6: matches legacy updateFn sign pattern', () => {
    const spec = ARM_SPECS['6-6'];
    const M = [30, 45, -60, 20, -15, 10];
    const fk = computeFK(M, spec);

    // Legacy: g[1].rotation.y = z(M[0]-180) → (30-180)=(-150)° → -150*D
    expect(Math.abs(fk.rotations[0].angle - ((-150) * D))).toBeLessThan(EPSILON);

    // Legacy: g[2].rotation.x = -z(M[1])  → -(45*D) = sign=-1, offset=0
    expect(Math.abs(fk.rotations[1].angle - (-45 * D))).toBeLessThan(EPSILON);

    // Legacy: g[3].rotation.x = -z(M[2])  → -(-60*D) = +60*D. sign=-1, offset=0 → (-60+0)*-1=60
    expect(Math.abs(fk.rotations[2].angle - (60 * D))).toBeLessThan(EPSILON);

    // Legacy: g[4].rotation.y = -z(M[3])  → -(20*D). sign=-1, offset=0 → (20+0)*-1=-20
    expect(Math.abs(fk.rotations[3].angle - (-20 * D))).toBeLessThan(EPSILON);

    // Legacy: g[5].rotation.x = -z(M[4])  → -(-15*D)=+15*D. sign=-1 → (-15+0)*-1=15
    expect(Math.abs(fk.rotations[4].angle - (15 * D))).toBeLessThan(EPSILON);

    // Legacy: g[6].rotation.y = -z(M[5])  → -(10*D). sign=-1 → (10+0)*-1=-10
    expect(Math.abs(fk.rotations[5].angle - (-10 * D))).toBeLessThan(EPSILON);
  });

  it('5-5: 5 joints', () => {
    const spec = ARM_SPECS['5-5'];
    const fk = computeFK([0, 0, 0, 0, 0], spec);
    expect(fk.rotations.length).toBe(5);
  });

  it('7-7: 7 joints', () => {
    const spec = ARM_SPECS['7-7'];
    const fk = computeFK([0, 0, 0, 0, 0, 0, 0], spec);
    expect(fk.rotations.length).toBe(7);
  });

  it('7-13: matches legacy sign pattern', () => {
    const spec = ARM_SPECS['7-13'];
    const M = [10, 20, 30, 40, 50, 60, 70];
    const fk = computeFK(M, spec);

    // Legacy g[1].rotation.y = z(M[0]-180)  → sign=1, offset=-180  → (10-180)*1=-170
    expect(Math.abs(fk.rotations[0].angle - (-170 * D))).toBeLessThan(EPSILON);

    // Legacy g[2].rotation.x = z(M[1])      → sign=1, offset=0    → 20*1=20
    expect(Math.abs(fk.rotations[1].angle - (20 * D))).toBeLessThan(EPSILON);

    // Legacy g[3].rotation.y = z(M[2])      → sign=1, offset=0    → 30*1=30
    expect(Math.abs(fk.rotations[2].angle - (30 * D))).toBeLessThan(EPSILON);

    // Legacy g[4].rotation.x = -z(M[3])     → sign=-1, offset=0   → 40*-1=-40
    expect(Math.abs(fk.rotations[3].angle - (-40 * D))).toBeLessThan(EPSILON);

    // Legacy g[5].rotation.y = -z(M[4])     → sign=-1, offset=0   → 50*-1=-50
    expect(Math.abs(fk.rotations[4].angle - (-50 * D))).toBeLessThan(EPSILON);

    // Legacy g[6].rotation.x = z(M[5])      → sign=1, offset=0    → 60*1=60
    expect(Math.abs(fk.rotations[5].angle - (60 * D))).toBeLessThan(EPSILON);

    // Legacy g[7].rotation.y = -z(M[6])     → sign=-1, offset=0   → 70*-1=-70
    expect(Math.abs(fk.rotations[6].angle - (-70 * D))).toBeLessThan(EPSILON);
  });
});

describe('computeFKForVariant', () => {
  it('resolves 6-6 by axis and type', () => {
    const fk = computeFKForVariant(6, 6, [0, 0, 0, 0, 0, 0]);
    expect(fk.rotations.length).toBe(6);
  });

  it('falls back for unknown type', () => {
    const fk = computeFKForVariant(6, 999, [0, 0, 0, 0, 0, 0]);
    expect(fk.rotations.length).toBe(6); // falls back to 6-6
  });
});

describe('computeGroupSetup', () => {
  it('returns correct count for 6-6', () => {
    const spec = ARM_SPECS['6-6'];
    const setup = computeGroupSetup(spec);
    expect(setup.length).toBe(spec.groupsPosition.length);
  });

  it('scales positions by 20', () => {
    const spec = ARM_SPECS['6-6'];
    const setup = computeGroupSetup(spec, [-90, 0, -90], 20);
    // groupsPosition[0] = [0, -0.25, 0] → [0, -5, 0]
    expect(setup[0].position[0]).toBe(0);
    expect(setup[0].position[1]).toBe(-5);
    expect(setup[0].position[2]).toBe(0);
  });

  it('adds meshRotationBaseDeg to meshsRotation', () => {
    const spec = ARM_SPECS['6-6'];
    const setup = computeGroupSetup(spec, [-90, 0, -90]);
    // meshsRotation[0] = [0, 0, 0] + [-90, 0, -90] = [-90, 0, -90]
    expect(setup[0].meshRotationDeg[0]).toBe(-90);
    expect(setup[0].meshRotationDeg[1]).toBe(0);
    expect(setup[0].meshRotationDeg[2]).toBe(-90);
  });
});

describe('getAxisCount', () => {
  it('5-5 → 5', () => expect(getAxisCount('5-5')).toBe(5));
  it('6-12 → 6', () => expect(getAxisCount('6-12')).toBe(6));
  it('7-13 → 7', () => expect(getAxisCount('7-13')).toBe(7));
});
