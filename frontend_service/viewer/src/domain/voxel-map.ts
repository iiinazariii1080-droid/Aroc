/**
 * voxel-map.ts — Spatial voxel accumulation for depth map building.
 *
 * Domain layer — no Three.js dependency.
 *
 * Converts camera-frame point clouds to world-frame voxels using
 * the transform chain, then accumulates into a spatial grid.
 *
 * Voxel key: integer grid coordinates (floor(pos / voxelSize)).
 * Uses a Map for O(1) insert/lookup and ring-buffer eviction.
 */

import type { VoxelEntry } from '@/types/depth';
import type { Mat4 } from '@/types/coordinates';
import { mat4TransformPoint } from './coord-utils';

// ─── Voxel grid ───────────────────────────────────

export interface VoxelMapConfig {
  /** Voxel size in world units. */
  readonly voxelSizeWu: number;
  /** Maximum number of voxels before oldest are evicted. */
  readonly maxVoxels: number;
}

/** Internal voxel with insert order for ring-buffer eviction. */
interface InternalVoxel {
  x: number;
  y: number;
  z: number;
  r: number;
  g: number;
  b: number;
  order: number;
}

export class VoxelMap {
  private readonly voxelSize: number;
  private readonly maxVoxels: number;
  private readonly map = new Map<string, InternalVoxel>();
  private insertOrder = 0;

  constructor(config: VoxelMapConfig) {
    this.voxelSize = config.voxelSizeWu;
    this.maxVoxels = config.maxVoxels;
  }

  /** Voxel key from world-unit position. */
  private key(x: number, y: number, z: number): string {
    const gx = Math.floor(x / this.voxelSize);
    const gy = Math.floor(y / this.voxelSize);
    const gz = Math.floor(z / this.voxelSize);
    return `${gx},${gy},${gz}`;
  }

  /**
   * Add a camera-frame point cloud, transforming each point to world frame.
   *
   * @param positions - Camera-frame XYZ (meters), interleaved Float32Array.
   * @param colors    - RGB 0..1, interleaved Float32Array.
   * @param count     - Number of points.
   * @param cameraToWorld - 4×4 transform: camera → world (meters).
   * @param scaleFactor - meters → world units.
   */
  addCloud(
    positions: Float32Array,
    colors: Float32Array,
    count: number,
    cameraToWorld: Mat4,
    scaleFactor: number,
  ): void {
    for (let i = 0; i < count; i++) {
      const off = i * 3;
      const camPt = { x: positions[off], y: positions[off + 1], z: positions[off + 2] };

      // Camera frame → world frame (meters)
      const worldM = mat4TransformPoint(cameraToWorld, camPt);

      // Meters → world units
      const wx = worldM.x * scaleFactor;
      const wy = worldM.y * scaleFactor;
      const wz = worldM.z * scaleFactor;

      const k = this.key(wx, wy, wz);

      // Update or insert voxel
      this.map.set(k, {
        x: wx, y: wy, z: wz,
        r: colors[off], g: colors[off + 1], b: colors[off + 2],
        order: this.insertOrder++,
      });
    }

    // Evict oldest if over capacity
    this.evict();
  }

  /** Ring-buffer eviction: remove oldest voxels if over maxVoxels. */
  private evict(): void {
    if (this.map.size <= this.maxVoxels) return;

    // Collect all entries and sort by insert order
    const entries = [...this.map.entries()];
    entries.sort((a, b) => a[1].order - b[1].order);

    // Remove oldest until within budget
    const toRemove = entries.length - this.maxVoxels;
    for (let i = 0; i < toRemove; i++) {
      this.map.delete(entries[i][0]);
    }
  }

  /** Get all voxels as an array (for rendering). */
  getVoxels(): VoxelEntry[] {
    const result: VoxelEntry[] = [];
    for (const v of this.map.values()) {
      result.push({ x: v.x, y: v.y, z: v.z, r: v.r, g: v.g, b: v.b });
    }
    return result;
  }

  /** Number of active voxels. */
  get size(): number {
    return this.map.size;
  }

  /** Clear all voxels. */
  clear(): void {
    this.map.clear();
    this.insertOrder = 0;
  }
}
