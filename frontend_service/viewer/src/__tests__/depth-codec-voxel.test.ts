/**
 * depth-codec.test.ts + voxel-map.test.ts — Tests for depth codec and voxel map.
 */
import { describe, it, expect } from 'vitest';
import { encodeDMP1, decodeDMP1, isDMP1, encodeDMP1Legacy } from '../domain/depth-codec';
import { VoxelMap } from '../domain/voxel-map';

const SAMPLE_VOXELS = [
  { x: 1.5, y: 2.5, z: 3.5, r: 0.1, g: 0.2, b: 0.3 },
  { x: -1, y: 0, z: 10, r: 0.9, g: 0.8, b: 0.7 },
];

describe('DMP1 codec V2 (default)', () => {
  it('roundtrip: encode → decode preserves data', () => {
    const buf = encodeDMP1(SAMPLE_VOXELS);
    const result = decodeDMP1(buf);

    expect(result.version).toBe(2);
    expect(result.voxels.length).toBe(2);

    // Float32 precision
    const eps = 1e-5;
    expect(Math.abs(result.voxels[0].x - 1.5)).toBeLessThan(eps);
    expect(Math.abs(result.voxels[0].r - 0.1)).toBeLessThan(eps);
    expect(Math.abs(result.voxels[1].z - 10)).toBeLessThan(eps);
    expect(Math.abs(result.voxels[1].b - 0.7)).toBeLessThan(eps);
  });

  it('empty array roundtrip', () => {
    const buf = encodeDMP1([]);
    const result = decodeDMP1(buf);
    expect(result.voxels.length).toBe(0);
    expect(result.version).toBe(2);
  });

  it('buffer size = 12 + N*24', () => {
    const buf = encodeDMP1(SAMPLE_VOXELS);
    expect(buf.byteLength).toBe(12 + 2 * 24);
  });
});

describe('DMP1 codec V1 (legacy)', () => {
  it('roundtrip: encodeLegacy → decode preserves data (uint8 RGB)', () => {
    const buf = encodeDMP1Legacy(SAMPLE_VOXELS);
    const result = decodeDMP1(buf);

    expect(result.version).toBe(1);
    expect(result.voxels.length).toBe(2);

    // XYZ preserved exactly (float32 for both)
    const eps = 1e-5;
    expect(Math.abs(result.voxels[0].x - 1.5)).toBeLessThan(eps);
    expect(Math.abs(result.voxels[1].z - 10)).toBeLessThan(eps);

    // RGB has uint8 quantization: 0..1 → 0..255 → 0..1
    // Tolerance = 1/255 ≈ 0.004
    const rgbEps = 1 / 255 + 1e-6;
    expect(Math.abs(result.voxels[0].r - 0.1)).toBeLessThan(rgbEps);
    expect(Math.abs(result.voxels[0].g - 0.2)).toBeLessThan(rgbEps);
    expect(Math.abs(result.voxels[1].b - 0.7)).toBeLessThan(rgbEps);
  });

  it('buffer size = 12 + N*15', () => {
    const buf = encodeDMP1Legacy(SAMPLE_VOXELS);
    expect(buf.byteLength).toBe(12 + 2 * 15);
  });

  it('empty array roundtrip for legacy', () => {
    const buf = encodeDMP1Legacy([]);
    const result = decodeDMP1(buf);
    expect(result.voxels.length).toBe(0);
    expect(result.version).toBe(1);
  });

  it('RGB clamping: values outside 0..1 are clamped', () => {
    const voxels = [{ x: 0, y: 0, z: 0, r: -0.5, g: 1.5, b: 0.5 }];
    const buf = encodeDMP1Legacy(voxels);
    const result = decodeDMP1(buf);
    expect(result.voxels[0].r).toBe(0); // clamped from -0.5
    expect(result.voxels[0].g).toBe(1); // clamped from 1.5
    const rgbEps = 1 / 255 + 1e-6;
    expect(Math.abs(result.voxels[0].b - 0.5)).toBeLessThan(rgbEps);
  });
});

describe('DMP1 auto-detection', () => {
  it('detects V2 from encodeDMP1', () => {
    const buf = encodeDMP1(SAMPLE_VOXELS);
    const result = decodeDMP1(buf);
    expect(result.version).toBe(2);
  });

  it('detects V1 from encodeDMP1Legacy', () => {
    const buf = encodeDMP1Legacy(SAMPLE_VOXELS);
    const result = decodeDMP1(buf);
    expect(result.version).toBe(1);
  });
});

describe('DMP1 validation', () => {
  it('isDMP1 detects valid V2 buffer', () => {
    const buf = encodeDMP1([{ x: 0, y: 0, z: 0, r: 0, g: 0, b: 0 }]);
    expect(isDMP1(buf)).toBe(true);
  });

  it('isDMP1 detects valid V1 buffer', () => {
    const buf = encodeDMP1Legacy([{ x: 0, y: 0, z: 0, r: 0, g: 0, b: 0 }]);
    expect(isDMP1(buf)).toBe(true);
  });

  it('isDMP1 rejects random data', () => {
    const buf = new ArrayBuffer(20);
    expect(isDMP1(buf)).toBe(false);
  });

  it('decodeDMP1 throws on invalid magic', () => {
    const buf = new ArrayBuffer(20);
    expect(() => decodeDMP1(buf)).toThrow(/invalid magic/);
  });

  it('decodeDMP1 throws on truncated V2 buffer', () => {
    const buf = encodeDMP1([{ x: 0, y: 0, z: 0, r: 0, g: 0, b: 0 }]);
    const truncated = buf.slice(0, 20);
    expect(() => decodeDMP1(truncated)).toThrow(/buffer too short/);
  });

  it('decodeDMP1 throws on truncated V1 buffer', () => {
    const buf = encodeDMP1Legacy([{ x: 0, y: 0, z: 0, r: 0, g: 0, b: 0 }]);
    const truncated = buf.slice(0, 20);
    expect(() => decodeDMP1(truncated)).toThrow(/buffer too short/);
  });

  it('decodeDMP1 throws on tiny buffer', () => {
    const buf = new ArrayBuffer(4);
    // Set magic but no version/count
    const view = new DataView(buf);
    view.setUint8(0, 0x44); view.setUint8(1, 0x4D); view.setUint8(2, 0x50); view.setUint8(3, 0x31);
    expect(() => decodeDMP1(buf)).toThrow(/buffer too short/);
  });
});

