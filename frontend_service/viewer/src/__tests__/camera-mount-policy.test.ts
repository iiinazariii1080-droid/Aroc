import { describe, expect, it } from 'vitest';
import { CameraMountPolicy } from '@/rendering/camera-mount-policy';
import type { StaticTransform } from '@/types/coordinates';

describe('CameraMountPolicy', () => {
  it('maps flangeToCamera static transform to Three pose consistently', () => {
    const policy = new CameraMountPolicy(20);
    const tf: StaticTransform = {
      translation: { x: 0.03, y: -0.03, z: -0.15 },
      rotation: { roll: -Math.PI / 2, pitch: Math.PI / 2, yaw: 0 },
      status: 'KNOWN',
    };

    const pose = policy.fromFlangeToCamera(tf);

    expect(pose.px).toBeCloseTo(0.6, 6);
    expect(pose.py).toBeCloseTo(-3.0, 6);
    expect(pose.pz).toBeCloseTo(-0.6, 6);
    expect(pose.rx).toBeCloseTo(-Math.PI / 2, 6);
    expect(pose.ry).toBeCloseTo(0, 6);
    expect(pose.rz).toBeCloseTo(Math.PI / 2, 6);
  });

  it('maps runtime debug transform in same convention', () => {
    const policy = new CameraMountPolicy(20);
    const pose = policy.fromDebugTransform({
      tx: 0.03,
      ty: -0.03,
      tz: -0.15,
      rxDeg: -90,
      ryDeg: 90,
      rzDeg: 0,
    });

    expect(pose.px).toBeCloseTo(0.6, 6);
    expect(pose.py).toBeCloseTo(-3.0, 6);
    expect(pose.pz).toBeCloseTo(-0.6, 6);
    expect(pose.rx).toBeCloseTo(-Math.PI / 2, 6);
    expect(pose.ry).toBeCloseTo(0, 6);
    expect(pose.rz).toBeCloseTo(Math.PI / 2, 6);
  });
});
