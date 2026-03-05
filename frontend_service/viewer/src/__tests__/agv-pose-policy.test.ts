import { describe, expect, it } from 'vitest';
import { AgvPosePolicy } from '@/rendering/agv-pose-policy';
import type { StaticTransform } from '@/types/coordinates';

const agvToArmBase: StaticTransform = {
  translation: { x: 1, y: 0, z: 0 },
  rotation: { roll: 0, pitch: 0, yaw: Math.PI / 2 },
  status: 'KNOWN',
};

describe('AgvPosePolicy', () => {
  it('applies remap flags from config', () => {
    const policy = new AgvPosePolicy(
      {
        anchorMode: 'agv_center',
        swapXY: true,
        invertX: false,
        invertY: true,
        invertYaw: false,
        yawOffsetDeg: 10,
      },
      agvToArmBase,
    );

    const res = policy.resolve({ x_m: 2, y_m: 3, theta_deg: 5, map_id: 1 });

    expect(res.remapped.xM).toBe(3);
    expect(res.remapped.yM).toBe(-2);
    expect(res.remapped.thetaDeg).toBe(15);
    expect(res.arm.xM).toBe(3);
    expect(res.arm.yM).toBe(-2);
    expect(res.arm.thetaDeg).toBe(15);
  });

  it('applies arm_base anchor offset and yaw composition', () => {
    const policy = new AgvPosePolicy(
      {
        anchorMode: 'arm_base',
        swapXY: false,
        invertX: false,
        invertY: false,
        invertYaw: false,
        yawOffsetDeg: 0,
      },
      agvToArmBase,
    );

    const res = policy.resolve({ x_m: 10, y_m: 20, theta_deg: 0, map_id: 1 });

    expect(res.arm.xM).toBeCloseTo(11, 6);
    expect(res.arm.yM).toBeCloseTo(20, 6);
    expect(res.arm.thetaDeg).toBeCloseTo(90, 6);
  });
});
