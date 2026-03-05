/**
 * depth-processor.test.ts — Unit tests for depth frame processing.
 */
import { describe, it, expect } from 'vitest';
import {
  calibrateDepthMm,
  rotatePixel,
  unprojectPixel,
  processDepthFrame,
  isNearBlackRgb,
  NEAR_BLACK_THRESHOLD,
} from '../domain/depth-processor';

const EPSILON = 1e-6;

describe('calibrateDepthMm', () => {
  it('applies linear correction: k × raw + b', () => {
    const cal = { k: 0.957, b: 45.2 };
    const result = calibrateDepthMm(500, cal);
    expect(Math.abs(result - (0.957 * 500 + 45.2))).toBeLessThan(EPSILON);
  });

  it('identity calibration: k=1, b=0', () => {
    expect(calibrateDepthMm(1234, { k: 1, b: 0 })).toBe(1234);
  });
});

describe('rotatePixel', () => {
  it('0° does nothing', () => {
    const r = rotatePixel(10, 20, 640, 480, 0);
    expect(r.u).toBe(10);
    expect(r.v).toBe(20);
    expect(r.w).toBe(640);
    expect(r.h).toBe(480);
  });

  it('90° rotation: (0,0,640,480) → (479,0,480,640)', () => {
    const r = rotatePixel(0, 0, 640, 480, 90);
    expect(r.u).toBe(479);
    expect(r.v).toBe(0);
    expect(r.w).toBe(480);
    expect(r.h).toBe(640);
  });

  it('180° rotation: (0,0,640,480) → (639,479,640,480)', () => {
    const r = rotatePixel(0, 0, 640, 480, 180);
    expect(r.u).toBe(639);
    expect(r.v).toBe(479);
  });

  it('270° rotation', () => {
    const r = rotatePixel(0, 0, 640, 480, 270);
    expect(r.u).toBe(0);
    expect(r.v).toBe(639);
    expect(r.w).toBe(480);
    expect(r.h).toBe(640);
  });

  it('360° = 0°', () => {
    const r = rotatePixel(10, 20, 640, 480, 360);
    expect(r.u).toBe(10);
    expect(r.v).toBe(20);
  });
});

describe('unprojectPixel', () => {
  it('center pixel → (0, 0, depth)', () => {
    const p = unprojectPixel(320, 240, 1.0, 380, 380, 320, 240);
    expect(Math.abs(p.x)).toBeLessThan(EPSILON);
    expect(Math.abs(p.y)).toBeLessThan(EPSILON);
    expect(Math.abs(p.z - 1.0)).toBeLessThan(EPSILON);
  });

  it('offset pixel produces correct XY', () => {
    // u=420, cx=320, fx=380, depth=2.0 → x = (420-320)/380 * 2.0
    const p = unprojectPixel(420, 240, 2.0, 380, 380, 320, 240);
    const expectedX = (100 / 380) * 2.0;
    expect(Math.abs(p.x - expectedX)).toBeLessThan(EPSILON);
  });
});

