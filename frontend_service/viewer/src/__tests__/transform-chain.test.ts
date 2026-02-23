/**
 * transform-chain.test.ts — Unit tests for the rigid-body transform chain.
 */
import { describe, it, expect } from 'vitest';
import {
  poseToMat4,
  staticTransformToMat4,
  computeMountTransform,
  legacyMountPositionOffsets_threeWu,
  resolveChain,
} from '../domain/transform-chain';
import { mat4Identity, mat4GetTranslation } from '../domain/coord-utils';

const EPSILON = 1e-9;

describe('poseToMat4', () => {
  it('zero pose → identity', () => {
    const M = poseToMat4({
      position: { x: 0, y: 0, z: 0 },
      orientation: { roll: 0, pitch: 0, yaw: 0 },
    });
    const I = mat4Identity();
    for (let i = 0; i < 16; i++) {
      expect(Math.abs(M[i] - I[i])).toBeLessThan(EPSILON);
    }
  });

  it('pure translation preserved', () => {
    const M = poseToMat4({
      position: { x: 1, y: 2, z: 3 },
      orientation: { roll: 0, pitch: 0, yaw: 0 },
    });
    const t = mat4GetTranslation(M);
    expect(Math.abs(t.x - 1)).toBeLessThan(EPSILON);
    expect(Math.abs(t.y - 2)).toBeLessThan(EPSILON);
    expect(Math.abs(t.z - 3)).toBeLessThan(EPSILON);
  });
});

describe('staticTransformToMat4', () => {
  it('known flange→camera transform has correct translation', () => {
    const M = staticTransformToMat4({
      translation: { x: 0.044, y: -0.168, z: -0.027 },
      rotation: { roll: -Math.PI / 2, pitch: 0, yaw: -Math.PI / 2 },
      status: 'KNOWN',
    });
    const t = mat4GetTranslation(M);
    expect(Math.abs(t.x - 0.044)).toBeLessThan(EPSILON);
    expect(Math.abs(t.y - (-0.168))).toBeLessThan(EPSILON);
    expect(Math.abs(t.z - (-0.027))).toBeLessThan(EPSILON);
  });
});

describe('computeMountTransform', () => {
  it('zero mount → identity', () => {
    const M = computeMountTransform({ tilt: 0, rotation: 0 });
    const I = mat4Identity();
    for (let i = 0; i < 16; i++) {
      expect(Math.abs(M[i] - I[i])).toBeLessThan(EPSILON);
    }
  });

  it('non-zero mount produces non-identity', () => {
    const M = computeMountTransform({ tilt: 30, rotation: -90 });
    // Should not be identity
    expect(Math.abs(M[0] - 1) > 0.01 || Math.abs(M[5] - 1) > 0.01).toBe(true);
  });
});

describe('legacyMountPositionOffsets_threeWu', () => {
  it('tilt=0 → posX=0, posY=-5', () => {
    const p = legacyMountPositionOffsets_threeWu(0);
    expect(Math.abs(p.x)).toBeLessThan(EPSILON);
    expect(Math.abs(p.y - (-5))).toBeLessThan(EPSILON);
  });

  it('tilt=90 → posX=5, posY=-2', () => {
    const p = legacyMountPositionOffsets_threeWu(90);
    expect(Math.abs(p.x - 5)).toBeLessThan(EPSILON);
    expect(Math.abs(p.y - (-2))).toBeLessThan(EPSILON);
  });

  it('tilt=180 → posX=0, posY=1', () => {
    const p = legacyMountPositionOffsets_threeWu(180);
    expect(Math.abs(p.x)).toBeLessThan(EPSILON);
    expect(Math.abs(p.y - 1)).toBeLessThan(EPSILON);
  });
});

describe('resolveChain', () => {
  it('all-identity chain yields identity-like poses', () => {
    const I = mat4Identity();
    const zeroPose = { position: { x: 0, y: 0, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } };
    const zeroST = { translation: { x: 0, y: 0, z: 0 }, rotation: { roll: 0, pitch: 0, yaw: 0 }, status: 'KNOWN' as const };

    const result = resolveChain({
      worldToAgv: zeroPose,
      agvToArmBase: zeroST,
      liftDisplacement: zeroPose,
      armBaseToFlange: I,
      flangeToCamera: zeroST,
      flangeToGripper: zeroST,
    });

    expect(Math.abs(result.armBase.position.x)).toBeLessThan(EPSILON);
    expect(Math.abs(result.tcp.position.x)).toBeLessThan(EPSILON);
    expect(Math.abs(result.camera.position.x)).toBeLessThan(EPSILON);
  });

  it('AGV position propagates to arm base', () => {
    const I = mat4Identity();
    const zeroST = { translation: { x: 0, y: 0, z: 0 }, rotation: { roll: 0, pitch: 0, yaw: 0 }, status: 'KNOWN' as const };
    const zeroPose = { position: { x: 0, y: 0, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } };

    const result = resolveChain({
      worldToAgv: { position: { x: 5, y: 3, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } },
      agvToArmBase: zeroST,
      liftDisplacement: zeroPose,
      armBaseToFlange: I,
      flangeToCamera: zeroST,
      flangeToGripper: zeroST,
    });

    expect(Math.abs(result.armBase.position.x - 5)).toBeLessThan(EPSILON);
    expect(Math.abs(result.armBase.position.y - 3)).toBeLessThan(EPSILON);
  });

  it('flange→camera offset appears in camera position', () => {
    const I = mat4Identity();
    const zeroPose = { position: { x: 0, y: 0, z: 0 }, orientation: { roll: 0, pitch: 0, yaw: 0 } };
    const zeroST = { translation: { x: 0, y: 0, z: 0 }, rotation: { roll: 0, pitch: 0, yaw: 0 }, status: 'KNOWN' as const };

    const result = resolveChain({
      worldToAgv: zeroPose,
      agvToArmBase: zeroST,
      liftDisplacement: zeroPose,
      armBaseToFlange: I,
      flangeToCamera: {
        translation: { x: 0.044, y: -0.168, z: -0.027 },
        rotation: { roll: 0, pitch: 0, yaw: 0 },  // no rotation for simple test
        status: 'KNOWN',
      },
      flangeToGripper: zeroST,
    });

    expect(Math.abs(result.camera.position.x - 0.044)).toBeLessThan(EPSILON);
    expect(Math.abs(result.camera.position.y - (-0.168))).toBeLessThan(EPSILON);
    expect(Math.abs(result.camera.position.z - (-0.027))).toBeLessThan(EPSILON);
  });
});
