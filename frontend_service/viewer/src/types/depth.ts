/**
 * depth.ts — Types for depth camera data and point cloud processing.
 */

// NOTE: RosPoint3 was previously imported but is not needed directly here.

/** Camera intrinsic parameters (pinhole model). */
export interface CameraIntrinsics {
  readonly fx: number;
  readonly fy: number;
  readonly cx: number;
  readonly cy: number;
  readonly width: number;
  readonly height: number;
}

/** Linear depth calibration: true_mm = k × raw_mm + b. */
export interface DepthCalibration {
  readonly k: number;
  readonly b: number;
}

/** Raw depth frame from the D435 endpoint. */
export interface DepthFrame {
  readonly raw: Uint16Array;
  readonly width: number;
  readonly height: number;
  readonly timestamp: number;
  readonly timestampSource: 'source' | 'local';
}

/** Raw RGB frame (RGB24 interleaved). */
export interface RgbFrame {
  readonly raw: Uint8Array;
  readonly width: number;
  readonly height: number;
}

/** Processed point cloud in camera-local frame (meters). */
export interface PointCloud {
  /** Interleaved XYZ positions in meters (length = count × 3). */
  readonly positions: Float32Array;
  /** Interleaved RGB colors, 0..1 (length = count × 3). */
  readonly colors: Float32Array;
  /** Number of valid points. */
  readonly count: number;
  /** Capture timestamp of source depth frame (ms epoch). */
  readonly timestampMs: number;
}

/** A single voxel entry in the accumulated world-frame map. */
export interface VoxelEntry {
  readonly x: number;  // world units (Three.js)
  readonly y: number;
  readonly z: number;
  readonly r: number;  // 0..1
  readonly g: number;
  readonly b: number;
  readonly mapId?: number;
}

export type FrameDropReason =
  | 'skew'
  | 'validation'
  | 'empty'
  | 'stale'
  | 'map_id_mismatch';

export interface DepthCameraRuntimeParams {
  readonly intrinsics: {
    readonly fx: number;
    readonly fy: number;
    readonly cx: number;
    readonly cy: number;
  };
  readonly depthScale: number;
  readonly depthCalibration: {
    readonly k: number;
    readonly b: number;
  };
  readonly frustum: {
    readonly near: number;
    readonly far: number;
  };
}

/** Depth overlay configuration (controls frame processing behavior). */
export interface DepthOverlayConfig {
  readonly stridePx: number;
  readonly minRaw: number;
  readonly maxRaw: number;
  readonly minDistanceM: number;
  readonly rejectNearBlackRgb?: boolean;
  readonly nearBlackThreshold?: number;
  readonly pixelRotationDeg: number;
  readonly flipX: boolean;
  readonly flipY: boolean;
  readonly swapPayloadWH: boolean;
  readonly cloudMirrorVertical: boolean;
  readonly cloudMirrorAxis: 'x' | 'y' | 'z';
  readonly cloudRotationDeg: { rx: number; ry: number; rz: number };
  readonly depthShotColor: readonly [number, number, number];
}

/** Voxel recording configuration. */
export interface VoxelRecordingConfig {
  readonly voxelSizeMm: number;
  readonly maxVoxels: number;
  readonly livePointSizeWu: number;
  readonly mapPointSizeWu: number;
  readonly mapOpacity: number;
}

/** Point cloud rendering configuration. */
export interface PointCloudRenderConfig {
  /** Screen-space point size in pixels (sizeAttenuation = false). */
  readonly pointSizePx: number;
  readonly opacity: number;
  readonly depthTest: boolean;
  readonly depthWrite: boolean;
}

/** Result of building a depth frame for rendering (Three.js world units). */
export interface DepthFrameResult {
  readonly positions: Float32Array;
  readonly colors: Float32Array;
  readonly count: number;
  readonly width: number;
  readonly height: number;
}
