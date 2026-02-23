/**
 * depth-codec.test.ts + voxel-map.test.ts — Tests for depth codec and voxel map.
 */
import { describe, it, expect } from 'vitest';
import { encodeDMP1, decodeDMP1, isDMP1 } from '../domain/depth-codec';
import { VoxelMap } from '../domain/voxel-map';
import { mat4Identity } from '../domain/coord-utils';

describe('DMP1 codec', () => {
  it('roundtrip: encode → decode preserves data', () => {
    const voxels = [
      { x: 1.5, y: 2.5, z: 3.5, r: 0.1, g: 0.2, b: 0.3 },
      { x: -1, y: 0, z: 10, r: 0.9, g: 0.8, b: 0.7 },
    ];
    const buf = encodeDMP1(voxels);
    const result = decodeDMP1(buf);

    expect(result.version).toBe(1);
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
  });

  it('isDMP1 detects valid buffer', () => {
    const buf = encodeDMP1([{ x: 0, y: 0, z: 0, r: 0, g: 0, b: 0 }]);
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

  it('decodeDMP1 throws on truncated buffer', () => {
    const buf = encodeDMP1([{ x: 0, y: 0, z: 0, r: 0, g: 0, b: 0 }]);
    const truncated = buf.slice(0, 20); // cut off point data
    expect(() => decodeDMP1(truncated)).toThrow(/buffer too short/);
  });
});

describe('VoxelMap', () => {
  it('adds points and retrieves voxels', () => {
    const vm = new VoxelMap({ voxelSizeWu: 1.0, maxVoxels: 1000 });
    const positions = new Float32Array([0, 0, 1, 0.1, 0.1, 1]);
    const colors = new Float32Array([1, 0, 0, 0, 1, 0]);
    const I = mat4Identity();

    vm.addCloud(positions, colors, 2, I, 1.0);
    expect(vm.size).toBeGreaterThan(0);
    expect(vm.size).toBeLessThanOrEqual(2);
  });

  it('deduplicates voxels in same cell', () => {
    const vm = new VoxelMap({ voxelSizeWu: 10.0, maxVoxels: 1000 });
    // Both points fall in the same voxel (within 10 wu)
    const positions = new Float32Array([1, 1, 1, 2, 2, 2]);
    const colors = new Float32Array([1, 0, 0, 0, 1, 0]);
    const I = mat4Identity();

    vm.addCloud(positions, colors, 2, I, 1.0);
    expect(vm.size).toBe(1); // same voxel cell
  });

  it('respects maxVoxels limit', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.01, maxVoxels: 5 });
    const I = mat4Identity();

    // Add 10 clearly distinct points
    for (let i = 0; i < 10; i++) {
      const positions = new Float32Array([i * 100, 0, 0]);
      const colors = new Float32Array([1, 0, 0]);
      vm.addCloud(positions, colors, 1, I, 1.0);
    }

    expect(vm.size).toBeLessThanOrEqual(5);
  });

  it('clear removes all voxels', () => {
    const vm = new VoxelMap({ voxelSizeWu: 1.0, maxVoxels: 1000 });
    const I = mat4Identity();
    const positions = new Float32Array([0, 0, 1]);
    const colors = new Float32Array([1, 0, 0]);
    vm.addCloud(positions, colors, 1, I, 1.0);
    expect(vm.size).toBe(1);
    vm.clear();
    expect(vm.size).toBe(0);
  });

  it('getVoxels returns array of VoxelEntry', () => {
    const vm = new VoxelMap({ voxelSizeWu: 1.0, maxVoxels: 1000 });
    const I = mat4Identity();
    const positions = new Float32Array([5, 10, 15]);
    const colors = new Float32Array([0.5, 0.6, 0.7]);
    vm.addCloud(positions, colors, 1, I, 1.0);
    const voxels = vm.getVoxels();
    expect(voxels.length).toBe(1);
    expect(voxels[0].x).toBe(5);
    expect(voxels[0].r).toBe(0.5);
  });
});