describe('VoxelMap', () => {
  it('adds points and retrieves voxels', () => {
    const vm = new VoxelMap({ voxelSizeWu: 1.0, maxVoxels: 1000 });
    const positions = new Float32Array([0, 0, 1, 0.1, 0.1, 1]);
    const colors = new Float32Array([1, 0, 0, 0, 1, 0]);

    vm.addCloud(positions, colors, 2);
    expect(vm.size).toBeGreaterThan(0);
    expect(vm.size).toBeLessThanOrEqual(2);
  });

  it('deduplicates voxels in same cell', () => {
    const vm = new VoxelMap({ voxelSizeWu: 10.0, maxVoxels: 1000 });
    // Both points fall in the same voxel (within 10 wu)
    const positions = new Float32Array([1, 1, 1, 2, 2, 2]);
    const colors = new Float32Array([1, 0, 0, 0, 1, 0]);

    vm.addCloud(positions, colors, 2);
    expect(vm.size).toBe(1); // same voxel cell
  });

  it('respects maxVoxels limit', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.01, maxVoxels: 5 });

    // Add 10 clearly distinct points
    for (let i = 0; i < 10; i++) {
      const positions = new Float32Array([i * 100, 0, 0]);
      const colors = new Float32Array([1, 0, 0]);
      vm.addCloud(positions, colors, 1);
    }

    expect(vm.size).toBeLessThanOrEqual(5);
  });

  it('clear removes all voxels', () => {
    const vm = new VoxelMap({ voxelSizeWu: 1.0, maxVoxels: 1000 });
    const positions = new Float32Array([0, 0, 1]);
    const colors = new Float32Array([1, 0, 0]);
    vm.addCloud(positions, colors, 1);
    expect(vm.size).toBe(1);
    vm.clear();
    expect(vm.size).toBe(0);
  });

  it('getVoxels returns array of VoxelEntry', () => {
    const vm = new VoxelMap({ voxelSizeWu: 1.0, maxVoxels: 1000 });
    // addCloud now consumes world-frame points directly
    const positions = new Float32Array([5, 10, 15]);
    const colors = new Float32Array([0.5, 0.6, 0.7]);
    vm.addCloud(positions, colors, 1);
    const voxels = vm.getVoxels();
    expect(voxels.length).toBe(1);
    expect(voxels[0].x).toBe(5);
    expect(voxels[0].r).toBe(0.5);
  });

  // ─── clearFrustum tests ───────────────────────────

  it('clearFrustum removes voxels inside frustum cone', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.5, maxVoxels: 1000 });
    const I = new Float64Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    // In camera-local Three.js space, forward is -Z, so front point has z < 0.
    const positions = new Float32Array([0, 0, -2]);
    const colors = new Float32Array([1, 0, 0]);
    vm.addCloud(positions, colors, 1);
    expect(vm.size).toBe(1);

    // Clear with a frustum that covers ±45° in both axes
    const removed = vm.clearFrustum(I, 1.0, 1.0, 1.0);
    expect(removed).toBe(1);
    expect(vm.size).toBe(0);
  });

  it('clearFrustum preserves voxels outside frustum cone', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.5, maxVoxels: 1000 });
    const I = new Float64Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    // Voxel far to the side at (100, 0, -1) — tanX = 100/1 = 100
    const positions = new Float32Array([100, 0, -1]);
    const colors = new Float32Array([1, 0, 0]);
    vm.addCloud(positions, colors, 1);
    expect(vm.size).toBe(1);

    // Frustum tanX=0.5 → voxel with tanX=100 is way outside
    const removed = vm.clearFrustum(I, 0.5, 0.5, 1.0);
    expect(removed).toBe(0);
    expect(vm.size).toBe(1);
  });

  it('clearFrustum ignores voxels too close (behind minEraseDist)', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.5, maxVoxels: 1000 });
    const I = new Float64Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    // Voxel at (0, 0, -0.05) → depth = 0.05 < 0.10 minEraseDist
    const positions = new Float32Array([0, 0, -0.05]);
    const colors = new Float32Array([1, 0, 0]);
    vm.addCloud(positions, colors, 1);

    const removed = vm.clearFrustum(I, 1.0, 1.0, 1.0);
    expect(removed).toBe(0); // too close, not erased
    expect(vm.size).toBe(1);
  });

  it('clearFrustum returns 0 for empty map', () => {
    const vm = new VoxelMap({ voxelSizeWu: 1.0, maxVoxels: 1000 });
    const removed = vm.clearFrustum(new Float64Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]), 1.0, 1.0, 1.0);
    expect(removed).toBe(0);
  });

  it('clearFrustum returns 0 for zero tangent bounds', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.5, maxVoxels: 1000 });
    const I = new Float64Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    const positions = new Float32Array([0, 0, -2]);
    const colors = new Float32Array([1, 0, 0]);
    vm.addCloud(positions, colors, 1);

    const removed = vm.clearFrustum(I, 0, 0, 1.0);
    expect(removed).toBe(0);
    expect(vm.size).toBe(1);
  });
});
