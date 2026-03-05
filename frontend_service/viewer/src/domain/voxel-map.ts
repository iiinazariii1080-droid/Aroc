/**
 * voxel-map.ts — Spatial voxel accumulation for depth map building.
 *
 * Domain layer — no Three.js dependency.
 *
 * Accumulates world-frame point clouds into a spatial grid.
 *
 * Voxel key: integer grid coordinates (floor(pos / voxelSize)).
 * Uses a Map for O(1) insert/lookup and ring-buffer eviction.
 */

import type { VoxelEntry } from '@/types/depth';

// ─── Constants ────────────────────────────────────

/** Minimum distance (meters) for frustum erasure to avoid clearing near-camera voxels. */
const MIN_ERASE_DIST_M = 0.10;

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
   * Add world-frame points to the voxel map.
   *
   * @param positions - World-frame XYZ in Three.js world units, interleaved Float32Array.
   * @param colors    - RGB 0..1, interleaved Float32Array.
   * @param count     - Number of points.
   */
  addCloud(
    positions: Float32Array,
    colors: Float32Array,
    count: number,
  ): void {
    for (let i = 0; i < count; i++) {
      const off = i * 3;
      const wx = positions[off];
      const wy = positions[off + 1];
      const wz = positions[off + 2];

      const k = this.key(wx, wy, wz);

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

    // Find the minimum order threshold to keep maxVoxels entries.
    // Use a single pass: delete any entry with order < cutoff.
    const cutoff = this.insertOrder - this.maxVoxels;
    for (const [key, voxel] of this.map) {
      if (voxel.order < cutoff) {
        this.map.delete(key);
      }
    }
  }

  /** Get all voxels as an array (for rendering / serialization). */
  getVoxels(): VoxelEntry[] {
    const result: VoxelEntry[] = [];
    for (const v of this.map.values()) {
      result.push({ x: v.x, y: v.y, z: v.z, r: v.r, g: v.g, b: v.b });
    }
    return result;
  }

  /**
   * Fill pre-allocated typed arrays with voxel data (zero-alloc hot path).
   *
   * @param positions - Float32Array to fill with XYZ (min length: size × 3).
   * @param colors    - Float32Array to fill with RGB (min length: size × 3).
   * @returns Number of voxels written.
   */
  fillArrays(positions: Float32Array, colors: Float32Array): number {
    let i = 0;
    for (const v of this.map.values()) {
      const off = i * 3;
      positions[off]     = v.x;
      positions[off + 1] = v.y;
      positions[off + 2] = v.z;
      colors[off]     = v.r;
      colors[off + 1] = v.g;
      colors[off + 2] = v.b;
      i++;
    }
    return i;
  }

  /**
   * Replace all voxels with pre-built entries (e.g. from loaded file).
   * Positions are assumed to already be in Three.js world units.
   * Skips entries with NaN/Infinity coordinates.
   */
  loadVoxels(entries: readonly VoxelEntry[]): void {
    this.map.clear();
    this.insertOrder = 0;
    for (const e of entries) {
      // Validate: skip NaN/Infinity entries that would corrupt the map
      if (!isFinite(e.x) || !isFinite(e.y) || !isFinite(e.z)) continue;

      const k = this.key(e.x, e.y, e.z);
      this.map.set(k, {
        x: e.x, y: e.y, z: e.z,
        r: e.r, g: e.g, b: e.b,
        order: this.insertOrder++,
      });
    }
    this.evict();
  }

  /** Number of active voxels. */
  get size(): number {
    return this.map.size;
  }

  /**
   * Erase voxels that fall inside a camera frustum cone.
   *
   * Uses the inverse camera world matrix (Three.js frame) to project
   * voxels back into camera-local space for frustum testing.
   *
   * @param inverseCameraWorldElements - 16-element column-major inverse
   *        camera world matrix (Three.js frame, world units).
   * @param frameTanX - Max tangent X half-angle of the current depth frame.
   * @param frameTanY - Max tangent Y half-angle of the current depth frame.
   * @param scaleFactor - world units per meter (for distance threshold).
   * @returns Number of removed voxels.
   */
  clearFrustum(
    inverseCameraWorldElements: ArrayLike<number>,
    frameTanX: number,
    frameTanY: number,
    scaleFactor: number,
  ): number {
    if (frameTanX <= 0 || frameTanY <= 0) return 0;

    const ie = inverseCameraWorldElements;
    const minEraseWu = MIN_ERASE_DIST_M * scaleFactor;

    const keysToDelete: string[] = [];
    for (const [key, voxel] of this.map) {
      // Voxel is in Three.js world units. Transform to camera-local:
      // cam = inverseWorldMatrix × voxel
      const cx = ie[0] * voxel.x + ie[4] * voxel.y + ie[8]  * voxel.z + ie[12];
      const cy = ie[1] * voxel.x + ie[5] * voxel.y + ie[9]  * voxel.z + ie[13];
      const cz = ie[2] * voxel.x + ie[6] * voxel.y + ie[10] * voxel.z + ie[14];

      // In camera-local Three.js space, the camera looks along -Z.
      // Depth in front of camera = -cz (positive for objects in front).
      const dist = -cz;
      if (dist <= minEraseWu) continue;

      // Tangent ratios (cx, cy are in world units, dist is in world units → unitless)
      const tx = Math.abs(cx) / Math.max(1e-6, dist);
      const ty = Math.abs(cy) / Math.max(1e-6, dist);

      if (tx <= frameTanX && ty <= frameTanY) {
        keysToDelete.push(key);
      }
    }

    for (const key of keysToDelete) {
      this.map.delete(key);
    }

    return keysToDelete.length;
  }

  /** Clear all voxels. */
  clear(): void {
    this.map.clear();
    this.insertOrder = 0;
  }
}
