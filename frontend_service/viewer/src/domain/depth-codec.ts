/**
 * depth-codec.ts — DMP1 binary depth map encode/decode.
 *
 * Format (DMP1):
 *   [0..3]  magic: "DMP1" (4 bytes ASCII)
 *   [4..7]  version: uint32 LE (always 1)
 *   [8..11] pointCount: uint32 LE
 *   [12..]  points: array of 6 × float32 LE (x, y, z, r, g, b per point)
 *
 * Total bytes = 12 + pointCount × 24.
 *
 * Domain layer — no Three.js dependency.
 */

import type { VoxelEntry } from '@/types/depth';

const MAGIC = 'DMP1';
const VERSION = 1;
const HEADER_SIZE = 12;
const POINT_SIZE = 6 * 4; // 6 float32 = 24 bytes

// ─── Encode ───────────────────────────────────────

/**
 * Encode voxel entries into DMP1 binary buffer.
 */
export function encodeDMP1(voxels: readonly VoxelEntry[]): ArrayBuffer {
  const count = voxels.length;
  const buffer = new ArrayBuffer(HEADER_SIZE + count * POINT_SIZE);
  const view = new DataView(buffer);

  // Header: magic
  for (let i = 0; i < 4; i++) {
    view.setUint8(i, MAGIC.charCodeAt(i));
  }
  // Header: version
  view.setUint32(4, VERSION, true);
  // Header: pointCount
  view.setUint32(8, count, true);

  // Points
  let offset = HEADER_SIZE;
  for (const v of voxels) {
    view.setFloat32(offset, v.x, true); offset += 4;
    view.setFloat32(offset, v.y, true); offset += 4;
    view.setFloat32(offset, v.z, true); offset += 4;
    view.setFloat32(offset, v.r, true); offset += 4;
    view.setFloat32(offset, v.g, true); offset += 4;
    view.setFloat32(offset, v.b, true); offset += 4;
  }

  return buffer;
}

// ─── Decode ───────────────────────────────────────

export interface DMP1DecodeResult {
  readonly version: number;
  readonly voxels: VoxelEntry[];
}

/**
 * Decode a DMP1 binary buffer into voxel entries.
 * Throws on invalid magic or version.
 */
export function decodeDMP1(buffer: ArrayBuffer): DMP1DecodeResult {
  if (buffer.byteLength < HEADER_SIZE) {
    throw new Error('DMP1: buffer too short for header');
  }

  const view = new DataView(buffer);

  // Verify magic
  const magic = String.fromCharCode(
    view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3),
  );
  if (magic !== MAGIC) {
    throw new Error(`DMP1: invalid magic "${magic}"`);
  }

  const version = view.getUint32(4, true);
  if (version !== VERSION) {
    throw new Error(`DMP1: unsupported version ${version}`);
  }

  const count = view.getUint32(8, true);
  const expectedSize = HEADER_SIZE + count * POINT_SIZE;
  if (buffer.byteLength < expectedSize) {
    throw new Error(`DMP1: buffer too short (expected ${expectedSize}, got ${buffer.byteLength})`);
  }

  const voxels: VoxelEntry[] = [];
  let offset = HEADER_SIZE;
  for (let i = 0; i < count; i++) {
    voxels.push({
      x: view.getFloat32(offset, true),
      y: view.getFloat32(offset + 4, true),
      z: view.getFloat32(offset + 8, true),
      r: view.getFloat32(offset + 12, true),
      g: view.getFloat32(offset + 16, true),
      b: view.getFloat32(offset + 20, true),
    });
    offset += POINT_SIZE;
  }

  return { version, voxels };
}

/**
 * Check if a buffer looks like a valid DMP1 file (quick magic check).
 */
export function isDMP1(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 4) return false;
  const view = new DataView(buffer);
  return (
    view.getUint8(0) === 0x44 && // D
    view.getUint8(1) === 0x4D && // M
    view.getUint8(2) === 0x50 && // P
    view.getUint8(3) === 0x31    // 1
  );
}
