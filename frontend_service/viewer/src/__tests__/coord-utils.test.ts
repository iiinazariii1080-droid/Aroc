/**
 * coord-utils.test.ts — Unit tests for coordinate conversions.
 */
import { describe, it, expect } from 'vitest';
import {
  rosToThree,
  threeToRos,
  metersToWorld,
  worldToMeters,
  mmToWorld,
  worldToMm,
  rosRPYToThree,
  threeToRosRPY,
  degToRad,
  radToDeg,
  mat4Identity,
  mat4Multiply,
  mat4FromPose,
  mat4GetTranslation,
  mat4TransformPoint,
  mat4InvertRigid,
  mat4ThreeToRos,
} from '../domain/coord-utils';

const EPSILON = 1e-10;
const close = (a: number, b: number) => Math.abs(a - b) < EPSILON;

describe('rosToThree / threeToRos', () => {
  it('ROS origin maps to Three origin', () => {
    const t = rosToThree({ x: 0, y: 0, z: 0 });
    expect(t.x).toBeCloseTo(0);
    expect(t.y).toBeCloseTo(0);
    expect(t.z).toBeCloseTo(0);
  });

  it('ROS (1,0,0) → Three (0,0,-20)', () => {
    const t = rosToThree({ x: 1, y: 0, z: 0 });
    expect(t.x).toBeCloseTo(0);
    expect(t.y).toBeCloseTo(0);
    expect(t.z).toBe(-20);
  });

  it('ROS (0,1,0) → Three (-20,0,0)', () => {
    const t = rosToThree({ x: 0, y: 1, z: 0 });
    expect(t.x).toBe(-20);
    expect(t.y).toBeCloseTo(0);
    expect(t.z).toBeCloseTo(0);
  });

  it('ROS (0,0,1) → Three (0,20,0)', () => {
    const t = rosToThree({ x: 0, y: 0, z: 1 });
    expect(t.x).toBeCloseTo(0);
    expect(t.y).toBe(20);
    expect(t.z).toBeCloseTo(0);
  });

  it('roundtrip: threeToRos(rosToThree(p)) === p', () => {
    const p = { x: 1.5, y: -0.3, z: 2.7 };
    const rt = threeToRos(rosToThree(p));
    expect(close(rt.x, p.x)).toBe(true);
    expect(close(rt.y, p.y)).toBe(true);
    expect(close(rt.z, p.z)).toBe(true);
  });
});

describe('scalar conversions', () => {
  it('metersToWorld(1) = 20', () => expect(metersToWorld(1)).toBe(20));
  it('worldToMeters(20) = 1', () => expect(worldToMeters(20)).toBe(1));
  it('mmToWorld(50) = 1', () => expect(mmToWorld(50)).toBe(1));
  it('worldToMm(1) = 50', () => expect(worldToMm(1)).toBe(50));
  it('roundtrip: worldToMeters(metersToWorld(0.123))', () => {
    expect(close(worldToMeters(metersToWorld(0.123)), 0.123)).toBe(true);
  });
});

describe('rotation conversions', () => {
  it('zero ROS RPY → zero Three euler', () => {
    const e = rosRPYToThree({ roll: 0, pitch: 0, yaw: 0 });
    expect(e.x).toBeCloseTo(0);
    expect(e.y).toBeCloseTo(0);
    expect(e.z).toBeCloseTo(0);
  });

  it('ROS yaw +90° → Three y +90°', () => {
    const halfPi = Math.PI / 2;
    const e = rosRPYToThree({ roll: 0, pitch: 0, yaw: halfPi });
    expect(close(e.y, halfPi)).toBe(true);
    expect(e.x).toBeCloseTo(0);
    expect(e.z).toBeCloseTo(0);
  });

  it('roundtrip: threeToRosRPY(rosRPYToThree(rpy)) === rpy', () => {
    const rpy = { roll: 0.3, pitch: -0.5, yaw: 1.2 };
    const rt = threeToRosRPY(rosRPYToThree(rpy));
    expect(close(rt.roll, rpy.roll)).toBe(true);
    expect(close(rt.pitch, rpy.pitch)).toBe(true);
    expect(close(rt.yaw, rpy.yaw)).toBe(true);
  });
});

describe('degToRad / radToDeg', () => {
  it('90° → π/2', () => expect(close(degToRad(90), Math.PI / 2)).toBe(true));
  it('π → 180°', () => expect(close(radToDeg(Math.PI), 180)).toBe(true));
  it('roundtrip', () => expect(close(radToDeg(degToRad(42.5)), 42.5)).toBe(true));
});