describe('processDepthFrame', () => {
  it('generates point cloud from synthetic depth data', () => {
    const W = 4, H = 4;
    const depthRaw = new Uint16Array(W * H);
    // Fill with 500mm depth (valid range)
    depthRaw.fill(500);

    const intrinsics = { fx: 380, fy: 380, cx: 2, cy: 2, width: W, height: H };
    const calibration = { k: 1.0, b: 0 };
    const overlay = {
      stridePx: 1,
      minRaw: 50,
      maxRaw: 2000,
      minDistanceM: 0.10,
      pixelRotationDeg: 0,
      flipX: false,
      flipY: false,
      swapPayloadWH: false,
      cloudMirrorVertical: false,
      cloudMirrorAxis: 'y' as const,
      cloudRotationDeg: { rx: 0, ry: 0, rz: 0 },
      depthShotColor: [0.45, 0.75, 0.95] as const,
    };

    const cloud = processDepthFrame(depthRaw, null, W, H, intrinsics, calibration, overlay);
    expect(cloud.count).toBe(16);
    expect(cloud.positions.length).toBe(48);
    expect(cloud.colors.length).toBe(48);

    // Default color should be depthShotColor
    expect(Math.abs(cloud.colors[0] - 0.45)).toBeLessThan(EPSILON);
  });

  it('filters out pixels below minRaw', () => {
    const W = 2, H = 2;
    const depthRaw = new Uint16Array([10, 500, 500, 10]);
    const intrinsics = { fx: 380, fy: 380, cx: 1, cy: 1, width: W, height: H };
    const calibration = { k: 1.0, b: 0 };
    const overlay = {
      stridePx: 1, minRaw: 50, maxRaw: 2000, minDistanceM: 0.05,
      rejectNearBlackRgb: true,
      nearBlackThreshold: 12,
      pixelRotationDeg: 0, flipX: false, flipY: false, swapPayloadWH: false,
      cloudMirrorVertical: false, cloudMirrorAxis: 'y' as const,
      cloudRotationDeg: { rx: 0, ry: 0, rz: 0 },
      depthShotColor: [0.45, 0.75, 0.95] as const,
    };

    const cloud = processDepthFrame(depthRaw, null, W, H, intrinsics, calibration, overlay);
    expect(cloud.count).toBe(2);
  });

  it('stride=2 produces ~4x fewer points', () => {
    const W = 8, H = 8;
    const depthRaw = new Uint16Array(W * H).fill(500);
    const intrinsics = { fx: 380, fy: 380, cx: 4, cy: 4, width: W, height: H };
    const calibration = { k: 1.0, b: 0 };
    const overlay = {
      stridePx: 2, minRaw: 50, maxRaw: 2000, minDistanceM: 0.05,
      pixelRotationDeg: 0, flipX: false, flipY: false, swapPayloadWH: false,
      cloudMirrorVertical: false, cloudMirrorAxis: 'y' as const,
      cloudRotationDeg: { rx: 0, ry: 0, rz: 0 },
      depthShotColor: [0.45, 0.75, 0.95] as const,
    };

    const cloud = processDepthFrame(depthRaw, null, W, H, intrinsics, calibration, overlay);
    expect(cloud.count).toBe(16); // 8/2 × 8/2
  });

  it('skips near-black RGB pixels when rgbRaw is present', () => {
    const W = 4, H = 4;
    const depthRaw = new Uint16Array(W * H).fill(500);
    const rgbRaw = new Uint8Array(W * H * 3).fill(128);
    // Make first 2 pixels near-black
    rgbRaw[0] = 5; rgbRaw[1] = 5; rgbRaw[2] = 5;   // pixel 0
    rgbRaw[3] = 10; rgbRaw[4] = 10; rgbRaw[5] = 10; // pixel 1

    const intrinsics = { fx: 100, fy: 100, cx: 2, cy: 2, width: W, height: H };
    const calibration = { k: 1, b: 0 };
    const overlay = {
      stridePx: 1, minRaw: 50, maxRaw: 2000, minDistanceM: 0.05,
      rejectNearBlackRgb: true,
      nearBlackThreshold: 12,
      pixelRotationDeg: 0, flipX: false, flipY: false, swapPayloadWH: false,
      cloudMirrorVertical: false, cloudMirrorAxis: 'y' as const,
      cloudRotationDeg: { rx: 0, ry: 0, rz: 0 },
      depthShotColor: [0.45, 0.75, 0.95] as const,
    };

    const cloud = processDepthFrame(depthRaw, rgbRaw, W, H, intrinsics, calibration, overlay);
    // 16 total pixels, 2 near-black should be skipped
    expect(cloud.count).toBe(14);
  });
});

describe('isNearBlackRgb', () => {
  it('detects pure black (0,0,0)', () => {
    expect(isNearBlackRgb(0, 0, 0)).toBe(true);
  });

  it('detects near-black below threshold', () => {
    expect(isNearBlackRgb(10, 10, 10)).toBe(true);
    expect(isNearBlackRgb(NEAR_BLACK_THRESHOLD, NEAR_BLACK_THRESHOLD, NEAR_BLACK_THRESHOLD)).toBe(true);
  });

  it('rejects pixels above threshold', () => {
    expect(isNearBlackRgb(13, 10, 10)).toBe(false);
    expect(isNearBlackRgb(128, 128, 128)).toBe(false);
    expect(isNearBlackRgb(255, 255, 255)).toBe(false);
  });

  it('requires ALL channels to be below threshold', () => {
    expect(isNearBlackRgb(5, 5, 200)).toBe(false);
    expect(isNearBlackRgb(0, 255, 0)).toBe(false);
  });

  it('accepts custom threshold', () => {
    expect(isNearBlackRgb(20, 20, 20, 25)).toBe(true);
    expect(isNearBlackRgb(20, 20, 20, 15)).toBe(false);
  });
});
