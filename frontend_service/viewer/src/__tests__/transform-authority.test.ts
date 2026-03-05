import { describe, it, expect } from 'vitest';
import { TransformAuthority } from '@/domain/transform-authority';
import { mat4Identity } from '@/domain/coord-utils';
import type { TransformChainState } from '@/types/transforms';

function makeState(): TransformChainState {
  return {
    worldToAgv: {
      position: { x: 1, y: 2, z: 0 },
      orientation: { roll: 0, pitch: 0, yaw: 0 },
    },
    agvToArmBase: {
      translation: { x: 0.1, y: 0.2, z: 0.3 },
      rotation: { roll: 0, pitch: 0, yaw: 0 },
      status: 'KNOWN',
    },
    mountOrientation: { tilt: 0, rotation: 0 },
    liftDisplacement: {
      position: { x: 0, y: 0, z: 0.5 },
      orientation: { roll: 0, pitch: 0, yaw: 0 },
    },
    armBaseToFlange: mat4Identity(),
    flangeToCamera: {
      translation: { x: 0.01, y: 0.02, z: 0.03 },
      rotation: { roll: 0, pitch: 0, yaw: 0 },
      status: 'KNOWN',
    },
    flangeToGripper: {
      translation: { x: 0, y: 0, z: 0 },
      rotation: { roll: 0, pitch: 0, yaw: 0 },
      status: 'KNOWN',
    },
  };
}

describe('TransformAuthority', () => {
  it('resolves nested world positions via parent-child chain', () => {
    const authority = new TransformAuthority();
    const resolved = authority.resolve(makeState());

    expect(resolved.armBase.position.x).toBeCloseTo(1.1, 6);
    expect(resolved.armBase.position.y).toBeCloseTo(2.2, 6);
    expect(resolved.armBase.position.z).toBeCloseTo(0.3, 6);

    expect(resolved.tcp.position.x).toBeCloseTo(1.1, 6);
    expect(resolved.tcp.position.y).toBeCloseTo(2.2, 6);
    expect(resolved.tcp.position.z).toBeCloseTo(0.8, 6);

    expect(resolved.camera.position.x).toBeCloseTo(1.11, 6);
    expect(resolved.camera.position.y).toBeCloseTo(2.22, 6);
    expect(resolved.camera.position.z).toBeCloseTo(0.83, 6);
  });

  it('mount tilt (ry) rotates armBase around Y axis', () => {
    const authority = new TransformAuthority();
    const state = makeState();
    // Override: AGV at origin, armBase at (0,0,1), tilt=90° → ry=90°
    const s = {
      ...state,
      worldToAgv: { position: { x: 0, y: 0, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } },
      agvToArmBase: {
        translation: { x: 0, y: 0, z: 1 },
        rotation: { roll: 0, pitch: 0, yaw: 0 },
        status: 'KNOWN' as const,
      },
      mountOrientation: { tilt: 90, rotation: 0 },
      liftDisplacement: { position: { x: 0, y: 0, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } },
      armBaseToFlange: mat4Identity(),
      flangeToCamera: { translation: { x: 0, y: 0, z: 0 }, rotation: { roll: 0, pitch: 0, yaw: 0 }, status: 'KNOWN' as const },
      flangeToGripper: { translation: { x: 0, y: 0, z: 0 }, rotation: { roll: 0, pitch: 0, yaw: 0 }, status: 'KNOWN' as const },
    };
    const resolved = authority.resolve(s);

    // With 90° pitch (tilt), armBase z=1 should rotate to x=1, z≈0
    expect(resolved.armBase.position.x).toBeCloseTo(1, 4);
    expect(resolved.armBase.position.z).toBeCloseTo(0, 4);
  });

  it('mount rotation (rx) rotates armBase around X axis', () => {
    const authority = new TransformAuthority();
    const s = {
      ...makeState(),
      worldToAgv: { position: { x: 0, y: 0, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } },
      agvToArmBase: {
        translation: { x: 0, y: 0, z: 1 },
        rotation: { roll: 0, pitch: 0, yaw: 0 },
        status: 'KNOWN' as const,
      },
      mountOrientation: { tilt: 0, rotation: 90 },
      liftDisplacement: { position: { x: 0, y: 0, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } },
      armBaseToFlange: mat4Identity(),
      flangeToCamera: { translation: { x: 0, y: 0, z: 0 }, rotation: { roll: 0, pitch: 0, yaw: 0 }, status: 'KNOWN' as const },
      flangeToGripper: { translation: { x: 0, y: 0, z: 0 }, rotation: { roll: 0, pitch: 0, yaw: 0 }, status: 'KNOWN' as const },
    };
    const resolved = authority.resolve(s);

    // With 90° roll (rotation), armBase z=1 should rotate to y=-1, z≈0
    expect(resolved.armBase.position.y).toBeCloseTo(-1, 4);
    expect(resolved.armBase.position.z).toBeCloseTo(0, 4);
  });
});
