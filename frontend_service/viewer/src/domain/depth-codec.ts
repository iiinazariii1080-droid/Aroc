/**
 * depth-codec.ts — DMP1 binary depth map encode/decode.
 *
 * Format V2 (new, 24 bytes/point):
 *   [0..3]  magic: "DMP1" (4 bytes ASCII)
 *   [4..7]  version: uint32 LE (value = 2)
 *   [8..11] pointCount: uint32 LE
 *   [12..]  points: array of 6 × float32 LE (x, y, z, r, g, b per point)
 *   Total bytes = 12 + pointCount × 24.
 *
 * Format V1 (legacy, 15 bytes/point):
 *   [0..3]  magic: "DMP1" (4 bytes ASCII)
 *   [4..5]  version: uint16 LE (value = 1)
 *   [6..7]  padding (0)
 *   [8..11] pointCount: uint32 LE
 *   [12..]  points: 3 × float32 LE (x, y, z) + 3 × uint8 (r, g, b) per point
 *   Total bytes = 12 + pointCount × 15.
 *
 * Domain layer — no Three.js dependency.
 */

import type { VoxelEntry } from '@/types/depth';

const MAGIC = 'DMP1';
const VERSION_V2 = 2;
const VERSION_V1_LEGACY = 1;
const HEADER_SIZE = 12;
const POINT_SIZE_V2 = 6 * 4; // 6 float32 = 24 bytes
const POINT_SIZE_V1 = 3 * 4 + 3; // 3 float32 + 3 uint8 = 15 bytes

// ─── Encode (always V2) ──────────────────────────────

/**
 * Encode voxel entries into DMP1 V2 binary buffer.
 */
export function encodeDMP1(voxels: readonly VoxelEntry[]): ArrayBuffer {
  const count = voxels.length;
  const buffer = new ArrayBuffer(HEADER_SIZE + count * POINT_SIZE_V2);
  const view = new DataView(buffer);

  // Header: magic
  for (let i = 0; i < 4; i++) {
    view.setUint8(i, MAGIC.charCodeAt(i));
  }
  // Header: version (V2)
  view.setUint32(4, VERSION_V2, true);
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

// ─── Decode (auto-detect V1 or V2) ──────────────────

export interface DMP1DecodeResult {
  readonly version: number;
  readonly voxels: VoxelEntry[];
}

/**
 * Decode a DMP1 binary buffer into voxel entries.
 * Auto-detects V1 (legacy, 15 bytes/point, uint8 RGB) vs V2 (24 bytes/point, float32 RGB).
 *
 * Detection heuristic:
 *   - Read uint32 at offset 4. If value is 2 → V2.
 *   - If value is 1 → V1 (the uint16 reads as 1, with 2 bytes padding → uint32 also reads as 1).
 *   - If value > 2 → likely V1 with high point count packed differently; fallback to V1 and
 *     re-read header with uint16 version.
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

  // Auto-detect version: read as uint32 first
  const versionU32 = view.getUint32(4, true);

  if (versionU32 === VERSION_V2) {
    return decodeV2(view, buffer.byteLength);
  } else {
    // Try V1: legacy uses uint16 at offset 4 (reads as 1 in uint32 too,
    // since bytes 6-7 are zero padding)
    return decodeV1(view, buffer.byteLength);
  }
}

/** Decode V2 format (24 bytes/point, float32 RGB). */
function decodeV2(view: DataView, bufferLen: number): DMP1DecodeResult {
  const count = view.getUint32(8, true);
  const expectedSize = HEADER_SIZE + count * POINT_SIZE_V2;
  if (bufferLen < expectedSize) {
    throw new Error(`DMP1 V2: buffer too short (expected ${expectedSize}, got ${bufferLen})`);
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
    offset += POINT_SIZE_V2;
  }

  return { version: 2, voxels };
}

/** Decode V1 legacy format (15 bytes/point, uint8 RGB scaled to 0..1). */
function decodeV1(view: DataView, bufferLen: number): DMP1DecodeResult {
  // V1 header: 4 bytes magic + 2 bytes uint16 version + 2 bytes padding + 4 bytes uint32 count
  const version = view.getUint16(4, true);
  if (version !== VERSION_V1_LEGACY) {
    throw new Error(`DMP1: unsupported version ${version}`);
  }

  const count = view.getUint32(8, true);
  const expectedSize = HEADER_SIZE + count * POINT_SIZE_V1;
  if (bufferLen < expectedSize) {
    throw new Error(`DMP1 V1: buffer too short (expected ${expectedSize}, got ${bufferLen})`);
  }

  const voxels: VoxelEntry[] = [];
  let offset = HEADER_SIZE;
  for (let i = 0; i < count; i++) {
    voxels.push({
      x: view.getFloat32(offset, true),
      y: view.getFloat32(offset + 4, true),
      z: view.getFloat32(offset + 8, true),
      r: view.getUint8(offset + 12) / 255,
      g: view.getUint8(offset + 13) / 255,
      b: view.getUint8(offset + 14) / 255,
    });
    offset += POINT_SIZE_V1;
  }

  return { version: 1, voxels };
}

/**
 * Encode voxel entries into legacy V1 DMP1 binary buffer.
 * Used only for backward-compatibility export.
 */
export function encodeDMP1Legacy(voxels: readonly VoxelEntry[]): ArrayBuffer {
  const count = voxels.length;
  const buffer = new ArrayBuffer(HEADER_SIZE + count * POINT_SIZE_V1);
  const view = new DataView(buffer);

  // Header: magic
  for (let i = 0; i < 4; i++) {
    view.setUint8(i, MAGIC.charCodeAt(i));
  }
  // Header: version uint16 + 2 bytes padding
  view.setUint16(4, VERSION_V1_LEGACY, true);
  view.setUint8(6, 0);
  view.setUint8(7, 0);
  // Header: pointCount
  view.setUint32(8, count, true);

  // Points: 3 × float32 (xyz) + 3 × uint8 (rgb scaled from 0..1 to 0..255)
  let offset = HEADER_SIZE;
  for (const v of voxels) {
    view.setFloat32(offset, v.x, true); offset += 4;
    view.setFloat32(offset, v.y, true); offset += 4;
    view.setFloat32(offset, v.z, true); offset += 4;
    view.setUint8(offset, Math.round(Math.min(1, Math.max(0, v.r)) * 255)); offset += 1;
    view.setUint8(offset, Math.round(Math.min(1, Math.max(0, v.g)) * 255)); offset += 1;
    view.setUint8(offset, Math.round(Math.min(1, Math.max(0, v.b)) * 255)); offset += 1;
  }

  return buffer;
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