describe('mat4 operations', () => {
  it('identity × identity = identity', () => {
    const I = mat4Identity();
    const R = mat4Multiply(I, I);
    for (let i = 0; i < 16; i++) {
      expect(close(R[i], I[i])).toBe(true);
    }
  });

  it('mat4FromPose(0,0,0, 0,0,0) = identity', () => {
    const I = mat4Identity();
    const M = mat4FromPose(0, 0, 0, 0, 0, 0);
    for (let i = 0; i < 16; i++) {
      expect(close(M[i], I[i])).toBe(true);
    }
  });

  it('mat4FromPose translation extracted correctly', () => {
    const M = mat4FromPose(1.5, -2.0, 3.0, 0, 0, 0);
    const t = mat4GetTranslation(M);
    expect(close(t.x, 1.5)).toBe(true);
    expect(close(t.y, -2.0)).toBe(true);
    expect(close(t.z, 3.0)).toBe(true);
  });

  it('identity transforms point to same point', () => {
    const I = mat4Identity();
    const p = { x: 3, y: -1, z: 7 };
    const q = mat4TransformPoint(I, p);
    expect(close(q.x, p.x)).toBe(true);
    expect(close(q.y, p.y)).toBe(true);
    expect(close(q.z, p.z)).toBe(true);
  });

  it('translation-only matrix translates point', () => {
    const M = mat4FromPose(10, 20, 30, 0, 0, 0);
    const q = mat4TransformPoint(M, { x: 1, y: 2, z: 3 });
    expect(close(q.x, 11)).toBe(true);
    expect(close(q.y, 22)).toBe(true);
    expect(close(q.z, 33)).toBe(true);
  });

  it('mat4InvertRigid: M × M^-1 = identity', () => {
    const M = mat4FromPose(1, 2, 3, 0.5, -0.3, 0.8);
    const Mi = mat4InvertRigid(M);
    const I = mat4Multiply(M, Mi);
    const eye = mat4Identity();
    for (let i = 0; i < 16; i++) {
      expect(Math.abs(I[i] - eye[i])).toBeLessThan(1e-9);
    }
  });

  it('mat4TransformPoint + invert roundtrip', () => {
    const M = mat4FromPose(0.5, -1.2, 3.4, 0.1, -0.2, 0.3);
    const p = { x: 2, y: -3, z: 5 };
    const pTransformed = mat4TransformPoint(M, p);
    const Mi = mat4InvertRigid(M);
    const pBack = mat4TransformPoint(Mi, pTransformed);
    expect(Math.abs(pBack.x - p.x)).toBeLessThan(1e-9);
    expect(Math.abs(pBack.y - p.y)).toBeLessThan(1e-9);
    expect(Math.abs(pBack.z - p.z)).toBeLessThan(1e-9);
  });
});

describe('mat4ThreeToRos', () => {
  it('identity stays identity', () => {
    const I = mat4Identity();
    const Iros = mat4ThreeToRos(I);
    for (let i = 0; i < 16; i++) {
      expect(Iros[i]).toBeCloseTo(I[i], 10);
    }
  });

  it('pure Three.js Y translation → ROS Z translation', () => {
    // Three.js translation (0, 5, 0) → ROS (0, 0, 5)
    const T = mat4FromPose(0, 5, 0, 0, 0, 0);
    const Tros = mat4ThreeToRos(T);
    const pos = mat4GetTranslation(Tros);
    expect(pos.x).toBeCloseTo(0, 8);
    expect(pos.y).toBeCloseTo(0, 8);
    expect(pos.z).toBeCloseTo(5, 8);
  });

  it('pure Three.js -Z translation → ROS X translation', () => {
    // Three.js translation (0, 0, -3) → ROS (3, 0, 0)
    // Three Z → ROS -X, so Three -Z → ROS X
    const T = mat4FromPose(0, 0, -3, 0, 0, 0);
    const Tros = mat4ThreeToRos(T);
    const pos = mat4GetTranslation(Tros);
    expect(pos.x).toBeCloseTo(3, 8);
    expect(pos.y).toBeCloseTo(0, 8);
    expect(pos.z).toBeCloseTo(0, 8);
  });

  it('roundtrip: ros→three→ros preserves translation', () => {
    // A point at ROS (1, 2, 3) → Three (-2, 3, -1) → back to ROS (1, 2, 3)
    // ROS→Three manually: Three x=-rosY=-2, y=rosZ=3, z=-rosX=-1
    const threeT = mat4FromPose(-2, 3, -1, 0, 0, 0);
    const rosBack = mat4ThreeToRos(threeT);
    const pos = mat4GetTranslation(rosBack);
    expect(pos.x).toBeCloseTo(1, 8);
    expect(pos.y).toBeCloseTo(2, 8);
    expect(pos.z).toBeCloseTo(3, 8);
  });
});
