import { describe, it, expect } from 'vitest';
import { ARM_CANONICAL_VISUAL, WORLD } from '../config/scene-config';

describe('Transform canon invariants', () => {
  it('keeps world convention fixed', () => {
    expect(WORLD.convention).toBe('Z-up');
    expect(WORLD.coordinateSystem).toBe('ROS');
  });

  it('keeps canonical visual baseline free of extra z-spin', () => {
    expect(ARM_CANONICAL_VISUAL.rotationDeg[2]).toBe(0);
  });
});
