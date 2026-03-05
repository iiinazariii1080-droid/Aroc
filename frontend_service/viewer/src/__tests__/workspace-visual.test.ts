/**
 * workspace-visual.test.ts — Unit tests for workspace bounds computation.
 *
 * Tests the pure math in computeWorkspaceBounds which converts
 * ROS-frame workspace dimensions (mm) into Three.js Y-up world units.
 */

import { describe, it, expect } from 'vitest';
import { computeWorkspaceBounds } from '@/rendering/workspace-visual';

describe('computeWorkspaceBounds', () => {
  const SCALE = 20; // 1 m = 20 wu

  it('computes correct bounds for default xArm workspace', () => {
    const bounds = computeWorkspaceBounds(
      [400, 900, 1200],      // dimensions X×Y×Z mm
      [150, 450, 0],         // basePositionInWS mm
      SCALE,
    );

    // ROS base-relative:
    //   X: -150 to +250 mm
    //   Y: -450 to +450 mm
    //   Z:    0 to +1200 mm
    //
    // Three.js conversion (Three.x=ROS.x, Three.y=ROS.z, Three.z=-ROS.y):
    //   Three.x: -150/1000*20 to +250/1000*20  = -3.0 to +5.0
    //   Three.y:    0/1000*20 to +1200/1000*20  =  0.0 to +24.0
    //   Three.z: +450/1000*20 to -450/1000*20   = -9.0 to +9.0 (after normalize)

    expect(bounds.min.x).toBeCloseTo(-3.0, 5);
    expect(bounds.min.y).toBeCloseTo(0.0, 5);
    expect(bounds.min.z).toBeCloseTo(-9.0, 5);

    expect(bounds.max.x).toBeCloseTo(5.0, 5);
    expect(bounds.max.y).toBeCloseTo(24.0, 5);
    expect(bounds.max.z).toBeCloseTo(9.0, 5);
  });

  it('computes correct size', () => {
    const bounds = computeWorkspaceBounds(
      [400, 900, 1200],
      [150, 450, 0],
      SCALE,
    );

    // Size = max - min
    expect(bounds.size.x).toBeCloseTo(8.0, 5);   // 400mm = 0.4m × 20
    expect(bounds.size.y).toBeCloseTo(24.0, 5);   // 1200mm = 1.2m × 20
    expect(bounds.size.z).toBeCloseTo(18.0, 5);   // 900mm = 0.9m × 20
  });

  it('computes correct center', () => {
    const bounds = computeWorkspaceBounds(
      [400, 900, 1200],
      [150, 450, 0],
      SCALE,
    );

    // Center = (min + max) / 2
    expect(bounds.center.x).toBeCloseTo(1.0, 5);    // (-3 + 5) / 2
    expect(bounds.center.y).toBeCloseTo(12.0, 5);   // (0 + 24) / 2
    expect(bounds.center.z).toBeCloseTo(0.0, 5);    // (-9 + 9) / 2
  });

  it('handles symmetric workspace (base centered)', () => {
    const bounds = computeWorkspaceBounds(
      [1000, 1000, 1000],   // 1m cube
      [500, 500, 500],       // base at center
      SCALE,
    );

    // ROS base-relative: -500..+500 in each axis
    // Three.x: -10 to +10, Three.y: -10 to +10, Three.z: -10 to +10
    expect(bounds.min.x).toBeCloseTo(-10.0, 5);
    expect(bounds.min.y).toBeCloseTo(-10.0, 5);
    expect(bounds.min.z).toBeCloseTo(-10.0, 5);
    expect(bounds.max.x).toBeCloseTo(10.0, 5);
    expect(bounds.max.y).toBeCloseTo(10.0, 5);
    expect(bounds.max.z).toBeCloseTo(10.0, 5);
    expect(bounds.center.x).toBeCloseTo(0.0, 5);
    expect(bounds.center.y).toBeCloseTo(0.0, 5);
    expect(bounds.center.z).toBeCloseTo(0.0, 5);
  });

  it('handles base at workspace origin', () => {
    const bounds = computeWorkspaceBounds(
      [200, 300, 400],
      [0, 0, 0],         // base at workspace origin
      SCALE,
    );

    // ROS base-relative: X 0→200, Y 0→300, Z 0→400
    // Three: x 0→4, y 0→8, z -6→0
    expect(bounds.min.x).toBeCloseTo(0.0, 5);
    expect(bounds.min.y).toBeCloseTo(0.0, 5);
    expect(bounds.min.z).toBeCloseTo(-6.0, 5);
    expect(bounds.max.x).toBeCloseTo(4.0, 5);
    expect(bounds.max.y).toBeCloseTo(8.0, 5);
    expect(bounds.max.z).toBeCloseTo(0.0, 5);
  });

  it('scales correctly with different scale factor', () => {
    const bounds = computeWorkspaceBounds(
      [1000, 1000, 1000],
      [500, 500, 500],
      10,  // different scale
    );

    // 1m cube centered: ±500mm = ±0.5m × 10 = ±5 wu
    expect(bounds.size.x).toBeCloseTo(10.0, 5);
    expect(bounds.size.y).toBeCloseTo(10.0, 5);
    expect(bounds.size.z).toBeCloseTo(10.0, 5);
  });
});
